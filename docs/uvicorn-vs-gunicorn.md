# Uvicorn vs Gunicorn for Production — A Practical, Tested Comparison

> **Audience:** backend engineers and SREs choosing how to run a Python ASGI app
> (FastAPI / Starlette / Litestar) in production.
> **Status:** living document. Numbers in the *Benchmark Results* section come from the
> reproducible scripts in this repo (`benchmarks/`) — re-run them on your own hardware.
> **Last reviewed:** 2026-06-20.

---

## 1. TL;DR (read this first)

| If you are… | Use | Why |
|---|---|---|
| Running FastAPI in **Kubernetes / ECS / Cloud Run** (one process per container) | **Uvicorn**, 1 worker per container | The orchestrator already restarts, scales and load-balances. A second process manager (Gunicorn) is redundant. |
| Running on a **bare VM / on-prem host** and want one resilient process tree | **Gunicorn + Uvicorn workers** *(or* `uvicorn --workers`*)* | You need a supervisor for crash-restart, graceful reloads, worker recycling. Gunicorn is the most battle-tested option. |
| On a **single box, want simplest setup**, modern Uvicorn (≥0.30) | **`uvicorn --workers N`** | Uvicorn's own multi-process manager is now good enough for most cases. |
| On **Windows** (dev or prod) | **Uvicorn** (`--workers` works) | **Gunicorn does not run on Windows at all** (needs the Unix-only `fcntl`). |
| Serving a **legacy WSGI app** (Flask/Django sync) | **Gunicorn** (sync/gthread workers) | ASGI servers don't run WSGI apps; Gunicorn is the standard WSGI server. |

**The single most important idea:** Uvicorn and Gunicorn are *not* really competitors.
Uvicorn is the **ASGI server** (it speaks HTTP and runs your async app on an event loop).
Gunicorn is a **process manager / pre-fork master** (it supervises worker processes).
"Gunicorn **with** Uvicorn workers" combines them: Gunicorn supervises, each worker *is* a Uvicorn.

---

## 2. The 30-second mental model ("the easy-to-picture version")

Picture a restaurant.

- **Uvicorn = one waiter who never stands still.** Because the app is `async`, a single Uvicorn
  worker takes an order, and *while the kitchen cooks* (an `await` on a DB/HTTP call) it goes and
  takes the next table's order. One waiter can juggle hundreds of tables **as long as the work is
  waiting, not chopping vegetables himself.** This is the event loop.
- **The kitchen has one stove per waiter (the GIL).** If a waiter has to *personally* chop
  vegetables (CPU work — JSON of a huge payload, image resize, crypto), he stops taking orders.
  The only fix is **more waiters = more processes.**
- **Gunicorn = the floor manager.** It hires N waiters, watches them, and if one faints
  (crashes or hangs) it fires him and hires a replacement *without closing the restaurant*. It can
  also rotate waiters out after N tables (memory-leak hygiene) and bring in a new shift with zero
  downtime (graceful reload).

So:
- More **async I/O** → one Uvicorn worker already scales well.
- More **CPU** or you want **resilience** → you want **multiple processes** and **a manager**.
- Gunicorn is the manager; Uvicorn workers are the waiters. In Kubernetes, **Kubernetes is the
  manager**, so you often just need the waiter (plain Uvicorn).

---

## 3. What each tool actually is

### 3.1 Uvicorn — the ASGI server
- An **ASGI** (Asynchronous Server Gateway Interface) server built by Encode. It implements HTTP/1.1
  and WebSockets and drives your `async def` app on an event loop.
- With `pip install "uvicorn[standard]"` it pulls **uvloop** (a libuv-based event loop, much faster
  than stock `asyncio`) and **httptools** (a fast C HTTP parser). **uvloop is Unix-only** — on
  Windows Uvicorn silently falls back to the standard `asyncio` loop, which is slower.
- Process model: **one process, one event loop** by default. `--workers N` starts Uvicorn's own
  built-in multiprocess supervisor that spawns N independent worker processes behind one socket.

### 3.2 Gunicorn — the process manager (WSGI master)
- "Green Unicorn", a **pre-fork** WSGI HTTP server, in production since ~2010 and extremely battle-tested.
- A **master** process binds the socket and forks **worker** processes; the OS load-balances accepts
  across workers. The master:
  - **restarts** workers that crash or **hang** (heartbeat + `--timeout`),
  - does **graceful reloads** (`HUP`) and **hot binary upgrades** (`USR2`),
  - **recycles** workers after `--max-requests` (bounds memory leaks),
  - **scales** workers up/down via signals (`TTIN`/`TTOU`),
  - exposes lifecycle **hooks** (`post_fork`, `on_starting`, …).
