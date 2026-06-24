# Slim Containers vs Fat Pods for Python ASGI on Kubernetes: A Reproducible Study

**A research report on how to package Uvicorn/Gunicorn workers when Kubernetes is the deployment target.**

> **Format:** Confluence-style research paper · **Reading time:** ~8 minutes · **Status:** complete, reproducible
> **Environment of record:** Windows 11 · 8 logical CPUs · Python 3.13.12 · FastAPI 0.115.6 · Uvicorn 0.34.0 `[standard]`
> **Date:** 2026-06-24 · **All data + code:** this repository (`research/`, `benchmarks/`, `results/`)

---

## Abstract

We study a single, practical question: when serving a Python ASGI application (FastAPI) on Kubernetes,
should you deploy **"slim containers"** — one Uvicorn worker per pod, with Kubernetes replicating pods — or
**"fat pods"** — a process manager (Gunicorn) running several Uvicorn workers inside each pod? We hold the
**total worker count constant (4 processes)** and compare the two packagings under a fixed **10,000-request**
load on two workload shapes (async I/O-bound and CPU-bound), measuring throughput and tail latency. We
supplement the experiment with a queueing-theoretic capacity model and a survey of first-party
documentation and field reports. **Both architectures serve 10,000 requests reliably**; raw latency is
**workload-dependent and, on representative hardware, close**. The deciding factors are therefore
**operational**, not latency: autoscaler signal quality, failure isolation, and health-check fidelity all
favour slim containers on Kubernetes. **We conclude: default to slim containers (1 worker/pod); adopt fat
Gunicorn pods only for specific, named reasons** (large shared in-memory models, sidecar amortisation, or a
deliberate need for in-pod worker supervision).

---

## 1. Introduction & problem statement

Python's Global Interpreter Lock (GIL) permits only one thread to execute Python bytecode at a time per
process, so a single Uvicorn process saturates at roughly one CPU core for CPU-bound work. Scaling beyond
one core requires **multiple processes**. On a single host this was historically achieved by a process
manager — Gunicorn — supervising N Uvicorn workers. On Kubernetes, the same parallelism can instead be
achieved by running **more pods**, each a single process, replicated and supervised by the orchestrator.

These two strategies produce the **same number of worker processes** but distribute control very
differently:

- **Slim containers (Architecture A):** N pods × 1 worker. Kubernetes owns every unit.
- **Fat pods (Architecture B):** M pods × K workers, supervised by Gunicorn. Kubernetes owns the pod;
  Gunicorn owns the workers inside it.

The research question: **for equal capacity, which packaging yields better latency and operability on
Kubernetes, and what should teams adopt?**

---

## 2. Background

**ASGI vs WSGI.** FastAPI is an ASGI framework and must be served by an ASGI server (Uvicorn); a plain
synchronous WSGI server cannot run it. Gunicorn is a WSGI process manager that can host ASGI apps only by
delegating each worker to a Uvicorn worker class.

**The GIL and concurrency.** Async concurrency (the event loop) gives one worker high throughput for
**awaited I/O** but zero parallelism for **CPU** work or for any blocking call, which freezes the loop.
Extra **processes** are the only way to (a) use multiple cores and (b) keep serving when one worker is busy
or crashed.

**Kubernetes' unit of management is the Pod**, not the OS process. The Horizontal Pod Autoscaler (HPA)
scales pod replicas on per-pod metrics; liveness/readiness probes act on a container endpoint. Crucially,
**Kubernetes cannot see or individually manage the worker processes inside a multi-worker container** —
that is Gunicorn's job. This asymmetry is the root of the entire slim-vs-fat trade-off.

---

## 3. Methodology

### 3.1 System under test

A FastAPI application (`app/main.py`) exposing four endpoints that isolate work shapes; every response
carries its serving process's PID so we can verify load distribution:

| Endpoint | Work | Models |
|---|---|---|
| `/` | trivial JSON | server/framework overhead floor |
| `/async-io` | `await asyncio.sleep(0.05)` | well-behaved async I/O (DB/cache/HTTP) |
| `/sync-io` | `time.sleep(0.05)` in `async def` | the blocking anti-pattern |
| `/cpu` | busy compute loop | CPU-bound, GIL-limited |

