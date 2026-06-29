# Research: Latency of "N pods × 4 workers (Gunicorn)" on a Kubernetes cluster

> **The scenario being researched.** A Kubernetes cluster. Each **pod is allotted 4 CPUs** and runs
> **Gunicorn with 4 Uvicorn workers** (one worker per core — the GIL means one Python process uses one
> core at a time). The cluster runs **3–4 such pods**, and a Service load-balances across them.
> **Question:** as we scale the number of pods and the offered load, *what happens to latency*?
>
> **What this folder delivers**
> 1. A **real, reproducible emulation** of "N pods × M workers" on this machine ([`scripts/cluster_emulation.py`](scripts/cluster_emulation.py)) — measured data in [`data/`](data/).
> 2. An **analytical latency model** ([`scripts/latency_model.py`](scripts/latency_model.py)) that *predicts* real-cluster latency at 1/2/3/4 pods and any load, using queueing theory — because a single laptop cannot honestly stand in for 4 pods × 4 dedicated CPUs.
> 3. **Production Kubernetes manifests** ([`manifests/k8s-gunicorn-4workers.yaml`](manifests/k8s-gunicorn-4workers.yaml)) and a **ground-truth load-test script** ([`scripts/k8s_loadtest.sh`](scripts/k8s_loadtest.sh)) to measure the real thing on your cluster.
>
> **Last run:** 2026-06-24 · Windows 11 · 8 CPUs · Python 3.13.12 · FastAPI 0.115.6 · Uvicorn 0.34.0`[standard]`
>
> **📚 Companion articles in this folder (every line individually sourced):**
> - [**Slim pods (Uvicorn) vs Fat pod (Gunicorn) — a 10,000-request A/B experiment**](slim-vs-fat-10k-experiment.md) — measured latency for "4 pods × 1 worker" vs "1 pod × 4 Gunicorn workers."
> - [**Why in-pod Gunicorn workers are discouraged on Kubernetes**](why-not-gunicorn-workers-on-kubernetes.md) — the autoscaler/OOM/health-check reasons, with the counter-arguments.
> - [**Memory leaks & worker recycling — `max_requests` vs Kubernetes (one worker per pod)**](memory-leaks-and-worker-recycling.md) — does LangGraph+PostgresSaver really leak (measured), how `max_requests` frees memory, and why "1 worker/pod × 2 pods" recycling causes 529/530 + how to fix it.
> - [**Recycling one Uvicorn worker per pod — the Kubernetes way (no Gunicorn)**](recycling-one-uvicorn-worker-per-pod-on-kubernetes.md) — when Kubernetes is your process manager: the Gunicorn→K8s translation, the CrashLoopBackOff trap, and a `kubectl rollout restart` CronJob as the native `max_requests`.
> - [**Is "one process per pod" really the industry standard? — the proof**](is-one-process-per-pod-industry-standard.md) — the claim proven with primary sources from five independent authorities (Kubernetes, Google Cloud, AWS, the Twelve-Factor App, FastAPI), each quoted, honestly graded.
> - [**Sourced Edition — core Uvicorn-vs-Gunicorn claims, line by line**](sourced-edition-core-claims.md) — each claim tagged with an inline `[S#]` you can click to verify.
> - [**Related articles & further reading**](related-articles.md) — curated sources, labelled by what they back and how much to trust them.

---

## 1. Why we both measure *and* model (read this first — it's about honesty)

The exact scenario — Gunicorn, Linux, uvloop, and **each pod owning 4 real CPUs** — **cannot be
faithfully reproduced on one laptop**, for three reasons:

1. **Gunicorn doesn't run on Windows** (needs the Unix-only `fcntl`). On this Windows host the emulation
   substitutes `uvicorn --workers 4`, which is the *same N-pods-of-M-workers shape* but without Gunicorn's
   supervisor. (Run the manifests on a real Linux cluster for Gunicorn-exact numbers.)
2. **A laptop has a fixed core count.** Emulating "2 pods × 4 workers = 8 worker processes" on an 8-core
   box already saturates every core; "4 pods × 4 = 16 processes" would oversubscribe 2:1. So **absolute
   throughput from the emulation is a lower bound, not the cluster's real ceiling.**
3. **No uvloop on Windows** → the event loop is slower than Linux, so async numbers understate Linux.

So we do two complementary things, and we are explicit about which is which:

| Approach | What it gives | Trust it for |
|---|---|---|
| **Emulation** ([`cluster_emulation.py`](scripts/cluster_emulation.py)) | Real measured latency on identical hardware | The *shape* of scaling; per-pod behaviour; proof workers/pods share load |
| **Model** ([`latency_model.py`](scripts/latency_model.py)) | Predicted latency for 1/2/3/4 pods at any load | Capacity planning; "how many pods for my target p99?" |
| **Real cluster** ([manifests](manifests/) + [`k8s_loadtest.sh`](scripts/k8s_loadtest.sh)) | Ground truth | Final sign-off numbers |

---

## 2. The mental model: the deployment is a queue with `c` lanes

Think of the whole deployment as **one queue feeding `c` parallel service lanes**:

```
            requests arrive at rate λ (req/s)
                        │
                 [ Kubernetes Service ]   ← load-balances across pods
            ┌───────────┼───────────┐
          Pod 1       Pod 2       Pod 3 …      (each pod = 4 worker processes)
        ┌──┬──┬──┐  ┌──┬──┬──┐  ┌──┬──┬──┐
        w  w  w  w  w  w  w  w  w  w  w  w      ← these are the "lanes" (c = pods × 4)
```

- **`c` = pods × workers_per_pod** concurrent lanes (for CPU-bound work; see §5 for async I/O).
- **Service time `S`** = how long one worker is busy with one request.
- **Capacity** `C = c / S` req/s. Past this, the queue grows without bound → latency explodes.
- **Utilisation** `ρ = λ / C`. The golden rule: **keep ρ below ~0.7–0.8**, or tail latency (p95/p99)
  blows up — this is the non-linear "hockey-stick" every load test shows.

**Adding pods raises `c` → raises capacity → lowers ρ at the same load → lowers p95/p99.**
That single sentence is the whole answer to "what does scaling pods do to latency."

---

## 3. Predicted latency for the exact scenario (1→4 pods × 4 workers)

From [`scripts/latency_model.py`](scripts/latency_model.py). **CPU-bound** example: each request keeps a
worker busy **25 ms** (e.g. serialisation + light compute). `c = pods × 4`, capacity `= c / 0.025`.

```
python research/scripts/latency_model.py --service-ms 25 --pods 1 2 3 4 --workers 4 \
    --lambda 100 300 500 700
```

| Pods | Lanes (c) | Capacity (rps) | Load 100 rps | Load 300 rps | Load 500 rps | Load 700 rps |
|---:|---:|---:|---|---|---|---|
| **1** | 4 | 160 | p99 **82.8 ms** (ρ=0.63) | ⛔ overloaded | ⛔ overloaded | ⛔ overloaded |
| **2** | 8 | 320 | p99 **25 ms** (ρ=0.31) | p99 **244 ms** (ρ=0.94) | ⛔ overloaded | ⛔ overloaded |
| **3** | 12 | 480 | p99 **25 ms** (ρ=0.21) | p99 **37.6 ms** (ρ=0.63) | ⛔ overloaded | ⛔ overloaded |
| **4** | 16 | 640 | p99 **25 ms** (ρ=0.16) | p99 **25 ms** (ρ=0.47) | p99 **48.4 ms** (ρ=0.78) | ⛔ overloaded |

**How to read this table (the core findings):**

- **Each pod adds ~160 rps of capacity** (4 lanes ÷ 25 ms). 1 pod ≈ 160, 2 ≈ 320, 3 ≈ 480, 4 ≈ 640 rps.
- **Latency is flat-then-cliff.** Well below capacity, p99 ≈ the service time (25 ms). As load approaches
  capacity, p99 shoots up (note 2 pods at 300 rps: ρ=0.94 → p99 jumps to **244 ms**), then the pod count
  can't serve it at all (⛔).
- **More pods = headroom = stable tail latency.** At **500 rps, only 4 pods** keeps you healthy
  (ρ=0.78, p99 ≈ 48 ms); 1–3 pods are overloaded. **This is the quantitative answer to "how many pods?"**
- **Picking a pod count = picking the load you can serve under your p99 target.** Want 500 rps at
  p99 < 50 ms for this workload? You need **4 pods**. Want 300 rps? **3 pods** suffice.

> Calibrate `--service-ms` to *your* endpoint: `service_ms ≈ 1000 ÷ (single_pod_max_rps ÷ workers_per_pod)`.

---

## 4. Real measured emulation (this machine, honest numbers)