- Gunicorn itself only speaks **WSGI** (sync). To serve an **ASGI** app you give it the
  **Uvicorn worker class** so each worker is actually a Uvicorn instance (next section).
- **Gunicorn is Unix-only.** It imports `fcntl`/uses `os.fork`, which don't exist on Windows.

### 3.3 "Gunicorn + Uvicorn workers" — the classic combo
```bash
gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4
```
- Gunicorn is the master/manager; each of the 4 workers boots a Uvicorn ASGI server.
- You get **Gunicorn's process supervision** + **Uvicorn's async speed**. For years this was *the*
  recommended way to run FastAPI on a VM.

> ⚠️ **Evolving guidance (verify against current docs):** Recent Uvicorn versions **deprecated the
> built-in `uvicorn.workers` module**, moving the worker class to a separate community package
> (`uvicorn-worker`, imported as `uvicorn_worker.UvicornWorker`). In parallel, FastAPI's own
> deployment docs now lead with **`uvicorn --workers`** and **"one process per container, let the
> orchestrator manage replicas"**, rather than Gunicorn. The combo still works and is fine; just
> know the ecosystem is drifting toward "Uvicorn alone + an orchestrator." See *References*.

---

## 4. WSGI vs ASGI (why this whole thing exists)

| | WSGI | ASGI |
|---|---|---|
| Concurrency model | **synchronous**, 1 request per worker/thread at a time | **asynchronous**, many concurrent requests per worker via an event loop |
| Long-lived connections | ✗ (no native WebSockets) | ✓ WebSockets, SSE, long-poll |
| Typical frameworks | Flask, Django (classic), Pyramid | FastAPI, Starlette, Litestar, Django ASGI |
| Servers | Gunicorn (sync/gthread/gevent), uWSGI | Uvicorn, Hypercorn, Daphne, Granian |
| Best for | CPU-ish request/response apps, mature ecosystems | high-concurrency I/O, real-time, microservices |

**Key takeaway:** an `async` FastAPI app *must* be served by an ASGI server. Gunicorn participates
only as a process manager wrapping an ASGI worker (Uvicorn). You cannot serve FastAPI with
Gunicorn's plain `sync` workers.

---

## 5. The worker / process question (the heart of "more workers, good?")

Two independent knobs:

1. **Concurrency inside a worker** — handled by the event loop. Great for `await`-ed I/O; *useless*
   for CPU work or accidentally-blocking calls (those freeze the whole loop).
