# Memory growth, worker recycling, and the 529/530 problem — `max_requests` vs Kubernetes (one worker per pod)

> **Format note (matches this repo's other research):** factual lines carry an inline source `[S#]`,
> resolved in [Sources](#sources). Lines marked *(ours)* are our own synthesis or our **measured** results
> on this machine. Lines marked *(measured)* come from scripts in [`scripts/`](scripts/) that you can re-run.
>
> **The exact question this answers.** *"My LangGraph app with a PostgresSaver checkpointer (no cache) grows
> memory request after request, then the worker restarts hard and I get 529/530. I wanted to use the
> `max_requests` limit to release memory, but with **one worker per pod and two pods**, both workers shut
> and reboot at once → 529/530. (1) Does the memory really grow, and why? (2) How does `max_requests`
> actually release memory? (3) How do I get the same recycling on Kubernetes, even with one worker per pod,
> **without** taking both pods down together?"*
>
> **Environment for the measured parts:** Windows 11 · Python 3.13.12 · langgraph 1.2.4 ·
> langgraph-checkpoint 4.1.1 · langgraph-checkpoint-postgres 3.1.0 · psycopg 3.3.4 · PostgreSQL 18.4 ·
> FastAPI + Uvicorn. Gunicorn does **not** run on Windows (needs the Unix-only `fcntl`), so — exactly as you
> suggested — we drive the recycling with Uvicorn's `--limit-max-requests`, which is the same idea `[S3]`.
> **Verified / last run:** 2026-06-29.

---

## TL;DR (the five findings)

1. **`max_requests` does not "release" memory inside the process — it kills the process.** A worker that has
   served *N* requests is replaced by a brand-new worker; the OS reclaims **all** of the dead worker's memory
   because the process no longer exists. It bounds leaks; it does not garbage-collect them `[S1]` *(ours)*.
2. **In a controlled test, LangGraph + PostgresSaver does *not* leak the worker's memory.** Over **3,000**
   real `graph.invoke()`s against real Postgres, process RSS rose **+2.8 MB then plateaued**; the Python heap,
   thread count and live-object count were flat. Even the per-request "rebuild the graph every request"
   anti-pattern only added **+4.2 MB** and plateaued *(measured — §1)*. This matches LangChain's own docs:
   *checkpoints are not held in memory; pod-memory growth is almost always your own code* `[S5]`.
3. **The hard, ungraceful restart you see is almost certainly an OOMKill, not `max_requests`.** On Kubernetes,
   memory is *non-compressible*: a container over its limit is killed with SIGKILL, dropping every in-flight
   request `[S7]`. `max_requests` is the *opposite* of that — a *graceful, proactive* recycle meant to stop you
   ever reaching the OOM cliff *(ours)*.
4. **Both pods restart together because the recycle is *synchronized*, and Uvicorn makes it worse.**
   Gunicorn has `--max-requests-jitter` to de-synchronize restarts; **Uvicorn's `--limit-max-requests` has
   no jitter flag at all** `[S3]` *(measured: `uvicorn --help`)*. With even load + one worker per pod + no
   jitter, every worker hits *N* at the same moment and recycles together → a window with **no ready
   backend** → the load balancer returns 529/530 *(measured — §3)*.
5. **You *can* get `max_requests`-grade recycling on Kubernetes with one worker per pod — by breaking the
   synchronization.** The levers, in order: **jitter the recycle**, **run ≥3 replicas**, **gate traffic with a
   readiness probe**, **boot fast**, and **size the memory limit** so recycling (not the OOM killer) wins.
   For a runtime with no recycle feature at all, a **`CronJob` → `kubectl rollout restart`** is the
   Kubernetes-native, zero-downtime equivalent `[S10]` *(ours — §4)*.

---

## 0. First, what 529 and 530 actually are (and which restart you're seeing)

Neither code is an IETF standard; both are vendor conventions, so the *exact* emitter matters `[S12]`.

| Code | Common meaning | Who emits it | What it tells you here |
|---|---|---|---|
| **529** | "Site/service is **overloaded**" — aggregate demand exceeded capacity (≠ 429, which is per-client) | Qualys / SSL Labs, Pantheon, some gateways `[S12]` | Your gateway had **no healthy/ready backend** (or too few) to take the request |
| **530** | Cloudflare: origin error (usually shown with a `1xxx`); Pantheon: "site frozen" `[S12]` | Cloudflare, Pantheon | The proxy **could not reach a working origin** behind it |

> *(ours)* Both codes, in your topology, point at the **same root event**: for a brief window there was **no
> pod ready to serve**, so the ingress/load balancer had nowhere to send the request. That is exactly what
> happens when *all* of your single-worker pods recycle at the same instant. Tellingly, the Qualys SSL Labs
> API — the canonical 529 emitter — tells *clients* receiving a 529 to **back off 15–30 minutes and randomize
> the delay** `[S13]`: randomization to avoid a synchronized retry stampede is the very same fix you need on
> the server side (jitter, §3–§4).

**Two different "restarts" are being conflated — separate them:**

- **OOMKill (ungraceful, SIGKILL).** Memory climbs past the container's `limits.memory`; the kernel OOM-killer
  terminates the process instantly, mid-request `[S7]`. *This* is the "not a graceful restart" you described.
- **`max_requests` recycle (graceful, proactive).** After *N* requests the manager **gracefully** stops the
  worker (lets it finish, within `graceful_timeout`) and starts a fresh one `[S1]`. The goal of turning this
  on is to **never reach** the OOM cliff above.

So your instinct — "introduce `max_requests` to release memory" — is the right *direction*. The problem is
purely that the recycles are **synchronized** across your two pods. The rest of this doc proves the memory
behaviour, explains the mechanism, and fixes the synchronization.

---

## 1. Does LangGraph + PostgresSaver actually leak? (measured)

**Probe:** [`scripts/langgraph_memory_probe.py`](scripts/langgraph_memory_probe.py) drives a real
`StateGraph` compiled with a real `PostgresSaver` against real Postgres, sampling **process RSS**, the
**Python heap** (`tracemalloc`), **thread count** and **live-object count** every 100 requests. It separates
the three things people lump together as "a leak":

- **true leak** → RSS climbs and never comes back, even after `gc.collect()`; the *live heap* also grows.
- **cyclic garbage** → RSS climbs but `gc.collect()` reclaims it.
- **fragmentation / RSS drift** → the heap is flat but RSS drifts up a little and **plateaus** (CPython keeps
  freed arenas mapped) `[S15]`.

### 1.1 Result — it plateaus; it does not leak

Two configs, fresh `thread_id` per request (a busy API serving many short conversations):

| Config | Requests | RSS start → end | Growth | Heap growth | Threads | Live-object Δ | Verdict |
|---|---:|---|---:|---:|---:|---:|---|
| **shared-pool** (compile once, one shared `ConnectionPool`) | 3,000 | 85.9 → **88.7 MB** | +2.8 MB | +0.07 MB | 5 → 5 | +10 | **STABLE** |
| **per-request** (new graph + new connection every request — an anti-pattern) | 2,000 | 85.2 → **89.4 MB** | +4.2 MB | +0.10 MB | 1 → 1 | +7 | **STABLE** |

The shared-pool series **plateaus** almost immediately and stays there *(measured)*:

```
req     1: rss 85.9MB  heap 0.20MB  threads 5  objs 99535
req   700: rss 87.8MB  heap 0.26MB  threads 5  objs 99545
req  1500: rss 88.5MB  heap 0.26MB  threads 5  objs 99545
req  2300: rss 88.7MB  heap 0.27MB  threads 5  objs 99545
req  3000: rss 88.7MB  heap 0.27MB  threads 5  objs 99545   ← flat: warm-up, not a leak
```

> *(ours)* **Conclusion for Part 1:** the LangGraph runtime + the PostgresSaver checkpointer, used correctly,
> did **not** grow the worker's memory without bound — RSS rose a few MB once (allocator warm-up) and then sat
> flat across thousands of requests. This is exactly what LangChain documents: *"Checkpoints are never stored
> in memory … pod memory usage comes from code running inside the workflow … leaks must be fixed in code"*
> `[S5]`. **Important scope:** our graph nodes are pure Python (no LLM/tool/retriever calls), so this isolates
> the *framework + saver*. It does **not** prove *your* graph is leak-free — it proves the leak, if you have
> one, is overwhelmingly likely to be in the layers we deliberately left out (next).

### 1.2 Where the growth you're seeing most likely comes from (ranked)

Given the framework itself is clean, look here, in order `[S5]` *(ours)*:

1. **Your node code holding references.** Module-level lists/dicts/`@lru_cache(maxsize=None)` that accumulate;
   appending to a global; caching embeddings/messages "just for this process." You said "no cache" — confirm
   there is no *implicit* one (an unbounded `lru_cache`, a module-level dict, a retriever/vectorstore client
   buffering, an HTTP/DB client keeping response objects).
2. **Connection handling.** Using a *raw* `PostgresSaver.from_conn_string` connection held for a run, or
   creating a saver/pool **per request**, churns connections and prepared statements. Use **one shared
   `ConnectionPool`** for the whole process, with `autocommit=True` and `prepare_threshold=0` (the latter also
   stops psycopg's prepared-statement cache from growing) `[S5][S6]`.
3. **Unbounded conversation state.** Reusing one `thread_id` and letting `messages` grow forever makes each
   superstep's checkpoint bigger — that bloats the *database* and the per-request working set `[S5]`.
4. **Bounded-but-real thread pool.** LangGraph uses a `ThreadPoolExecutor` (up to ~32 threads) for JSON
   serialization; those threads sit idle after runs. They're **not** a leak but they add a fixed RSS floor —
   inspect with `py-spy` if thread count climbs `[S5]`. *(In our run, threads stayed flat at 1–5.)*
5. **CPython fragmentation (looks like a slow leak, isn't).** Even with no leak, RSS drifts up and **plateaus**
   because the allocator doesn't always return freed arenas to the OS `[S15]`. This is the one case where
   recycling genuinely helps and GC never will — and it's small (a few MB here).

**Measure your real app the same way** (this is the "snapshot" you asked for):

```bash
# point at YOUR Postgres and run YOUR graph through the probe pattern:
set LG_PG_URI=postgresql://user:pass@host:5432/yourdb        # PowerShell: $env:LG_PG_URI="..."
python research/scripts/langgraph_memory_probe.py --config shared-pool --requests 5000
# -> research/data/langgraph_mem__shared-pool.json  (RSS/heap/threads/objects every 100 reqs + a verdict)
```

If your real graph's `rss_growth` keeps rising past the plateau while `heap_growth` rises too → you have a
**true leak in your code** (profile with `tracemalloc`/`py-spy`). If RSS drifts then flattens while the heap
stays flat → it's **fragmentation**, and *that* is the case `max_requests`-style recycling is for *(ours)*.

---

## 2. How `max_requests` actually releases memory (the mechanism, in full)

### 2.1 The process model

Gunicorn is **pre-fork**: a **master (arbiter)** process binds the socket and forks *N* **worker** processes;
the master never serves requests — it only supervises workers `[S2]`. `max_requests = N` tells the master:
*after a worker has handled N requests, retire it and fork a replacement* `[S1]`. Crucially:

- Memory isn't "freed" by some cleanup routine. The worker **process exits**, and the kernel reclaims *its
  entire address space* — heap, fragmented arenas, leaked objects, cached buffers, everything. The replacement
  is a **fresh fork** that starts at the baseline RSS *(ours, from the OS process model)*.
- The master **keeps the listening socket open the whole time**, so the recycle is graceful: the dying worker
  finishes in-flight requests (up to `graceful_timeout`), and the new worker is forked to take its place `[S1][S2]`.
- That's why it's described in Gunicorn's own docs as *"a simple method to help limit the damage of memory
  leaks"* `[S1]` — **limit the damage**, not fix the leak. The maintainer's intent is a *workaround*; the real
  fix is the leak itself (§1.2).

```
  master (holds the socket, never dies on max_requests)
    ├── worker A  ──serves──▶ hits N ──▶ graceful stop ──▶ exits ──▶ OS reclaims ALL its RAM
    │                                         │
    │                                         └──▶ master forks a FRESH worker A'  (RSS back to baseline)
    └── worker B  ── … independent count …
```

### 2.2 `max_requests_jitter` — the part that exists *specifically* for your bug

If every worker recycles at *exactly* N, workers that booted together hit N together and **all restart at the
same moment → a brief outage**. `max_requests_jitter = J` adds a random `0..J` to **each worker's** limit, so
they **stagger** `[S1]`. This is not a nicety — it is the canonical fix for the precise symptom you reported
("both shut at once"). Typical: `--max-requests 2000 --max-requests-jitter 200` `[S1]`.

### 2.3 The Uvicorn equivalent you asked to test — and its two sharp edges

Uvicorn's `--limit-max-requests` is the same idea: *"Maximum number of requests to service before terminating
the process."* `[S3]` We tested it on this machine and found **two gotchas that bear directly on your setup**:

**Edge 1 — Uvicorn has _no jitter flag._** `uvicorn --help` lists `--limit-max-requests` but **no**
`--limit-max-requests-jitter` (that companion is **Gunicorn-only**) *(measured)*. So if your pods run
`uvicorn --limit-max-requests`, **every worker recycles at exactly N with zero jitter** — synchronization is
*guaranteed*. This alone is a strong candidate for your 529/530.

**Edge 2 — a _lone_ Uvicorn worker doesn't respawn; the whole server exits.** Measured:

```
uvicorn recycle_app:app --workers 1 --limit-max-requests 15
  → WARNING: Maximum request limit of 15 exceeded. Terminating process.
  → INFO:    Shutting down ...  Finished server process       ← the server is GONE, nothing respawns
  → load driver: 14 ok, then 36/36 requests FAIL (total outage)
```

```
uvicorn recycle_app:app --workers 2 --limit-max-requests 40
  → 4 distinct worker PIDs appear over the run  ← the supervisor DID respawn each worker (2 recycles)
  → load driver: 129/130 ok                     ← recycling works when a supervisor survives the worker
```

> *(ours)* **Why the difference matters for "one worker per pod":** recycling only works if **something that
> outlives the worker respawns it**. With `--workers ≥ 2`, Uvicorn's multiprocess supervisor does that. With a
> **single** Uvicorn worker there is no surviving supervisor, so `--limit-max-requests` is effectively a
> *self-destruct*. **Gunicorn is different:** its **master always survives**, so even `gunicorn -w 1
> --max-requests N` recycles its lone worker gracefully (a brief per-pod gap during the re-fork). So:
> - **One Gunicorn worker per pod** → recycles fine *per pod* (master respawns); your only problem is
>   cross-pod synchronization → fix with `--max-requests-jitter` (§3–§4).
> - **One Uvicorn worker per pod with `--limit-max-requests`** → the worker exits with nothing to respawn it;
>   you are relying on **Kubernetes** to restart the pod, which is slower and shows as a 529/530 window. Either
>   add a surviving supervisor (run Gunicorn, or `uvicorn --workers ≥2`), or recycle at the **cluster** level
>   (§4).

---

## 3. Why "1 worker/pod × 2 pods" takes both down at once (measured A/B)

**Harness:** [`scripts/recycle_app.py`](scripts/recycle_app.py) (a worker that leaks a *labelled, synthetic*
amount per request so the sawtooth is visible, and models a **heavy app's slow boot** via a startup delay —
the LangGraph analog of re-importing + re-running `checkpointer.setup()` on every respawn) +
[`scripts/run_recycle_experiment.py`](scripts/run_recycle_experiment.py) (boots it four ways and drives the
same load at each). Two Uvicorn workers behind one supervisor = a faithful local stand-in for **two
single-worker pods behind one Service**.

**Result** — 400 requests, concurrency 2, ~1.2 s modeled boot, 2 workers (except where noted) *(measured)*:

| Scenario | ok % | failed | recycles | p50 | p95 | **p99** | max | stalled >1s | RSS sawtooth |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **baseline-norecycle** (no recycling) | 100.0 | 0 | 0 | 2.7 | 17.6 | 20.3 | 26 ms | 0 | 54 → **62.5 MB, still climbing** |
| **single-worker-death** (1 worker + `--limit-max-requests 120`) | **31.5** | **274** | 0 | 3.3 | 16.1 | 18.0 | 26 ms | 0 | 50 → 60 MB then **server gone** |
| **sync-recycle** (2 workers, `--limit-max-requests 60`, no jitter) | 98.5 | 6 | 4 | 4.1 | 24.3 | **1515 ms** | **4956 ms** | 6 | 54 → 57 MB (recycled ✔) |
| **jitter-recycle** (2 workers, randomized self-recycle) | 99.5 | 2 | 4 | 5.1 | 16.9 | **27.9 ms** | 1965 ms | 4 | 54 → 59 MB (recycled ✔) |

**The three things this proves at once** *(measured)*:

- **No recycling → memory just climbs** (54 → 62.5 MB and rising in 400 requests). Latency is fine *until* the
  pod hits `limits.memory` and gets OOMKilled — the ungraceful restart you're seeing.
- **One worker + a request limit → self-destruct.** Only **31.5%** of requests succeeded; after the lone
  worker hit its limit the whole server exited with nothing to respawn it (§2.3).
- **Synchronized vs jittered recycling is the entire 529/530 story.** Both did the **same 4 recycles** and
  kept memory flat, but synchronized recycling drove **p99 to 1,515 ms (max ~5 s)** while jitter held **p99 at
  27.9 ms** — a **~54× worse tail** from a single variable. On a real cluster, those multi-second stalls are
  precisely the window where the ingress has no ready endpoint and returns **529/530**. *(Jitter still shows
  one ~2 s `max` from an early-generation overlap — which is why §4 pairs jitter with **≥3 replicas +
  readiness gating** rather than relying on jitter alone.)*

> *(ours)* **Reading the A/B:** with **synchronized** recycling (one fixed limit, no jitter) both workers hit
> the limit within a few requests of each other; because a heavy worker takes real time to boot, there is a
> window where **neither** worker is ready, and requests **pile up waiting** — the latency tail explodes and,
> on a real cluster where readiness removes the endpoint, those waiting requests become **529/530** instead of
> slow. With **jittered** recycling (randomized per-worker target) the two workers recycle at *different*
> times, so the other worker is always serving → the stall window disappears. Same leak, same boot cost, one
> variable changed: **synchronization**.

The mapping to your cluster is exact: *(ours)*

| Local experiment | Your Kubernetes setup |
|---|---|
| 2 Uvicorn workers under one supervisor | 2 pods (1 worker each) behind one Service |
| even round-robin from the driver | even load-balancing by kube-proxy/ingress |
| heavy-boot delay on respawn | LangGraph import + `checkpointer.setup()` on pod (re)start |
| both workers down together → requests stall/fail | both pods NotReady together → ingress has no endpoint → **529/530** |
| jittered self-recycle → other worker serves | jittered/staggered recycle (or ≥3 replicas) → some pod always Ready |

---

## 4. Getting `max_requests`-grade recycling on Kubernetes — with one worker per pod

You can absolutely keep recycling for memory hygiene with one worker per pod. The job is to make sure **never
more than a fraction of replicas recycle at once**, and that **traffic is steered away from a recycling pod**.
Here is the menu, cheapest first. *(ours, grounded in the cited Kubernetes/Gunicorn docs.)*

> **If you want to stay on a pure single Uvicorn worker per pod (no Gunicorn, no `--workers ≥2`)** — i.e. let
> **Kubernetes** be the process manager — read the dedicated companion:
> [**recycling-one-uvicorn-worker-per-pod-on-kubernetes.md**](recycling-one-uvicorn-worker-per-pod-on-kubernetes.md).
> It maps every Gunicorn knob to its cluster-level equivalent and explains the **CrashLoopBackOff** trap that
> makes a scheduled `kubectl rollout restart` better than a self-exiting `--limit-max-requests`. The options
> below that mention Gunicorn / `--workers ≥2` are the *in-pod-supervisor* alternative, which trades away the
> one-process-per-pod purity.

### 4.1 In-pod recycle, de-synchronized (keep doing what you're doing — correctly)

- **If you run Gunicorn in the pod:** keep `--max-requests` **and add a real `--max-requests-jitter`** (e.g.
  `--max-requests 2000 --max-requests-jitter 600`). Each pod independently randomizes, so two pods desync `[S1]`.
  The repo's manifest already does this — see [`manifests/k8s-gunicorn-4workers.yaml`](manifests/k8s-gunicorn-4workers.yaml).
- **If you run Uvicorn in the pod:** there is **no jitter flag** `[S3]`. Options: (a) run **`gunicorn -k
  uvicorn_worker.UvicornWorker`** so you get jitter + a surviving master; (b) run **`uvicorn --workers 2`** so
  a supervisor respawns workers (and stagger via app logic); or (c) add **app-level jitter** — each worker
  picks a randomized recycle target at boot and self-exits (our [`recycle_app.py`](scripts/recycle_app.py)
  shows this; the supervisor respawns it). *(measured: app self-exit is respawned under `--workers 2`.)*
- **Always run ≥ 3 replicas.** With one worker per pod, losing one pod for a second is `1/replicas` of
  capacity. At 2 replicas a single recycle halves capacity; at 3–4 it's a 25–33% blip the others absorb `[S9]` *(ours)*.

### 4.2 Steer traffic off a recycling pod (so a recycle is invisible)

- **Readiness probe.** Kubernetes removes a **NotReady** pod from the Service endpoints, so no traffic is sent
  while it (re)boots `[S8]`. Make `/ready` reflect "a worker is actually up." This is what converts a recycle
  from a 529 into a no-op.
- **Startup probe** for slow LangGraph boots, so a long first import doesn't trip liveness `[S8]`.
- **`preStop` sleep + `terminationGracePeriodSeconds`.** A few seconds of `preStop` lets the Service stop
  routing *before* SIGTERM, and the grace period lets in-flight requests finish — graceful drain `[S8]` *(ours)*.

### 4.3 Make the OOMKill impossible *and* recycling win

- **Set `requests.memory` and `limits.memory`** from the measured plateau (§1) **plus headroom** for the
  sawtooth peak. Memory is non-compressible: over the limit = SIGKILL `[S7]`. You want the **recycle** to fire
  *below* the limit so it, not the kernel, reclaims memory *(ours)*.
- **Recycle threshold < memory limit.** Pick `max_requests` (or a soft RSS check) so a worker recycles while
  still comfortably under `limits.memory`. If recycling already keeps RSS flat (our §1 plateau), the limit is
  just a backstop.

### 4.4 When the runtime has *no* recycle feature — the Kubernetes-native equivalent

There is **no built-in "restart after N requests"** in Kubernetes (HPA scales replica count; probes restart on
health, not on a request counter). The idiomatic substitute is a **scheduled rolling restart** `[S10]` *(ours)*:

```yaml
# A CronJob that runs `kubectl rollout restart` — the cluster-level "max_requests".
apiVersion: batch/v1
kind: CronJob
metadata: { name: recycle-myapp, namespace: myns }
spec:
  schedule: "17 */6 * * *"          # every 6h, off the :00 mark so it doesn't herd with other jobs
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: deployment-restart      # needs RBAC: get/patch/list/watch on deployments
          restartPolicy: Never
          containers:
            - name: kubectl
              image: bitnami/kubectl
              command: ["kubectl","rollout","restart","deployment/myapp"]
```

`kubectl rollout restart` does a **rolling** replacement that respects `maxUnavailable`/`maxSurge`, readiness,
and your PodDisruptionBudget — so it drains and recycles pods **gradually, with zero downtime** `[S10][S9][S11]`.
Pair it with a `Deployment` strategy of `maxUnavailable: 0, maxSurge: 1` (never drop below desired capacity)
and a PDB (`minAvailable: 50%`) so node drains never take all pods at once `[S9][S11]`. This recycles leaked
memory on a clock instead of a request count — the practical equivalent for a leaky runtime *(ours)*.

> *(ours)* A memory-triggered variant: expose a `/ready` that returns 503 once this worker's RSS crosses a
> **soft** threshold (well under `limits.memory`). Kubernetes drains it; a `preStop`/liveness then recycles it
> gracefully — *before* the OOM killer would. Keep **liveness** conservative (don't let "busy" look like
> "dead") `[S8]`; tie the soft-threshold recycle to **readiness** + a controlled restart, not to a liveness
> kill, so you never get the ungraceful SIGKILL you started with.

---

## 5. Concrete recommendation for your setup

*(ours — assuming one worker per pod, two pods today, a LangGraph + PostgresSaver API)*

1. **Confirm the diagnosis first.** Run §1's probe against your real graph for 5–10k requests. If RSS
   plateaus, you don't have a leak — you have **fragmentation + an OOM limit set too low**; fix with a slightly
   larger `limits.memory` + light recycling. If RSS climbs without plateauing, **fix the leak** (§1.2) — that
   is the real cure; recycling only buys time `[S1][S5]`.
2. **Go from 2 → 3 (ideally 4) replicas.** This is the single biggest lever against 529/530: one recycling pod
   is then ≤ 33% of capacity, not 50% `[S9]`.
3. **De-synchronize the recycle.**
   - Gunicorn in-pod: `--max-requests 2000 --max-requests-jitter 600` (jitter ≥ ~25–30% of base) `[S1]`.
   - Uvicorn in-pod: switch to `gunicorn -k uvicorn_worker.UvicornWorker` (gets jitter **and** a surviving
     master that respawns a single worker), or add app-level jitter (§4.1) `[S3]`.
4. **Add/repair the readiness probe** so a recycling pod leaves the Service rotation `[S8]`, plus a startup
   probe for slow boots and a `preStop` sleep for clean drain `[S8]`.
5. **Right-size memory:** `requests.memory` ≈ measured plateau; `limits.memory` ≈ plateau + sawtooth headroom,
   so the **recycle** fires under the limit and the OOM killer never does `[S7]`.
6. **If your runtime can't recycle itself,** add the §4.4 `CronJob → kubectl rollout restart` as the
   cluster-level `max_requests` `[S10]`.

Net: **yes — you can have `max_requests`-style recycling on Kubernetes with one worker per pod.** The trick is
never letting all the single-worker pods recycle at the same instant: **jitter + enough replicas + readiness
gating**, with a scheduled `rollout restart` as the native fallback.

---

## 6. Reproduce everything

```bash
# --- Part 1: does LangGraph + PostgresSaver leak? (needs a Postgres) ---
set LG_PG_URI=postgresql://postgres:password@127.0.0.1:5432/lg_memtest     # your URI
python research/scripts/langgraph_memory_probe.py --config shared-pool  --requests 3000
python research/scripts/langgraph_memory_probe.py --config per-request  --requests 2000
#   -> research/data/langgraph_mem__*.json  (RSS/heap/threads/objects + verdict)

# --- Parts 2-3: the recycle mechanism + the synchronized-restart 529/530 (no Postgres needed) ---
python research/scripts/run_recycle_experiment.py
#   -> research/data/recycle_experiment.json  + per-scenario recycle__*.json
#   boots recycle_app.py four ways (no-recycle / 1-worker-death / sync / jitter) and drives load at each

# drive any running server yourself:
python research/scripts/recycle_loadtest.py --url http://127.0.0.1:8020 --requests 400 --concurrency 2 --label mine
```

The probe and the experiment run on any machine with the listed packages; only Part 1 needs a reachable
Postgres. Gunicorn-exact numbers require Linux — the Uvicorn `--limit-max-requests` runs here are the same
mechanism `[S3]`.

---

## Sources

| Tag | Source | Type | Link |
|---|---|---|---|
| **S1** | Gunicorn — *Settings* (`max_requests`, `max_requests_jitter`, `timeout`, `graceful_timeout`; "limit the damage of memory leaks") | Official docs | https://docs.gunicorn.org/en/stable/settings.html |
| **S2** | Gunicorn — *Design* (pre-fork master/arbiter; the master supervises workers and holds the socket) | Official docs | https://docs.gunicorn.org/en/stable/design.html |
| **S3** | Uvicorn — *Settings / Deployment* (`--limit-max-requests`: "terminating the process"; no jitter flag exists) | Official docs | https://www.uvicorn.org/settings/ |
| **S4** | FastAPI — *FastAPI in Containers* (Uvicorn historically couldn't restart dead workers; one process per container on K8s) | Official docs | https://fastapi.tiangolo.com/deployment/docker/ |
| **S5** | LangChain Support — *Understanding Checkpointers, Databases, API Memory & TTL* (checkpoints not held in memory; pod-memory growth is your code; ThreadPoolExecutor ≤32 threads; use a shared pool) | Official support | https://support.langchain.com/articles/6253531756-understanding-checkpointers-databases-api-memory-and-ttl |
| **S6** | LangGraph — *Add memory / persistence* (PostgresSaver on psycopg3; prefer a `ConnectionPool`; `autocommit`, `prepare_threshold=0`) | Official docs | https://docs.langchain.com/oss/python/langgraph/add-memory |
| **S7** | Kubernetes — *Resource Management for Pods and Containers* (memory non-compressible → over-limit = OOMKilled) | Official docs | https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/ |
| **S8** | Kubernetes — *Configure Liveness, Readiness & Startup Probes* (readiness gates Service endpoints; startup gating; keep liveness conservative) | Official docs | https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/ |
| **S9** | Kubernetes — *Deployments / Rolling Update* (`maxUnavailable`, `maxSurge`) | Official docs | https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#rolling-update-deployment |
| **S10** | Kubernetes — *kubectl rollout restart* (rolling, respects readiness/maxUnavailable; the cluster-level recycle) | Official docs | https://kubernetes.io/docs/reference/kubectl/generated/kubectl_rollout/kubectl_rollout_restart/ |
| **S11** | Kubernetes — *Disruptions / PodDisruptionBudget* (bound how many pods are down at once) | Official docs | https://kubernetes.io/docs/concepts/workloads/pods/disruptions/ |
| **S12** | Wikipedia — *List of HTTP status codes* (529 used by Qualys/SSL Labs & Pantheon = "overloaded"; 530 by Cloudflare/Pantheon; all non-standard) | Reference | https://en.wikipedia.org/wiki/List_of_HTTP_status_codes |
| **S13** | Qualys SSL Labs — *API v3 docs* (on HTTP 529 "service overloaded": sleep 15–30 min and **randomize** before retry) | Maintainer docs | https://github.com/ssllabs/ssllabs-scan/blob/master/ssllabs-api-docs-v3.md |
| **S15** | Python — *Memory Management* (pymalloc arenas; freed memory is not always returned to the OS → RSS drift/plateau) | Official docs | https://docs.python.org/3/c-api/memory.html |

> Companion docs in this folder: [why in-pod Gunicorn workers are discouraged on
> K8s](why-not-gunicorn-workers-on-kubernetes.md) · the [N pods × 4 workers latency research](README.md) ·
> the [slim-vs-fat 10k experiment](slim-vs-fat-10k-experiment.md). Repo reference: [FINAL_CONFLUENCE_PAGE
> §14](../FINAL_CONFLUENCE_PAGE.md#14-kubernetes-on-powerful-multi-core-nodes-pods-vs-workers).