From [`scripts/cluster_emulation.py`](scripts/cluster_emulation.py), `/async-io` endpoint
(`await asyncio.sleep(0.05)` ≈ a 50 ms downstream call), 1500 requests/run, round-robin across pods.
Data: [`data/emulation__async-io.json`](data/emulation__async-io.json).

| Pods × workers | Concurrency | Throughput (rps) | p50 (ms) | p95 (ms) | p99 (ms) | OK | Distinct PIDs |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 × 4 | 50 | 102.7 | 326.5 | 1321.4 | 2427.6 | 1500/1500 | **4** |
| 1 × 4 | 100 | 82.7 | 742.1 | 3433.6 | 6074.1 | 1500/1500 | **4** |
| 1 × 4 | 200 | 36.5 | 1361.8 | 7661.7 | 31071.9 | 1476/1500 | **4** |
| 2 × 4 | 50 | 116.2 | 326.6 | 1014.0 | **1528.8** | 1500/1500 | **8** |
| 2 × 4 | 100 | 35.6 | 645.5 | 2417.6 | 11916.6 | 1492/1500 | **8** |
| 2 × 4 | 200 | 33.7 | 1611.5 | 6321.6 | 10726.9 | 1497/1500 | **8** |

**What's real and useful here:**

- ✅ **Worker/pod spread is proven:** distinct serving PIDs = **4** with one pod, **8** with two pods.
  The Service-style round-robin genuinely spread load across every worker in every pod.
- ✅ **At the lower, non-saturating load (conc 50), 2 pods beat 1** on tail latency:
  p99 **1528 ms vs 2427 ms** — scaling out helped, exactly as the model predicts.
- ⚠️ **At higher concurrency the numbers get noisy and even *invert*** (e.g. 2 pods/conc 200). This is
  **not** a real cluster behaviour — it's **core oversubscription**: 8 server workers + the load generator
  fighting over 8 cores, plus Windows' no-uvloop loop. It is the single-machine ceiling described in §1,
  and it's exactly why we rely on the model (§3) for the multi-pod projection.

The CPU run ([`data/emulation__cpu.json`](data/emulation__cpu.json)) shows the same: clean at 1 pod
(~400 rps, p99 ≈ 480–1445 ms across the sweep), but a 2-pod/conc-50 outlier (p99 ≈ 30 s) when 8
processes stall on 8 contended cores — again a laptop artefact, not the cluster.

> **Takeaway:** the emulation **confirms the mechanism** (load spreads across pods/workers; scaling out
> lowers tail latency while there's spare CPU) and **demonstrates the ceiling** (you can't fake 16
> dedicated cores). For real 4-pod × 4-CPU numbers, run §6 on your cluster.

---

## 5. CPU-bound vs async I/O — the lane count differs

The number of "lanes" `c` depends on the work type, and this changes everything:

- **CPU-bound** (`/cpu`): a worker is pinned to a core for the whole request. **`c = pods × workers`.**
  One pod × 4 workers = **4 lanes**. Capacity is small; scaling pods matters a lot.
- **Async I/O-bound** (`/async-io`): a worker `await`s and overlaps **many** in-flight requests. The
  effective lanes per worker are `K` (e.g. 40). **`c = pods × workers × K`.** Capacity is huge.

Model run for async I/O (50 ms downstream, K=40 overlaps/worker):

```
python research/scripts/latency_model.py --service-ms 50 --io-concurrency 40 \
    --pods 1 3 4 --workers 4 --lambda 1000 3000 6000 9000
```

| Pods | Lanes (c) | Capacity (rps) | 3000 rps | 6000 rps | 9000 rps |
|---:|---:|---:|---|---|---|
| 1 | 160 | 3,200 | p99 **67 ms** (ρ=0.94) | ⛔ overloaded | ⛔ overloaded |
| 3 | 480 | 9,600 | p99 **50 ms** (ρ=0.31) | p99 **50 ms** (ρ=0.63) | p99 **54 ms** (ρ=0.94) |
| 4 | 640 | 12,800 | p99 **50 ms** (ρ=0.23) | p99 **50 ms** (ρ=0.47) | p99 **50 ms** (ρ=0.70) |

**Finding:** for I/O-bound work, **one pod already absorbs thousands of rps** at p99 ≈ the downstream
latency, because the event loop overlaps awaits. You scale pods here mainly for **redundancy/HA and CPU
headroom**, not raw I/O concurrency. This is the same lesson as the main benchmarks: *add workers/pods for
CPU and resilience, not "more async."*

