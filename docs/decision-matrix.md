# Decision Matrix: Uvicorn vs Gunicorn (15 parameters)

Use this to score your own context. Weight the parameters that matter to *you*, then sum.
Legend: `3` = strong, `2` = ok, `1` = weak, `0`/`✗` = not supported.

| # | Parameter | Why it matters | Uvicorn (1 proc) | Uvicorn `--workers N` | Gunicorn + UvicornWorker |
|---|---|---|:--:|:--:|:--:|
| 1 | Protocol fit (ASGI) | FastAPI needs ASGI | 3 | 3 | 3 (via worker) |
| 2 | Single-worker throughput | baseline speed | 3 | 3 | 3 |
| 3 | Multi-core parallelism | beat the GIL | ✗ | 2 | 3 |
| 4 | Crash-restart supervision | resilience | 1 | 2 | 3 |
| 5 | Hung-worker timeout kill | stuck requests | ✗ | 1 | 3 |
| 6 | Zero-downtime graceful reload | deploys | 1 | 1 | 3 |
| 7 | Worker recycling (max-requests) | leak control | ✗ | ✗ | 3 |
| 8 | Dynamic worker scaling (signals) | live tuning | ✗ | ✗ | 2 |
| 9 | Config richness + lifecycle hooks | ops control | 1 | 1 | 3 |
| 10 | Windows support | dev/prod on Win | 3 | 3 | ✗ |
| 11 | uvloop acceleration (Unix) | perf | 3 | 3 | 3 |
| 12 | Memory efficiency | cost | 3 | 2 | 2 |
| 13 | Operational simplicity | fewer moving parts | 3 | 3 | 2 |
| 14 | Container / K8s fit (1 proc/pod) | cloud-native | 3 | 1 | 1 |
| 15 | Maturity / team familiarity | risk | 2 | 2 | 3 |

## Scoring guidance by scenario

**Kubernetes / serverless containers**
Weight rows 13–14 heavily, zero-out 4–9 (the platform does supervision).
→ **Uvicorn, 1 worker/container** usually wins.

**Bare VM / on-prem, no orchestrator**
Weight rows 4–9 heavily (you need a supervisor).
→ **Gunicorn + Uvicorn workers** (or `uvicorn --workers` + systemd) usually wins.

**Windows host (any reason)**
Row 10 is a hard gate.
→ **Uvicorn** is the only option; Gunicorn is eliminated.

**Pure async I/O microservice, low CPU**
Rows 2 + 13 dominate; multi-core (row 3) barely matters.
→ **Uvicorn** (1 or few workers) is plenty.

**CPU-heavy endpoints (serialization, crypto, ML inference in-process)**
Row 3 dominates.
→ Need multiple processes: **`uvicorn --workers N`** or **Gunicorn + workers**, sized to cores.
   (Better: move CPU work off the request path entirely.)

## The one-line verdict
> Gunicorn's entire advantage is **process-management robustness**. If your deployment platform
> already supplies that (Kubernetes, systemd, ECS), prefer **Uvicorn** for its simplicity and
> per-process isolation. If it doesn't (a lonely VM), **Gunicorn + Uvicorn workers** is the most
> battle-tested way to get it. Raw request-handling speed is ~the same either way — it's the same
> Uvicorn doing the work.