2. **Parallelism across workers** — more **processes**. The only way to use multiple CPU cores
   (Python's **GIL** lets one process run Python bytecode on one core at a time) and the only way to
   keep serving if one worker is busy/blocked/crashed.

### How many workers?
- **CPU-bound-ish workloads:** start at **`workers = CPU cores`** (or Gunicorn's classic
  `(2 × cores) + 1`). More than that just adds context-switching and memory.
- **I/O-bound async workloads:** you often need **far fewer** workers than the formula suggests,
  because one worker already handles huge concurrency. Sometimes `cores` or even fewer is plenty;
  scale on measured CPU saturation, not a formula.
- **Memory is the ceiling:** each worker is a full copy of your app/model. 8 workers × 400 MB =
  3.2 GB. Use Gunicorn `--preload` (or fork-after-load) to share read-only memory where possible.
- **In Kubernetes:** prefer **1 worker per container** and scale with **replicas + HPA**. It gives
  per-process isolation, clean autoscaling, and simpler resource limits. (Running N workers *inside*
  a pod fights the scheduler's CPU accounting.)

> **Rule of thumb:** add workers to get **CPU parallelism and resilience**, not to get
> "more async." Async concurrency you already have for free inside one worker.

---

## 6. Decision matrix — 15 parameters

Score each row for your context. (`★` = better/stronger; this is qualitative — your mileage varies.)

| # | Parameter | Uvicorn (standalone) | Uvicorn `--workers N` | Gunicorn + Uvicorn workers |
|---|---|---|---|---|
| 1 | **Protocol** | ASGI (async) | ASGI (async) | ASGI via worker; Gunicorn core is WSGI |
| 2 | **Raw single-worker throughput** | ★★★ (uvloop+httptools) | ★★★ | ★★★ (same Uvicorn under the hood) |
| 3 | **Multi-core / parallelism** | ✗ (1 process) | ★★ (own supervisor) | ★★★ (mature pre-fork) |
| 4 | **Process supervision / crash-restart** | ✗ minimal | ★★ improved, newer | ★★★ battle-tested since 2010 |
| 5 | **Hung-worker detection (timeout kill)** | ✗ | partial | ★★★ `--timeout` heartbeat |
| 6 | **Graceful reload (zero-downtime deploy)** | basic | basic | ★★★ `HUP`, `USR2` hot upgrade |
| 7 | **Worker recycling (`max-requests`)** | ✗ | ✗ | ★★★ leak mitigation |
| 8 | **Dynamic worker scaling (signals)** | ✗ | ✗ | ★★ `TTIN`/`TTOU` |
| 9 | **Config richness / lifecycle hooks** | basic CLI/env | basic | ★★★ config file + hooks |
| 10 | **Windows support** | ★★★ works | ★★★ works | ✗ **does not run on Windows** |
| 11 | **uvloop speed boost** | ★★★ (Unix) | ★★★ (Unix) | ★★★ (Unix) |
| 12 | **Memory per extra unit of capacity** | n/a (1 proc) | one process each | one process each (same) |
| 13 | **Operational simplicity** | ★★★ one process | ★★★ one command | ★★ two layers to reason about |
| 14 | **Fit for containers/K8s (1 proc/pod)** | ★★★ ideal | ★ (redundant manager) | ✗ (redundant manager) |
| 15 | **Maturity / ecosystem familiarity** | ★★ | ★★ | ★★★ everyone knows Gunicorn |

**How to read it:** Gunicorn's wins (rows 4–9) are all about **process-management robustness**.
If your platform (Kubernetes, systemd, supervisord, ECS) already provides that, those wins are
neutralized and the simpler Uvicorn options (rows 13–14) pull ahead. On a lonely VM with no
orchestrator, Gunicorn's robustness is exactly what you want.

A machine-readable version of this table lives in [decision-matrix.md](decision-matrix.md).

---

## 7. Benchmark methodology

Everything here is reproducible with this repo. See [README.md](../README.md) for setup.

- **App:** `app/main.py` — FastAPI with four endpoints exercising different work shapes:
  - `/` trivial JSON (server+framework overhead floor)
  - `/async-io` `await asyncio.sleep(0.05)` (well-behaved async I/O)
  - `/sync-io` `time.sleep(0.05)` inside async (the **blocking anti-pattern**)
  - `/cpu` a busy loop (CPU-bound, GIL-bound)
  - Every response includes the serving **process id**, so we can *prove* how many workers shared the load.
- **Load tool:** `benchmarks/loadtest.py` — async `httpx` client, fixed total requests with a
  concurrency cap, reports throughput + latency percentiles (p50/p90/p95/p99) + per-PID distribution.
- **Server configs compared:**
  1. `uvicorn --workers 1`
  2. `uvicorn --workers N`
  3. `gunicorn -k uvicorn.workers.UvicornWorker -w N` *(Linux/WSL/Docker — skipped on Windows)*
- **Driver:** `benchmarks/run_suite.py` boots each config, runs the workload matrix (incl. the
  **1000-requests-in-parallel** case), and tears the process tree down between runs.

What we expect to see (hypotheses the benchmark tests):
- **`/async-io`:** even **1 Uvicorn worker** sustains high throughput at high concurrency — the event
  loop keeps thousands of awaits in flight. Extra workers help only modestly.
- **`/cpu`:** throughput scales **roughly with worker count** up to core count, because only more
  processes beat the GIL. 1 worker is the floor.
- **`/sync-io`:** throughput is capped at `≈ workers / delay` regardless of concurrency, because the
  blocking call freezes each worker's loop. Adding workers is the *only* thing that helps — a vivid
  demo of why you must not block the event loop.
- **`/cpu` Uvicorn-N vs Gunicorn-N:** comparable throughput (same Uvicorn underneath); Gunicorn's
  advantage is **operational** (restart/timeout/recycle), not raw speed.

---

## 8. Benchmark results

> _Populated by running `python benchmarks/run_suite.py`. If this section still shows the
> placeholder, run the suite on your hardware and paste the comparison table the script prints.
> Do not trust hand-wavy numbers — the whole point of this repo is that you can generate your own._

**Environment:** _<fill: OS, CPU model, cores, Python version, package versions>_

| Config | Endpoint | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Distinct worker PIDs |
|---|---|---:|---:|---:|---:|---:|
| uvicorn-1worker | /async-io | _tbd_ | _tbd_ | _tbd_ | _tbd_ | 1 |
| uvicorn-Nworkers | /async-io | _tbd_ | _tbd_ | _tbd_ | _tbd_ | N |
| gunicorn-N | /async-io | _tbd_ | _tbd_ | _tbd_ | _tbd_ | N |
| uvicorn-1worker | /cpu | _tbd_ | _tbd_ | _tbd_ | _tbd_ | 1 |
| uvicorn-Nworkers | /cpu | _tbd_ | _tbd_ | _tbd_ | _tbd_ | N |
| gunicorn-N | /cpu | _tbd_ | _tbd_ | _tbd_ | _tbd_ | N |
| uvicorn-1worker | /sync-io | _tbd_ | _tbd_ | _tbd_ | _tbd_ | 1 |
| uvicorn-Nworkers | /sync-io | _tbd_ | _tbd_ | _tbd_ | _tbd_ | N |

Charts (after `python benchmarks/plot_results.py`): `results/charts/*.png`.

---

## 9. Production checklist (whichever you pick)

- [ ] **Front it with a real reverse proxy** (Nginx / Envoy / cloud LB) for TLS, slow-client
      buffering, timeouts, and static files. Never expose the app server raw to the internet.
- [ ] **Set timeouts:** Gunicorn `--timeout`/`--graceful-timeout`; Uvicorn `--timeout-keep-alive`.
- [ ] **Recycle workers:** Gunicorn `--max-requests` + `--max-requests-jitter` to bound leaks.
- [ ] **Right-size workers** from measured CPU, not a formula; watch memory × workers.
- [ ] **Don't block the event loop:** offload CPU/blocking calls to a threadpool (`def` endpoints,
      `run_in_executor`) or a task queue (Celery/RQ/Arq). The `/sync-io` benchmark shows why.
- [ ] **Health checks:** `/health` wired to your LB/orchestrator readiness+liveness probes.
- [ ] **Graceful shutdown:** ensure SIGTERM drains in-flight requests (K8s `terminationGracePeriod`).
- [ ] **Observability:** structured logs, request metrics (RPS, p95/p99), per-worker memory.
- [ ] **In K8s:** 1 worker/container, set CPU/memory requests+limits, use HPA on CPU or RPS.

---

## 10. Recommendation (decision tree)

```
Is your app ASGI (FastAPI/Starlette)?
├─ No  → it's WSGI (Flask/Django sync) → use Gunicorn (sync/gthread). (Uvicorn can't serve it.)
└─ Yes
   ├─ Deploying to Kubernetes / a container platform?
   │   └─ Yes → Uvicorn, 1 worker per container; scale with replicas + HPA.  ✅ simplest, isolated
   ├─ On Windows?
   │   └─ Yes → Uvicorn (--workers works). Gunicorn can't run here.          ✅ only real option
   └─ On a bare VM / on-prem, need a resilient process tree?
       ├─ Want max-proven supervision (timeout-kill, hot reload, recycling)?
       │   └─ Gunicorn + Uvicorn workers (or the new uvicorn-worker pkg).    ✅ most robust
       └─ Want simplest single-command setup, modern Uvicorn ≥0.30?
           └─ uvicorn --workers N behind systemd/Nginx.                      ✅ good enough now
```

---

## 11. References

> Verify these against the live docs — versions move. (This list is checked during the repo's
> research pass; see commit notes for the verification date.)

- Uvicorn docs — deployment, settings, `--workers`, the `uvicorn[standard]` extras (uvloop/httptools).
- Uvicorn changelog/release notes — deprecation of `uvicorn.workers`; the `uvicorn-worker` package.
- Gunicorn docs — design (pre-fork), settings (`timeout`, `max-requests`, `graceful-timeout`),
  signal handling, "Gunicorn cannot run on Windows."
- FastAPI docs — "Deployment / Server Workers" and "FastAPI in Containers"; current guidance on
  `uvicorn --workers` and one-process-per-container.
- Python GIL / multiprocessing background.
- TechEmpower Web Framework Benchmarks — cross-framework/server throughput context.
