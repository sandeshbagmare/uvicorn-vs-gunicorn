# The Final Word — Deploying FastAPI on Kubernetes (and what we learned)

> **One page. The whole conclusion.** If you read nothing else in this repo, read this.
> It assumes your **primary deployment target is Kubernetes**, folds in every finding from our
> benchmarks and deep dives, and answers the question that trips up almost everyone:
>
> *"My nodes aren't single-core — they're 4- or 8-core machines. If I run one worker per pod,
> won't each worker leave most of the machine's resources unused? Increasing workers clearly
> increases CPU utilisation — so am I wasting the box?"*
>
> Short answer: **No — as long as you run one worker per *pod*, not one worker per *node*, and
> right-size each pod to ~1 core.** The node fills up with *many* small pods, and Kubernetes keeps
> every core busy. The detail below is the part that matters.

---

## 1. The deployment reality we're optimising for

Kubernetes is the manager. It already does the things Gunicorn was invented to do — restart crashed
units, roll out new versions with zero downtime, scale capacity up and down, health-check, and
reschedule. So on Kubernetes the default is **plain Uvicorn, one worker per container**, and you scale
with **replicas + the Horizontal Pod Autoscaler (HPA)**. This is also FastAPI's own official guidance:
*don't use `--workers` in Kubernetes; run a single Uvicorn process per container.*

That advice is correct — but it is constantly **misread**, and the misreading is the source of your
worry. So let's kill the misreading first.

---

## 2. The worry, answered: "one worker per pod" ≠ "one worker per node"

Here is the mistake people picture when they hear "one worker per container":

```
❌ WHAT PEOPLE FEAR (and should NOT do):
8-core node
└─ ONE pod ─ ONE Uvicorn worker ─ allocated the whole 8-core machine
     → the GIL caps that single process at ~1 core of Python work
     → 7 of 8 cores sit idle. THIS is the underutilisation you're worried about. It is real.
```

And here is what "one worker per container" actually means in practice:

```
✅ WHAT YOU ACTUALLY DO:
8-core node
├─ Pod 1 ─ 1 Uvicorn worker ─ requests ~1 core
├─ Pod 2 ─ 1 Uvicorn worker ─ requests ~1 core
├─ Pod 3 ─ 1 Uvicorn worker ─ requests ~1 core
├─ Pod 4 ─ 1 Uvicorn worker ─ requests ~1 core
├─ Pod 5 ─ 1 Uvicorn worker ─ requests ~1 core
├─ Pod 6 ─ 1 Uvicorn worker ─ requests ~1 core
└─ Pod 7 ─ 1 Uvicorn worker ─ requests ~1 core   (≈1 core left for kubelet/system/DaemonSets)
     → 7 independent Python processes = 7 cores busy = the node is FULLY utilised
     → and Kubernetes controls each of those 7 units independently
```

**The node is filled by the number of *pods*, not by the number of *workers inside one pod*.**
You were absolutely right that *increasing the worker count increases CPU utilisation* — that is exactly
how you fill an 8-core box. The only question this repo answers is **how you package those extra
processes**: as more **pods** (the Kubernetes-native way) or as more **workers inside one pod** (the
Gunicorn way). On Kubernetes, more pods wins by default.

> **So where does underutilisation actually come from?** Two places, both about *sizing*, not philosophy:
> 1. **A fat allocation behind a single worker** — giving a 1-worker pod 4 cores of resources. The GIL
>    means that worker can only use ~1 core for Python, so 3 cores are stranded. *Fix: size each
>    thin pod to ~1 core.*
> 2. **Over-large CPU requests** — if `requests.cpu` is bigger than a pod really needs, the scheduler
>    packs fewer pods onto the node and the rest sits idle. *Fix: right-size requests to measured usage.*
>
> Neither is caused by "one worker per pod." Both are caused by mis-sizing.

---

## 3. The real decision is packaging, and the GIL sets the rule

Everything reduces to one hardware fact: **one Python process uses one core at a time (the GIL).**
To use all 8 cores you need 8 Python processes. Full stop. Your three legal packagings:

| Packaging | On an 8-core node | Who supervises the workers | Best when |
|---|---|---|---|
| **Thin pods** (1 worker/pod) | ~7 pods × 1 worker | **Kubernetes** (each worker is a pod) | **Default.** Stateless/modest-memory services; you want granular K8s control |
| **Hybrid pods** (2 workers/pod) | ~3–4 pods × 2 workers | Uvicorn (in-pod) + K8s (pods) | Want a little memory sharing / fewer pods, still good granularity |
| **Fat pods** (N workers/pod) | 1–2 pods × 4 workers | **Gunicorn** (in-pod) + K8s (pods) | Large shared in-memory model; heavy per-pod sidecar to amortise |