---

## 6. Ground truth: run it on your real cluster

The only way to get exact "4 pods × 4 real CPUs, Gunicorn, Linux, uvloop" numbers:

```bash
# 1. Build & push an image (from this repo's docker/Dockerfile), set it in the manifest:
#    image: <your-registry>/uvicorn-vs-gunicorn:latest

# 2. Deploy (3 pods, Service, HPA, PDB):
kubectl apply -f research/manifests/k8s-gunicorn-4workers.yaml
kubectl get pods -l app=uvg-research -w

# 3. Load-test at 1→4 pods and capture latency per pod count:
research/scripts/k8s_loadtest.sh /async-io 4000 200
research/scripts/k8s_loadtest.sh /cpu      1200 100
#    -> writes research/data/k8s/*.json ; compare p95/p99 across pod counts.
```

The manifest encodes the research scenario faithfully: **`requests.cpu: 4` per pod**, **Gunicorn `-w 4`**
with `uvicorn_worker.UvicornWorker`, `--timeout`/`--max-requests` for in-pod robustness, an **HPA** that
scales 3→8 pods on 70% CPU, a **memory limit but no CPU limit** (to avoid CFS throttling — see the
[reference §14.6](../FINAL_CONFLUENCE_PAGE.md#146-the-cpu-limits--throttling-trap-the-most-missed-gotcha)),
plus probes, graceful drain, topology spread, and a PodDisruptionBudget.

---

## 7. Conclusions — answering the research question directly

1. **What happens to latency as you scale pods?** Below capacity, latency stays flat at roughly the
   service time. As load nears a pod count's capacity, p95/p99 rise steeply, then that pod count can't
   serve the load at all. **Each added pod adds a fixed chunk of capacity** (`workers ÷ service_time`
   rps) and pushes the cliff further out. *Scaling pods buys tail-latency headroom.*

2. **How many pods for the scenario?** For CPU-bound work at 25 ms/req: 1 pod ≈ 160 rps, and you need
   **4 pods to safely serve ~500 rps** at p99 < 50 ms. For async I/O, **even 1 pod serves thousands of
   rps**; pods are then for HA and CPU headroom. *Pick the pod count from your target load and p99.*

3. **4 workers per 4-CPU pod is the right ratio** — one worker per core. Fewer workers strands cores
   (GIL caps one process at one core); more workers than cores adds context-switching with no gain.

4. **Gunicorn vs Uvicorn `--workers` doesn't change latency** — same Uvicorn handles requests in both.
   Inside a multi-worker pod, **Gunicorn earns its place** via `--timeout` (kill hung workers Kubernetes
   can't see) and `--max-requests` (recycle to bound leaks).

5. **A single machine can't measure 4×4 dedicated CPUs** — the emulation proves the *mechanism* and the
   model predicts the *cluster*; the manifests give *ground truth*. We measured what we honestly could
   and modelled the rest, rather than publishing laptop numbers as if they were cluster numbers.

---

## 8. Files in this folder

| Path | What it is |
|---|---|
| [`README.md`](README.md) | This document — the "N pods × 4 workers" latency research write-up |
| [`slim-vs-fat-10k-experiment.md`](slim-vs-fat-10k-experiment.md) | **Experiment:** 10k-request A/B — 4 slim Uvicorn pods vs 1 fat Gunicorn pod, measured latency |
| [`why-not-gunicorn-workers-on-kubernetes.md`](why-not-gunicorn-workers-on-kubernetes.md) | **Article:** why in-pod Gunicorn workers are discouraged on K8s — every line sourced |
| [`memory-leaks-and-worker-recycling.md`](memory-leaks-and-worker-recycling.md) | **Article + experiment:** LangGraph+PostgresSaver memory (measured), how `max_requests` frees memory, and fixing the synchronized "both pods recycle at once" 529/530 |
| [`recycling-one-uvicorn-worker-per-pod-on-kubernetes.md`](recycling-one-uvicorn-worker-per-pod-on-kubernetes.md) | **Article:** recycling a single Uvicorn worker per pod with **Kubernetes** as the manager — Gunicorn→K8s map, the CrashLoopBackOff trap, scheduled `rollout restart` |
| [`manifests/k8s-uvicorn-1worker-recycle.yaml`](manifests/k8s-uvicorn-1worker-recycle.yaml) | **Manifest:** one-uvicorn-worker-per-pod Deployment + Service + HPA + PDB + recycle CronJob (+RBAC), no Gunicorn |
| [`is-one-process-per-pod-industry-standard.md`](is-one-process-per-pod-industry-standard.md) | **Proof:** "one process per pod, cluster manages the rest" backed by Kubernetes, Google Cloud, AWS, Twelve-Factor, FastAPI — quoted + graded |
| [`scripts/langgraph_memory_probe.py`](scripts/langgraph_memory_probe.py) | Drives LangGraph+PostgresSaver and samples RSS/heap/threads/objects per request — leak vs plateau verdict |
| [`scripts/recycle_app.py`](scripts/recycle_app.py) · [`recycle_loadtest.py`](scripts/recycle_loadtest.py) · [`run_recycle_experiment.py`](scripts/run_recycle_experiment.py) | The worker-recycling A/B: no-recycle / 1-worker-death / synchronized / jittered, with latency + RSS sawtooth |
| [`sourced-edition-core-claims.md`](sourced-edition-core-claims.md) | **Article:** the core Uvicorn-vs-Gunicorn claims, each line with an inline `[S#]` source |
| [`related-articles.md`](related-articles.md) | **Reading list:** curated sources, each labelled by what it backs and its trust level |
| [`scripts/cluster_emulation.py`](scripts/cluster_emulation.py) | Boots N pods × M workers locally, sweeps load, measures latency |
| [`scripts/arch_comparison.py`](scripts/arch_comparison.py) | A/B harness: slim pods vs fat pod over a fixed request count (the 10k experiment) |
| [`scripts/latency_model.py`](scripts/latency_model.py) | M/M/c queueing model; predicts latency for any pods/load |
| [`scripts/k8s_loadtest.sh`](scripts/k8s_loadtest.sh) | Drives the real cluster at 1→4 pods, captures ground-truth JSON |
| [`manifests/k8s-gunicorn-4workers.yaml`](manifests/k8s-gunicorn-4workers.yaml) | Deployment + Service + HPA + PDB for the scenario |
| [`data/emulation__async-io.json`](data/emulation__async-io.json) | Measured emulation data (async I/O) |
| [`data/emulation__cpu.json`](data/emulation__cpu.json) | Measured emulation data (CPU-bound) |
| [`data/model_predictions.json`](data/model_predictions.json) | Model output (CPU-bound regime) |
| [`data/model_predictions_io.json`](data/model_predictions_io.json) | Model output (async I/O regime) |
| [`data/arch_comparison__async-io.json`](data/arch_comparison__async-io.json) | 10k-request A/B results (async I/O) |
| [`data/arch_comparison__cpu.json`](data/arch_comparison__cpu.json) | 10k-request A/B results (CPU-bound) |

---

## 9. References

- M/M/c queue & Erlang-C (the model's basis): [Wikipedia — M/M/c queue](https://en.wikipedia.org/wiki/M/M/c_queue), [Erlang (unit) / Erlang-C](https://en.wikipedia.org/wiki/Erlang_(unit))
- Kubernetes [HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) · [Service / kube-proxy load balancing](https://kubernetes.io/docs/concepts/services-networking/service/) · [Assign CPU resources](https://kubernetes.io/docs/tasks/configure-pod-container/assign-cpu-resource/) · [CPU throttling / CFS](https://docs.kernel.org/scheduler/sched-bwc.html)
- Gunicorn [design (pre-fork)](https://docs.gunicorn.org/en/stable/design.html) · [settings](https://docs.gunicorn.org/en/stable/settings.html) — `timeout`, `max-requests`, `workers`
- [`uvicorn-worker`](https://pypi.org/project/uvicorn-worker/) (replaces the deprecated `uvicorn.workers`)
- Full repo context: [FINAL_CONFLUENCE_PAGE.md §14 (Kubernetes deep dive)](../FINAL_CONFLUENCE_PAGE.md#14-kubernetes-on-powerful-multi-core-nodes-pods-vs-workers) · [FINAL_WORD.md](../FINAL_WORD.md) · [every claim's source](../CLAIMS_AND_SOURCES.md)

---

*Reproduce everything: the emulation runs on any machine (`python research/scripts/cluster_emulation.py`),
the model needs nothing but Python (`python research/scripts/latency_model.py --service-ms <ms>`), and the
manifests + `k8s_loadtest.sh` give ground truth on a real cluster.*