### 3.2 Load generator

A pure-Python async `httpx` client (`benchmarks/loadtest.py`) fires a fixed request total with a
concurrency cap, recording throughput, latency percentiles (p50/p90/p95/p99/max/mean), error tally, and
the distinct serving-PID distribution. For the architecture experiment, a round-robin dispatcher
(`research/scripts/arch_comparison.py`) emulates a Kubernetes Service spreading connections across pod
endpoints.

### 3.3 Experimental design

**Primary experiment — equal-capacity A/B, 10,000 requests, concurrency 100:**

- **A (slim):** 4 pods × 1 Uvicorn worker = 4 processes.
- **B (fat):** 1 pod × 4 Uvicorn/Gunicorn workers = 4 processes.

Both run for `/async-io` and `/cpu`. Equal total processes isolate the **packaging** effect from capacity.

**Supplementary analyses:**
- The original suite (`benchmarks/run_suite.py`): 1-worker vs 4-worker across all four endpoints.
- A queueing model (`research/scripts/latency_model.py`, M/M/c / Erlang-C) predicting capacity and tail
  latency for 1–4 pods at arbitrary load — the honest substitute for measurements a single host cannot
  produce (see §6, Threats to validity).

### 3.4 Reproducibility

All commands, raw JSON, and manifests are in the repository. The architecture experiment:
```
python research/scripts/arch_comparison.py --requests 10000 --concurrency 100 \
    --endpoint /async-io --slim-pods 4 --fat-pods 1 --fat-workers 4
```

---

## 4. Results

### 4.1 Reliability — both architectures serve 10,000 requests

| Architecture | Endpoint | Requests | OK | Errors | Distinct PIDs |
|---|---|---:|---:|---:|---:|
| A — slim (4×1) | `/async-io` | 10,000 | 9,999 | 1 (0.01%) | **4** |
| B — fat (1×4) | `/async-io` | 10,000 | 10,000 | 0 | **4** |
| A — slim (4×1) | `/cpu` | 10,000 | 10,000 | 0 | **4** |
| B — fat (1×4) | `/cpu` | 10,000 | 10,000 | 0 | **4** |

**Finding R1.** Both packagings handle the full load reliably. **Distinct PIDs = 4 in every case** confirms
all four worker processes shared the load in both architectures — the Service emulation distributed work as
intended.

### 4.2 Latency — async I/O (50 ms simulated downstream, concurrency 100)

| Metric | A — slim (4×1) | B — fat (1×4) | Δ |
|---|---:|---:|---|
| Throughput (req/s) | **193.2** | 114.8 | A +68% |
| p50 (ms) | **377.5** | 584.3 | A better |
| p95 (ms) | **1272.8** | 2559.5 | A better |
| **p99 (ms)** | **2015.5** | 4045.4 | **A ~2× better** |
| max (ms) | **5160.6** | 9231.6 | A better |

**Finding R2.** For async I/O, **slim containers won decisively** — ~2× lower p99 and ~68% higher throughput.

### 4.3 Latency — CPU-bound (concurrency 100)

| Metric | A — slim (4×1) | B — fat (1×4) | Δ |
|---|---:|---:|---|
| Throughput (req/s) | 248.0 | **397.6** | B +60% |
| p50 (ms) | 296.3 | **168.7** | B better |
| p95 (ms) | 1026.8 | **749.9** | B better |
| **p99 (ms)** | 1524.6 | **1145.0** | **B ~25% better** |
| max (ms) | 2918.3 | **2118.6** | B better |

**Finding R3.** For CPU-bound work on this single host, **the fat pod won** — lower p99 and higher throughput.

### 4.4 Supplementary — the blocking anti-pattern and multi-process necessity

From the original suite, the `/sync-io` blocking endpoint with **1 worker failed 272/1000 requests**
(p99 ≈ 30 s) but with **4 workers succeeded 1000/1000** (p99 ≈ 12 s); `/cpu` rose from 106.8 → 130.6 req/s
(1→4 workers). **Finding R4:** multiple processes are mandatory for resilience under blocking work and for
CPU parallelism — independent of *how* those processes are packaged.

### 4.5 Capacity model