All three can fully utilise the node. They differ in **how much control Kubernetes has** and **how much
memory you save**. That trade-off — not raw speed — is the whole game (the benchmarks confirm raw speed
is the same Uvicorn underneath either way).

---

## 4. The right-sizing rule (so nothing is stranded)

A practical recipe for your 4-/8-core nodes:

1. **Pick the pod's worker count from its CPU allocation, 1:1.** A pod that requests ~1 core → 1 worker.
   A pod that requests ~4 cores → 4 workers (because 4 workers need ~4 cores; 1 worker would waste 3).
   *Never give a single worker a multi-core allocation* — that is the stranded-resource trap.
2. **Set `requests` to real measured usage** (CPU + memory) so the scheduler bin-packs pods until the
   node is full. Under-set and you risk noisy neighbours; over-set and you strand capacity.
3. **Set a memory limit** (a leaking pod should be OOM-killed). **Think hard before a CPU limit** —
   on multi-process/latency-sensitive pods a CPU limit causes CFS throttling (bursty p99 spikes) even
   when the node has spare cores. Many teams set CPU *requests* only.
4. **Let the HPA add/remove pods** on CPU utilisation (or a custom metric like RPS / in-flight requests).
   With thin pods, scaling is smooth — one small unit at a time — and can spill onto more nodes under load.
5. **Spread for HA** (topology spread / anti-affinity) and protect drains with a PodDisruptionBudget.

**Worked example (8-core node, thin):** reserve ~1 core for kubelet + CNI + logging/metrics DaemonSets
(+ any service-mesh sidecar) → ~7 cores for app → **7 pods, `requests.cpu: ~900m`, 1 worker each**.
Node fully used; 7 units Kubernetes manages independently; HPA grows the replica count past 7 onto more nodes.

---

## 5. When to put several workers in one pod (and use Gunicorn)

Thin pods are the default, but a few-workers-per-pod layout is the *right* call when:

- **You load a large read-only asset per process** (an ML model, a big cache, a 1–2 GB table).
  8 thin pods = 8 full copies = brutal RAM. One pod with **Gunicorn `--preload`** loads it **once** and
  forks workers that **share** those read-only pages via copy-on-write — often cutting memory several-fold.
  *(Caveat: Python's refcounting/GC writes to objects and erodes that sharing over time. This is a real,
  studied effect — Instagram famously fought it and upstreamed `gc.freeze()` to preserve CoW. If memory
  sharing is your reason for fat pods, plan for it.)*
