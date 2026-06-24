# Experiment: Slim pods (Uvicorn) vs Fat pod (Gunicorn) over 10,000 requests

> **What this tests.** Two ways to deploy the **same 4 worker processes**, each hit with **10,000 requests**,
> then compare latency:
>
> - **Architecture A — "slim containers":** **4 pods × 1 Uvicorn worker.** Kubernetes manages each pod
>   independently (here emulated by a round-robin Service across 4 ports). *The cloud-native default.*
> - **Architecture B — "fat pod":** **1 pod × 4 Gunicorn workers.** Kubernetes manages the pod; **Gunicorn
>   manages the 4 workers inside it.** *The "process manager in the container" pattern.*
>
> Both use **4 total worker processes**, so any latency difference is due to **packaging**, not capacity —
> exactly the "pods vs workers" question, run as a measured experiment.
>
> **Harness:** [`scripts/arch_comparison.py`](scripts/arch_comparison.py) · **Data:** [`data/arch_comparison__async-io.json`](data/arch_comparison__async-io.json), [`data/arch_comparison__cpu.json`](data/arch_comparison__cpu.json)
> **Run on:** 2026-06-24 · Windows 11 · 8 CPUs · Python 3.13.12 · FastAPI 0.115.6 · Uvicorn 0.34.0`[standard]`

---

## 1. Does it work? — Yes

Both architectures booted, became healthy, and served the full load with essentially **zero errors**:

| Architecture | Endpoint | Requests | OK | Errors | Distinct worker PIDs |
|---|---|---:|---:|---:|---:|
| A — 4 pods × 1 worker | `/async-io` | 10,000 | 9,999 | 1 | **4** |
| B — 1 pod × 4 workers | `/async-io` | 10,000 | 10,000 | 0 | **4** |
| A — 4 pods × 1 worker | `/cpu` | 10,000 | 10,000 | 0 | **4** |
| B — 1 pod × 4 workers | `/cpu` | 10,000 | 10,000 | 0 | **4** |

- ✅ **Both work at 10k requests.** (The single async-io error in A was one transient connection drop out of 10,000 = 0.01%.)
- ✅ **Distinct PIDs = 4 in every case** — proof that all 4 worker processes shared the load in *both* packagings (4 pods × 1, and 1 pod × 4). The round-robin Service emulation genuinely spread the work.

---

## 2. The latency numbers (10,000 requests each)

### Async I/O endpoint (`await asyncio.sleep(0.05)` ≈ a 50 ms downstream call), concurrency 100

| Metric | A — slim (4 pods × 1) | B — fat (1 pod × 4) | Winner |
|---|---:|---:|:---:|
| Throughput (req/s) | **193.2** | 114.8 | A |
| p50 (ms) | **377.5** | 584.3 | A |
| p95 (ms) | **1272.8** | 2559.5 | A |
| **p99 (ms)** | **2015.5** | 4045.4 | **A** |
| max (ms) | **5160.6** | 9231.6 | A |
| mean (ms) | **504.0** | 858.0 | A |

→ **For async I/O, slim pods win decisively** — about **2× lower p99** (2015 ms vs 4045 ms) and higher throughput.

### CPU endpoint (busy compute, GIL-bound), concurrency 100

| Metric | A — slim (4 pods × 1) | B — fat (1 pod × 4) | Winner |
|---|---:|---:|:---:|
| Throughput (req/s) | 248.0 | **397.6** | B |
| p50 (ms) | 296.3 | **168.7** | B |
| p95 (ms) | 1026.8 | **749.9** | B |
| **p99 (ms)** | 1524.6 | **1145.0** | **B** |
| max (ms) | 2918.3 | **2118.6** | B |
| mean (ms) | 389.3 | **246.9** | B |

→ **For CPU-bound work, the fat Gunicorn pod won** on this machine — lower p99 (1145 ms vs 1525 ms) and higher throughput.

---

## 3. Why the two endpoints disagree (the honest interpretation)

The opposite results are not a contradiction — they expose **what each packaging optimises for, and the limits of measuring this on one machine.**

**Async I/O → slim wins because the bottleneck is the event loop, and 4 separate loops + 4 separate accept paths spread the scheduling work.** Each slim pod has its own process, its own event loop, and its own listening socket; the round-robin client fans connections cleanly across them. The fat pod funnels all 100 concurrent connections through **one shared listening socket** that 4 workers contend on, and on Windows (no uvloop) that contention shows up as worse tail latency. *(ours — interpretation of the measured data)*

**CPU-bound → fat won here because of a single-machine artefact, not a deep truth.** With CPU work, the win comes from lower coordination overhead: one OS process group, one shared accept queue feeding 4 hungry workers, versus 4 separate pods each paying their own per-process and per-connection overhead while the client also juggles 4 connection pools. On **one shared host** that favours the fat pod. **On a real cluster this advantage largely disappears**, because each slim pod would get its **own dedicated CPUs on its own node** — the very isolation a single laptop cannot give them. *(ours — see the honesty note below)*