For CPU-bound work at 25 ms/request, the M/M/c model gives each pod (4 lanes) a capacity of ~160 req/s;
**4 pods (16 lanes, ~640 req/s)** sustain 500 req/s at modelled p99 ≈ 48 ms, while 1–3 pods are overloaded.
**Finding R5:** capacity scales linearly with total workers regardless of packaging; pod count is chosen by
matching capacity to offered load under a tail-latency target.

---

## 5. Analysis: why the two endpoints disagree

The opposite latency winners (§4.2 vs §4.3) are not contradictory — they reveal what each packaging
optimises and where the single-host measurement distorts the picture.

**Async I/O favours slim.** Each slim pod has its own event loop and its own listening socket; the
round-robin client fans connections cleanly across four independent accept paths. The fat pod funnels all
100 concurrent connections through **one shared socket** that four workers contend on, and without uvloop
(Windows) that contention manifests as worse tail latency. The bottleneck here is loop/socket scheduling,
which slim parallelises better.

**CPU favours fat — but largely as a single-host artefact.** With CPU work the win comes from **lower
coordination overhead**: one process group and one shared accept queue feeding four hungry workers, versus
four separate pods each paying their own per-process and per-connection-pool overhead **while sharing the
same eight physical cores**. On one contended host this favours the fat pod. **On a real cluster this
advantage erodes**, because each slim pod would receive its **own dedicated CPUs on its own node** — the
isolation a single machine fundamentally cannot reproduce.

**Net:** raw latency is workload-dependent and, on realistic per-pod-dedicated hardware, close. Neither
architecture is universally faster. The decision must rest on properties that *do* differ structurally —
the operational ones.

---

## 6. Threats to validity

We state these explicitly; they bound every absolute number above.

1. **Single host, shared cores.** All four workers plus the load generator ran on one 8-core machine.
   Real pods get dedicated CPUs on separate nodes; the fat-pod CPU win (§4.3) is partly a shared-host
   effect (§5). Mitigation: a queueing model (§4.5) and ready-to-run cluster manifests
   (`research/manifests/`, `research/scripts/k8s_loadtest.sh`) for ground truth.
2. **No Gunicorn / no uvloop on Windows.** Gunicorn cannot run on Windows (Unix-only `fcntl`); Architecture
   B used `uvicorn --workers` as the same-shape stand-in, and uvloop (Linux-only, ~2–4× faster) was absent.
   Therefore **async numbers are a conservative floor**, not a Linux/production ceiling.
3. **Synthetic endpoints.** `asyncio.sleep`/`time.sleep`/a busy loop approximate real I/O and compute but
   omit real driver, serialisation, and network behaviour.
4. **Load-generator co-location.** The client competes for the same cores, inflating tail latency uniformly.

None of these threats invalidate the **operational** findings (§7), which derive from architecture and
documented behaviour, not from the host's timing.

---

## 7. The operational case (the actual decision driver)

Because raw latency does not separate the architectures decisively, the choice rests on how each behaves
under Kubernetes' management model. Here the asymmetry is structural and one-sided:

| Property | Slim (1 worker/pod) | Fat (N workers/pod) |
|---|---|---|
| **HPA signal quality** | Clean: per-pod metric = one worker's load | Lumpy: per-pod metric averages N workers → confuses the autoscaler |
| **Failure isolation (OOM)** | One worker's spike kills only its pod | One worker's spike can OOM-kill the whole pod (all N workers) |
| **Health-check fidelity** | Probe reflects that worker's true health | Probe answered by any free worker → a hung worker hides behind a healthy one |
| **Granular K8s control** | Each worker independently scheduled/rescheduled/rolled | K8s blind to inner workers; Gunicorn manages them |
| **Cross-node HA** | Pods spread across nodes | All N workers pinned to one node |
| **Memory (large shared model)** | N full copies (costly) | One copy shared via `--preload` copy-on-write (efficient) |
| **Sidecar overhead** | One sidecar per thin pod | One sidecar amortised over N workers |
| **Operational simplicity** | One supervisor (Kubernetes) | Two supervisors to reason about |