- **A per-pod service-mesh sidecar** (e.g. Istio's Envoy) is a meaningful slice of overhead, and you'd
  rather amortise one sidecar across several workers than pay it per thin pod.
- **Very large clusters** where the sheer pod/IP count strains the control plane or the per-node pod cap.

**If you go fat, then yes — use "Gunicorn + Uvicorn workers," not bare `uvicorn --workers`** — precisely
because you now *want* a real in-pod supervisor: `--timeout` to kill a hung worker (Kubernetes can't see
inside the pod to do this), `--max-requests` to recycle workers and bound leaks, and `--preload` for the
memory sharing above. Still keep **several pods** (don't collapse to one node-filling pod) so you keep
cross-node HA and rolling-deploy granularity.

> **The crisp answer to your question:** on Kubernetes, the powerful 8-core node does **not** push you to
> "Gunicorn with N workers per node." Fill the node with **many right-sized pods** (thin, or 2-worker
> hybrid) and let the **HPA** spread them. Reach for **Gunicorn-with-workers inside a pod only when a
> large shared model or sidecar overhead makes denser pods worth it** — and even then, run more than one.

---

## 6. Windows — what changes, and why

Most teams develop on Windows and deploy on Linux, so it's worth being explicit:

- **Gunicorn does not run on Windows at all.** It imports the Unix-only `fcntl` module and uses
  `os.fork()`; on Windows you get `ModuleNotFoundError: No module named 'fcntl'`. There is no flag to fix
  this — it's a fundamental OS gap. On Windows you use **Uvicorn only** (`--workers` works), or run the
  Gunicorn comparison via **WSL** or **Docker** (this repo ships a Docker setup for exactly that).
- **uvloop is also Unix-only**, so on Windows Uvicorn falls back to stock `asyncio` + the `h11` parser.
  That fallback is correct but slower — which is why our **Windows benchmarks understate async throughput**.
  On Linux with uvloop, a single async worker handles *far* more concurrent I/O than the Windows numbers
  suggest. Treat the Windows results as a conservative floor, not the production ceiling.

**Net:** benchmark on Windows for convenience and to see the *patterns*, but size and tune for **Linux +
uvloop**, which is where the cluster actually runs.

---

## 7. How and why Gunicorn works (and where it still earns its place)

Gunicorn is a **pre-fork** server: a **master** process binds the socket and forks **worker** processes;
the OS load-balances accepted connections across them. The master handles **no requests itself** — it is a
pure supervisor that restarts crashed workers, kills hung ones (`--timeout`), recycles workers
(`--max-requests`), reloads gracefully (`HUP`), hot-upgrades (`USR2`), and scales workers live
(`TTIN`/`TTOU`). Because its own workers are WSGI, you serve an ASGI app by giving it the **Uvicorn worker
class** — each worker then *is* a Uvicorn, so you get Gunicorn's supervision around Uvicorn's async speed.

On Kubernetes, most of that supervision is **redundant** — the platform already restarts, rolls, and
scales at the pod level. Gunicorn still earns its place **inside a fat pod**, where Kubernetes is blind to
the individual workers and you want `--timeout` / `--max-requests` / `--preload` operating at the
process level. Outside Kubernetes — a bare Linux VM with no orchestrator — Gunicorn + Uvicorn workers
remains the most battle-tested way to get that supervision.

> Note: `uvicorn.workers.UvicornWorker` is **deprecated** (since Uvicorn 0.30) in favour of the separate
> `uvicorn-worker` package — use `uvicorn_worker.UvicornWorker` for new deployments.

---

## 8. Every finding, in one table

| Finding | What we observed / concluded | Where it's proven |
|---|---|---|
| **Uvicorn ≠ Gunicorn (not competitors)** | Uvicorn = ASGI server; Gunicorn = process manager; the combo uses both | Architecture; [§3 of the reference](FINAL_CONFLUENCE_PAGE.md) |
| **Raw speed is the same** | "Gunicorn+Uvicorn" and "Uvicorn --workers" handle HTTP with identical Uvicorn code | Architecture; benchmarks |
| **1 async worker scales I/O** | The event loop keeps thousands of awaits in flight in one process | `/async-io` runs; clearest on Linux+uvloop |
| **Blocking code is catastrophic on 1 worker** | `/sync-io`: 1 worker → **272/1000 failures**; 4 workers → **0 failures** | [`results/raw/*sync-io.json`](results/raw/) |
| **CPU work rewards more processes** | `/cpu`: 4 workers = 130.6 req/s vs 1 worker = 106.8 (~22% on this box; bigger with heavier CPU) | [`results/raw/*cpu.json`](results/raw/) |
| **More workers ⇒ more CPU used** | Correct — and it's how you fill a multi-core node; the choice is pods vs workers | §2–§4 above |
| **The GIL caps one process at ~1 core** | Underutilisation comes from a fat allocation behind one worker, not from "1 worker/pod" | §2 above |
| **Kubernetes manages pods, not processes** | K8s can't restart/scale/health-check a worker *inside* a multi-worker container | [§14.2 of the reference](FINAL_CONFLUENCE_PAGE.md#142-the-one-fact-that-drives-everything-kubernetes-manages-pods-not-processes) |
| **Fill the node with many pods** | 7 thin pods on an 8-core node ⇒ full utilisation + granular control | §2, §4 above |
| **Fat pods win on memory (CoW)** | One `--preload`'d pod shares a big model across workers; many thin pods each copy it | §5 above |
| **CPU limits cause throttling** | Multi-process pods with CPU limits get CFS-throttled even with spare cores | §4 above |
| **Windows: no Gunicorn, no uvloop** | Gunicorn needs `fcntl`/`fork`; uvloop is POSIX-only → Windows async numbers understate Linux | §6 above |

---

## 9. The final recommendation (six lines)

1. **Kubernetes is your manager** → default to **plain Uvicorn, one worker per pod**, scaled by the HPA.
2. **Fill the node with pods, not with workers-in-a-pod** → "1 worker/container" means *per pod*, and you
   run *many* pods per node. Right-size `requests` to ~1 core so 7-ish pods pack onto an 8-core box.
3. **Never give a single worker a multi-core allocation** → that's the only real underutilisation, and the
   GIL guarantees it. Size workers 1:1 with the pod's cores.
4. **Go fat (Gunicorn + workers, `--preload`) only for** a large shared model or heavy per-pod sidecar —
   and keep several pods for HA.
5. **Set memory limits; be cautious with CPU limits** (throttling). Let the HPA do the scaling.
6. **Develop on Windows if you like, but tune for Linux + uvloop** — that's where it runs, and where the
   async numbers are far better than the Windows floor we measured.

---

*Deeper detail: [FINAL_CONFLUENCE_PAGE.md](FINAL_CONFLUENCE_PAGE.md) (full reference, incl. the Kubernetes
deep dive in §14). New to the topic: [BEGINNERS_GUIDE.md](BEGINNERS_GUIDE.md). Every claim's source:
[CLAIMS_AND_SOURCES.md](CLAIMS_AND_SOURCES.md). Reproduce the numbers: [README.md](README.md).*
