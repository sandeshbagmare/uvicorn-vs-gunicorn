# Uvicorn vs Gunicorn — Dev Quick Guide

> ⏱️ **~7-min read.** The practical version for engineers shipping FastAPI/ASGI apps:
> what to use where, copy-paste commands, the one rule that matters, and links to the source for each claim.
> Want depth? → [FINAL_CONFLUENCE_PAGE.md](FINAL_CONFLUENCE_PAGE.md) · Want the verdict? → [FINAL_WORD.md](FINAL_WORD.md) · New here? → [BEGINNERS_GUIDE.md](BEGINNERS_GUIDE.md)

---

## TL;DR — pick your row, copy the command

| Your environment | ✅ Recommended | Command |
|---|---|---|
| **Kubernetes / containers** | **Uvicorn, 1 worker per pod** + replicas/HPA | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| **Bare Linux VM (max robustness)** | **Gunicorn + Uvicorn workers** | `gunicorn app.main:app -k uvicorn_worker.UvicornWorker -w 4` |
| **Bare Linux VM (simple)** | **Uvicorn `--workers` + systemd** | `uvicorn app.main:app --workers 4` |
| **Windows (dev or prod)** | **Uvicorn only** (Gunicorn can't run) | `uvicorn app.main:app --workers 4` |
| **Legacy WSGI (Flask/Django sync)** | **Gunicorn (sync workers)** | `gunicorn app:app -w 4` |

**The core truth:** Uvicorn and Gunicorn aren't competitors. [Uvicorn](https://www.uvicorn.org/) is the **ASGI server** (speaks HTTP, runs your async app). [Gunicorn](https://docs.gunicorn.org/en/stable/design.html) is a **process manager** (supervises workers). The combo = Gunicorn supervises Uvicorn workers. **Raw speed is identical** — same Uvicorn underneath; Gunicorn only adds operational robustness.

---

## 60-second mental model

- **Uvicorn** = one fast async worker. With `[standard]` it uses [uvloop + httptools](https://www.uvicorn.org/#installation) (Unix only — [uvloop isn't on Windows](https://github.com/MagicStack/uvloop/issues/352)).
- **Gunicorn** = a pre-fork master that restarts/recycles/reloads workers. [Unix only](https://github.com/benoitc/gunicorn/issues/524) (needs `fcntl`/`fork`).
- **The GIL** = [one Python process uses one core at a time](https://docs.python.org/3/glossary.html#term-global-interpreter-lock). To use 8 cores you need **8 processes** — packaged as more pods or more workers.
- **Async ≠ parallelism.** One worker already handles huge **I/O** concurrency via the [event loop](https://fastapi.tiangolo.com/async/); you add workers for **CPU** and **resilience**, not "more async."

---

## Recommended setups (with the *why*)

### ✅ Kubernetes — the default for most teams

Run **plain Uvicorn, one worker per container**, and scale with **replicas + [HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)**. This is also [FastAPI's official guidance](https://fastapi.tiangolo.com/deployment/server-workers/): *don't use `--workers` in K8s; run one Uvicorn process per container.*

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Why no Gunicorn?** Kubernetes already restarts, rolls, scales, and health-checks — at the **pod** level. But note: [Kubernetes manages pods, not processes](https://kubernetes.io/docs/concepts/workloads/pods/) — it can't see workers inside a multi-worker container. So one-process-per-pod gives K8s **granular control**.

> **"Won't 1 worker waste my 8-core node?"** No — **"1 worker per *pod*" ≠ "1 worker per *node*."** You run *many* pods per node. Size each pod to ~1 core (`requests.cpu: 900m`) and ~7 thin pods fill an 8-core box. The only real waste is giving a **single** worker a multi-core allocation — the GIL strands the extra cores. Full reasoning → [FINAL_WORD.md](FINAL_WORD.md).

Minimal Deployment + HPA essentials (full version in [§14.9](FINAL_CONFLUENCE_PAGE.md#149-reference-manifests)):

```yaml
resources:
  requests: { cpu: "900m", memory: "256Mi" }   # scheduler packs ~7 pods/8-core node
  limits:   { memory: "512Mi" }                # memory limit yes; CPU limit → throttling risk
readinessProbe: { httpGet: { path: /health, port: 8000 } }
livenessProbe:  { httpGet: { path: /health, port: 8000 } }
lifecycle: { preStop: { exec: { command: ["sh","-c","sleep 5"] } } }   # drain before SIGTERM
# + terminationGracePeriodSeconds: 40, HPA on CPU ~70%, PodDisruptionBudget, topologySpread
```

**Go multi-worker-per-pod only when:** you load a **large shared model** (use Gunicorn [`--preload`](https://docs.gunicorn.org/en/stable/settings.html#preload-app) → copy-on-write saves RAM) or a heavy **per-pod service-mesh sidecar** is worth amortising. Then use Gunicorn for in-pod `--timeout`/`--max-requests`, and still keep several pods.

### ✅ Bare Linux VM / on-prem

No orchestrator, so you need a supervisor. Most robust:

```bash
gunicorn app.main:app -k uvicorn_worker.UvicornWorker -w 4 \
  --timeout 30 --max-requests 2000 --max-requests-jitter 200
```

[`--timeout`](https://docs.gunicorn.org/en/stable/settings.html#timeout) kills hung workers, [`--max-requests`](https://docs.gunicorn.org/en/stable/settings.html#max-requests) recycles to bound leaks, [signals](https://docs.gunicorn.org/en/stable/signals.html) (`HUP`/`USR2`) do zero-downtime reloads. Simpler alternative: `uvicorn app.main:app --workers 4` behind **systemd** + **Nginx**.

### ✅ Windows

**Uvicorn only.** [Gunicorn cannot run on Windows](https://github.com/benoitc/gunicorn/issues/524) (`ModuleNotFoundError: fcntl`). `--workers` works fine. For a real Gunicorn comparison use **WSL** or the **Docker** setup in this repo. Note: [uvloop is absent on Windows](https://github.com/MagicStack/uvloop/issues/352), so async throughput is **slower than Linux** — develop on Windows, but tune for Linux.

### ✅ Legacy WSGI (Flask, classic Django)

```bash
gunicorn app:app -w 4              # sync/gthread workers — no Uvicorn; ASGI servers can't run WSGI
```

---

## How many workers?

| Workload | Rule |
|---|---|
| **CPU-bound** | `workers ≈ cores` (Gunicorn's classic [`(2 × cores) + 1`](https://docs.gunicorn.org/en/stable/design.html#how-many-workers)) |
| **I/O-bound async** | Few — 1 worker already handles high concurrency; scale on measured CPU |
| **Kubernetes** | **1 worker/pod**, pod sized ~1 core; scale **replicas**, not workers-in-a-pod |
| **Memory** | Each worker = a full copy of your app; 8 × 400 MB = 3.2 GB. Use `--preload` to share big read-only models via copy-on-write |

---

## The one rule that matters: never block the event loop

A blocking call inside `async def` freezes the **whole** worker. Our benchmark proves it: the blocking `/sync-io` endpoint on 1 worker **failed 272 / 1000 requests**; on 4 workers, **0 failures** ([data](results/raw/)).

```python
import asyncio, time

@app.get("/bad")
async def bad():
    time.sleep(0.05)            # ❌ freezes the event loop for everyone

@app.get("/good")
async def good():
    await asyncio.sleep(0.05)   # ✅ yields — one worker serves thousands concurrently

@app.get("/blocking-but-ok")
def blocking_ok():              # ✅ plain `def` → FastAPI runs it in a threadpool
    time.sleep(0.05)
```

Details: [FastAPI async docs](https://fastapi.tiangolo.com/async/). Use async libraries (`httpx`, not `requests`) or `run_in_executor` for unavoidable blocking work.

---

## Production must-haves (any setup)

- [ ] **Reverse proxy in front** (Nginx / cloud LB) for TLS, slow-client buffering, static files — never expose the app server raw.
- [ ] **`/health` endpoint** wired to LB / K8s probes.
- [ ] **Timeouts:** Gunicorn `--timeout`/`--graceful-timeout`; Uvicorn `--timeout-keep-alive`.
- [ ] **Recycle workers:** Gunicorn `--max-requests` + jitter.
- [ ] **Graceful shutdown:** handle SIGTERM; K8s `terminationGracePeriodSeconds` ≥ drain time + `preStop`.
- [ ] **Don't block the event loop** (see above).
- [ ] **K8s:** memory limit on, CPU limit cautious ([throttling](https://home.robusta.dev/blog/stop-using-cpu-limits)), 1 worker/pod, HPA, PodDisruptionBudget.

---

## Gotchas worth knowing

- ⚠️ **`uvicorn.workers.UvicornWorker` is deprecated** (since Uvicorn 0.30) → use the [`uvicorn-worker`](https://pypi.org/project/uvicorn-worker/) package: `uvicorn_worker.UvicornWorker`.
- ⚠️ **CPU limits cause CFS throttling** on multi-process pods even with spare cores — prefer setting **requests**, [be cautious with CPU limits](https://home.robusta.dev/blog/stop-using-cpu-limits).
- ⚠️ **Windows benchmarks understate Linux** — no uvloop. The async numbers are a conservative floor.
- ⚠️ **One worker can't beat the GIL** — a single process maxes at ~1 core for Python; add processes for CPU.

---

## Sources (curated)

**Official:** [Uvicorn deployment](https://www.uvicorn.org/deployment/) · [Gunicorn design](https://docs.gunicorn.org/en/stable/design.html) · [Gunicorn settings](https://docs.gunicorn.org/en/stable/settings.html) · [FastAPI server workers](https://fastapi.tiangolo.com/deployment/server-workers/) · [FastAPI in containers](https://fastapi.tiangolo.com/deployment/docker/) · [ASGI spec](https://asgi.readthedocs.io/) · [K8s HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) · [K8s probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)

**Packages/specs:** [uvicorn-worker (PyPI)](https://pypi.org/project/uvicorn-worker/) · [uvloop](https://github.com/MagicStack/uvloop) · [PEP 3333 (WSGI)](https://peps.python.org/pep-3333/) · [Python GIL](https://docs.python.org/3/glossary.html#term-global-interpreter-lock)

**Every claim → its source:** [CLAIMS_AND_SOURCES.md](CLAIMS_AND_SOURCES.md)

---

*Reproduce the benchmarks: [README.md](README.md). This guide is the short version — the full reference is [FINAL_CONFLUENCE_PAGE.md](FINAL_CONFLUENCE_PAGE.md).*