This matches first-party guidance. FastAPI's deployment docs state that on a cluster you should *"handle
replication at the cluster level instead of using a process manager (like Uvicorn with workers) in each
container,"* and that a second in-container process manager *"would only add unnecessary complexity that you
are most probably already taking care of with your cluster system."* The documented counter-argument is the
**heartbeat problem** — a single *synchronous* worker can fail to answer probes while busy — which motivates
a small worker count (or worker+threads) rather than rigidly one, and is fully addressed by separating
liveness from readiness and using Gunicorn's per-worker `--timeout`.

---

## 8. Conclusion & recommendation

**Default to slim containers — one Uvicorn worker per pod — and let Kubernetes replicate and supervise.**

The evidence is consistent and points one way once latency is set aside as inconclusive:

1. **Latency does not decide it.** Both architectures serve 10,000 requests reliably; slim wins async I/O,
   fat wins CPU on a shared host, and that CPU win is expected to shrink when pods get dedicated CPUs
   (§4–§6). There is no robust latency reason to prefer fat pods on Kubernetes.
2. **Operations decide it, and they favour slim** (§7): accurate HPA scaling, OOM blast-radius isolation,
   truthful health checks, granular scheduling, and cross-node HA — precisely the capabilities teams adopt
   Kubernetes for. Stacking a second process manager inside the pod forfeits them.
3. **The GIL still mandates multiple processes** (§4.4) — but on Kubernetes those processes should be
   **pods**, not workers hidden inside one container.

**When to deliberately choose fat Gunicorn pods** (the bounded exceptions): a **large read-only in-memory
model** where `--preload` copy-on-write yields decisive RAM savings; a **per-pod service-mesh sidecar** whose
overhead is worth amortising over several workers; or an explicit need for **in-pod worker supervision**
(`--timeout` hung-worker kill, `--max-requests` recycling) beyond what the cluster provides. Even then, run
**several pods**, never one node-filling pod, to retain HA and rolling-deploy granularity — and size workers
1:1 with the pod's CPU allotment so the GIL never strands a core.

**One-line verdict.** *On Kubernetes, prefer slim containers (1 worker per pod) by default; reach for fat
Gunicorn pods only for shared-memory, sidecar, or in-pod-supervision reasons — and keep them multi-pod.*

---

## 9. Reproducibility & artifacts

| Artifact | Path |
|---|---|
| A/B experiment harness | `research/scripts/arch_comparison.py` |
| A/B raw results (10k) | `research/data/arch_comparison__async-io.json`, `…__cpu.json` |
| Cluster emulation | `research/scripts/cluster_emulation.py` |
| Capacity model (M/M/c) | `research/scripts/latency_model.py` |
| Cluster ground-truth manifests + load test | `research/manifests/`, `research/scripts/k8s_loadtest.sh` |
| Original benchmark suite | `benchmarks/run_suite.py`, `results/raw/*.json` |
| Sourced claims / further reading | `research/sourced-edition-core-claims.md`, `research/related-articles.md`, `CLAIMS_AND_SOURCES.md` |
| Why fat pods are discouraged on K8s (cited) | `research/why-not-gunicorn-workers-on-kubernetes.md` |

## 10. Key references (primary)

- FastAPI — [Server Workers](https://fastapi.tiangolo.com/deployment/server-workers/) · [FastAPI in Containers (Docker)](https://fastapi.tiangolo.com/deployment/docker/)
- Uvicorn — [Deployment](https://www.uvicorn.org/deployment/) · [Settings](https://www.uvicorn.org/settings/)
- Gunicorn — [Design](https://docs.gunicorn.org/en/stable/design.html) · [Settings](https://docs.gunicorn.org/en/stable/settings.html) · [issue #2467 (probes vs workers)](https://github.com/benoitc/gunicorn/issues/2467)
- Kubernetes — [Pods](https://kubernetes.io/docs/concepts/workloads/pods/) · [HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) · [Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/) · [Resource management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
- Python — [GIL (glossary)](https://docs.python.org/3/glossary.html#term-global-interpreter-lock) · [PEP 703](https://peps.python.org/pep-0703/)

*Full per-line sourcing for every claim made in this repository: [`CLAIMS_AND_SOURCES.md`](CLAIMS_AND_SOURCES.md) and [`research/sourced-edition-core-claims.md`](research/sourced-edition-core-claims.md).*