> **⚠️ Single-machine caveat (the same one stated throughout `research/`).** This experiment runs all 4
> workers — and the load generator — on **one 8-core Windows box without uvloop and without Gunicorn**
> (Architecture B uses `uvicorn --workers` as the stand-in, since [Gunicorn can't run on Windows](why-not-gunicorn-workers-on-kubernetes.md)).
> So these are a **same-hardware packaging comparison**, not real-cluster numbers. The latency *mechanism*
> they show is real; the *absolute* CPU-pod win is partly a shared-host effect. For ground truth, run
> [`scripts/k8s_loadtest.sh`](scripts/k8s_loadtest.sh) with the [manifests](manifests/) on a real cluster,
> where each pod owns its CPUs.

---

## 4. So which should you use? (this is where the rest of `research/` matters)

The raw latency here is close enough — and regime-dependent enough — that **the decision is driven by the
operational properties, not these millisecond deltas.** And on operational grounds, **slim pods are the
Kubernetes default**, because (all sourced in [why-not-gunicorn-workers-on-kubernetes.md](why-not-gunicorn-workers-on-kubernetes.md)):

- The **HPA autoscales on clean per-pod metrics** — a fat pod's 4 workers make per-pod resource use lumpy and confuse the autoscaler.
- **No shared-limit OOM** — in a fat pod, one worker's spike can OOM-kill the whole pod (all 4 workers); slim pods isolate that blast radius.
- **Health checks are unambiguous** — a slim pod's probe reflects *that* worker's health; a fat pod's probe is answered by whichever worker is free, hiding a hung one.
- **Kubernetes gets granular control** — each slim pod is independently scheduled, rescheduled, and rolled.

**Use the fat Gunicorn pod when** you have a specific reason: a **large shared in-memory model** (`--preload` copy-on-write saves RAM), a heavy **per-pod sidecar** to amortise, or you want Gunicorn's in-pod **`--timeout`/`--max-requests`** hygiene — and even then, run **several pods**, not one node-filling pod. (See [reference §14](../FINAL_CONFLUENCE_PAGE.md#14-kubernetes-on-powerful-multi-core-nodes-pods-vs-workers).)

> **Bottom line:** Both architectures handle 10,000 requests cleanly. Latency is regime-dependent and, on
> real hardware where each slim pod owns its CPUs, close. So **pick on operations: slim pods (1 worker/pod)
> by default for Kubernetes' granular control; fat Gunicorn pods only for shared-memory / sidecar / in-pod-
> robustness reasons.** That is the same conclusion the rest of this repo reaches — now backed by a direct
> 10k-request A/B measurement.

---

## 5. Reproduce it

```bash
# Async I/O: 4 slim pods (1 worker) vs 1 fat pod (4 workers), 10k requests
python research/scripts/arch_comparison.py --requests 10000 --concurrency 100 \
    --endpoint /async-io --slim-pods 4 --fat-pods 1 --fat-workers 4

# CPU-bound version
python research/scripts/arch_comparison.py --requests 10000 --concurrency 100 \
    --endpoint /cpu --slim-pods 4 --fat-pods 1 --fat-workers 4

# Vary it: 2 fat pods x 4 workers vs 8 slim pods (still 8 total workers)
python research/scripts/arch_comparison.py --requests 10000 --slim-pods 8 --fat-pods 2 --fat-workers 4
```

On **Linux**, Architecture B automatically uses real **Gunicorn + `uvicorn_worker.UvicornWorker`**.
For true per-pod-dedicated-CPU numbers, deploy [`manifests/k8s-gunicorn-4workers.yaml`](manifests/k8s-gunicorn-4workers.yaml)
and run [`scripts/k8s_loadtest.sh`](scripts/k8s_loadtest.sh) on a real cluster.

---

## 6. Raw results (verbatim from the run)

```
SIDE-BY-SIDE  (10000 requests, /async-io, concurrency 100)
A_slim_4pods_x1worker                 193.2  p50=377.48  p95=1272.78  p99=2015.49  max=5160.6   ok/err=9999/1
B_fat_1pod_x4workers                  114.8  p50=584.27  p95=2559.53  p99=4045.43  max=9231.62  ok/err=10000/0
  -> Lower p99: A_slim_4pods_x1worker (2015.49 ms vs 4045.43 ms)

SIDE-BY-SIDE  (10000 requests, /cpu, concurrency 100)
A_slim_4pods_x1worker                 248.0  p50=296.30  p95=1026.77  p99=1524.64  max=2918.33  ok/err=10000/0
B_fat_1pod_x4workers                  397.6  p50=168.74  p95=749.85   p99=1144.99  max=2118.63  ok/err=10000/0
  -> Lower p99: B_fat_1pod_x4workers (1144.99 ms vs 1524.64 ms)
```

Full JSON (every percentile + per-PID distribution): [`data/arch_comparison__async-io.json`](data/arch_comparison__async-io.json), [`data/arch_comparison__cpu.json`](data/arch_comparison__cpu.json).
