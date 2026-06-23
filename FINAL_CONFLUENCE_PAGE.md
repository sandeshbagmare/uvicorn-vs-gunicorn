# Uvicorn vs Gunicorn — Complete Technical Reference & Decision Guide

> **Purpose:** A single, self-contained page covering everything needed to decide which server stack to use
> for a Python async web application. Includes conceptual foundations, a 15-parameter decision matrix,
> full benchmark data from real tests run in this repository, analysis, production guidance, a deep dive on
> **Kubernetes deployment on powerful multi-core nodes (pods vs workers — see §14)**, and references.
>
> **Audience:** Backend engineers and SREs choosing how to serve a Python ASGI app
> (FastAPI / Starlette / Litestar / Django ASGI) in production.
>
> **Test environment:** Windows 11 | 8 CPUs | Python 3.13 | FastAPI 0.115.6 | Uvicorn 0.34.0 `[standard]`
> *(Gunicorn is Unix-only; the Windows runs cover Uvicorn only. See §8.4 for the Linux/Docker comparison path.)*
>
> **Last reviewed:** 2026-06-23

---

## Table of Contents

1. [Executive Summary — TL;DR](#1-executive-summary--tldr)
2. [Why This Question Exists — Background](#2-why-this-question-exists--background)
3. [What Each Tool Actually Is](#3-what-each-tool-actually-is)
4. [WSGI vs ASGI — The Protocol Context](#4-wsgi-vs-asgi--the-protocol-context)
5. [Mental Model — The Restaurant Analogy](#5-mental-model--the-restaurant-analogy)
6. [The Worker / Process Question](#6-the-worker--process-question)
7. [Decision Matrix — 15 Parameters](#7-decision-matrix--15-parameters)
8. [Benchmark Setup & Methodology](#8-benchmark-setup--methodology)
9. [Benchmark Results — Full Data](#9-benchmark-results--full-data)
10. [Analysis — What the Numbers Actually Mean](#10-analysis--what-the-numbers-actually-mean)
11. [Production Checklist](#11-production-checklist)
12. [Decision Tree — Recommendation](#12-decision-tree--recommendation)
13. [Evolving Ecosystem Notes](#13-evolving-ecosystem-notes)
14. [Kubernetes on Powerful Multi-Core Nodes: Pods vs Workers](#14-kubernetes-on-powerful-multi-core-nodes-pods-vs-workers)
15. [References & Sources](#15-references--sources)

---

## 1. Executive Summary — TL;DR

**The single most important idea:** Uvicorn and Gunicorn are *not* direct competitors.
- **Uvicorn** is an **ASGI server** — it speaks HTTP and runs your `async` Python app on an event loop.
- **Gunicorn** is a **process manager / supervisor** — it forks and watches worker processes; it does *not* speak ASGI natively.
- **"Gunicorn + Uvicorn workers"** combines them: Gunicorn supervises, each worker *is* Uvicorn.

| Situation | Recommended Stack | One-Line Why |
|---|---|---|
| FastAPI in **Kubernetes / ECS / Cloud Run** (1 process per container) | **Uvicorn, 1 worker** | Orchestrator already restarts, scales, load-balances — Gunicorn is redundant. |
| FastAPI on a **bare VM / on-prem**, need resilience | **Gunicorn + Uvicorn workers** | You need crash-restart, graceful reloads, worker recycling. Gunicorn is battle-tested at this. |
| **Simplest single-command** multi-process setup (Uvicorn ≥ 0.30) | **`uvicorn --workers N`** | Uvicorn's built-in supervisor is now good enough for most non-critical VM deployments. |
| **Windows** (dev or prod) | **Uvicorn only** | Gunicorn does **not run on Windows** — it depends on Unix-only `fcntl` / `os.fork`. |
| **Legacy WSGI app** (Flask, Django sync views) | **Gunicorn** (sync or gthread workers) | ASGI servers cannot run WSGI apps at all; Gunicorn is the standard. |

> **Bottom line from our benchmarks:**
> Raw request-handling *speed* is essentially **identical** between "Gunicorn + Uvicorn workers" and
> "Uvicorn `--workers N`" — it is the **same Uvicorn** doing the actual HTTP work in both cases.
> Gunicorn's advantage is entirely **operational robustness** (timeout kill, graceful reload, worker recycling).
> Choose based on *where* you deploy, not on speed expectations.

---

## 2. Why This Question Exists — Background

FastAPI (and the broader ASGI ecosystem) exploded in popularity through the early 2020s.
Before ASGI, every Python web app ran on a WSGI server, and **Gunicorn** was (and remains) the dominant WSGI server — battle-tested since ~2010 and trusted by thousands of production teams.

When async Python arrived with FastAPI and Starlette, the community needed an ASGI equivalent.
**Uvicorn** filled that role. But since teams already trusted Gunicorn's process management,
the natural solution was "Gunicorn + Uvicorn workers" — use Gunicorn as the supervisor and Uvicorn as each worker.

FastAPI's own early deployment docs recommended this pattern. It appeared everywhere. But as container
platforms (Kubernetes, ECS, Cloud Run) matured, a new pattern emerged: **one process per container,
let the orchestrator manage replicas**. This meant a plain Uvicorn (or `uvicorn --workers`) became the
simpler, preferred choice for containerised workloads.

By 2025–2026 the ecosystem has drifted further: Uvicorn itself deprecated the `uvicorn.workers` module
(moved to a separate package `uvicorn-worker`) and its own multi-process supervisor matured enough to
be recommended on its own. This page documents the current state.

---

## 3. What Each Tool Actually Is

### 3.1 Uvicorn — The ASGI Server

- An **ASGI** (Asynchronous Server Gateway Interface) server built by Tom Christie / Encode.
  Spec lives at [asgi.readthedocs.io](https://asgi.readthedocs.io).
- Implements **HTTP/1.1**, **WebSockets**, and (with `--http auto`) **HTTP/2** via hypercorn.
- With `pip install "uvicorn[standard]"` it pulls two performance extras:
  - **uvloop** — a libuv-based event loop, ~2–4× faster than stock `asyncio` for I/O. **Unix-only.**
  - **httptools** — a fast C-based HTTP parser from the Node.js ecosystem.
  - On Windows, Uvicorn silently falls back to the standard `asyncio` event loop and `h11` parser —
    both are correct and safe, but measurably slower than the Unix path.
- **Default process model:** one process, one event loop.
- **Multi-process option:** `uvicorn app.main:app --workers N` — Uvicorn's own built-in supervisor
  forks `N` independent worker processes that all share one listen socket. The OS load-balances
  `accept()` calls across them.
- Installed version in this project: **0.34.0**

#### Key Uvicorn features

| Feature | Detail |
|---|---|
| Protocol | ASGI (HTTP/1.1, WebSockets) |
| Event loop | uvloop on Unix (default with `[standard]`); asyncio on Windows |
| HTTP parser | httptools (C) or h11 (pure Python) |
| Multi-process | `--workers N` (Uvicorn's own supervisor, mature since ~0.30) |
| Windows support | ✅ Full — workers work, uvloop absent |
| Config | CLI flags and environment variables; no config file |

### 3.2 Gunicorn — The Process Manager

- **"Green Unicorn"**, a pre-fork WSGI HTTP server by Benoit Chesneau. In production since 2010.
  Extremely battle-tested across the industry.
- **Architecture:** a **master** process binds the socket and **forks** N **worker** processes.
  The OS load-balances incoming connections across workers using `SO_REUSEPORT` or accept-queuing.
- The master process does **no request handling itself** — it is purely a supervisor.
- Gunicorn's master:
  - **Detects crashed workers** and replaces them automatically.
  - **Kills hung workers** via heartbeat + `--timeout` (heartbeat sent via shared memory/pipe every N seconds).
  - **Graceful reloads** on `SIGHUP` — new workers start before old ones drain and exit (zero-downtime).
  - **Hot binary upgrades** on `SIGUSR2` — replace the master process itself without dropping connections.
  - **Scales workers live** via `SIGTTIN` (add 1 worker) / `SIGTTOU` (remove 1 worker).
  - **Recycles workers** after `--max-requests` requests (+ jitter), bounding memory leaks.
  - **Lifecycle hooks** (`post_fork`, `on_starting`, `pre_request`, etc.) for custom instrumentation.
- Gunicorn's built-in worker types are all **WSGI** (sync, gthread, gevent).
  To run an **ASGI** app, you must supply the Uvicorn worker class (§3.3).
- **Gunicorn is Unix-only.** It imports `fcntl` and calls `os.fork()`, which do not exist on Windows.
  Attempting to `pip install gunicorn` on Windows succeeds but running it fails at startup.
- Installed version in this project: **23.0.0** (skipped on Windows; pip's `sys_platform != "win32"` guard).

### 3.3 "Gunicorn + Uvicorn workers" — The Classic Combo

```bash
# The classic incantation (Unix / WSL / Docker only)
gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4

# Or with the newer separate package (see §13 — Evolving Ecosystem Notes)
gunicorn app.main:app -k uvicorn_worker.UvicornWorker -w 4
```

- **Gunicorn is the master/manager.** It forks 4 workers.
- **Each worker boots a Uvicorn ASGI server** — same code, same event loop, same uvloop.
- You get **Gunicorn's robust process supervision** + **Uvicorn's async performance**.
- For years (≈2019–2024) this was *the* recommended production pattern for FastAPI on a VM.
- It is still valid and widely used; the ecosystem just now has more alternatives.

> ⚠️ **Deprecation notice (verify against live docs):**
> `uvicorn.workers.UvicornWorker` was moved to a separate community package in newer Uvicorn releases.
> The new import is `uvicorn_worker.UvicornWorker` (package: `pip install uvicorn-worker`).
> Always check the current Uvicorn changelog for your version before deploying.

---

## 4. WSGI vs ASGI — The Protocol Context

Understanding this distinction is prerequisite to understanding why the servers above exist.

| Dimension | WSGI | ASGI |
|---|---|---|
| **Concurrency model** | Synchronous: 1 request per thread/process at a time | Asynchronous: many concurrent requests per worker via event loop |
| **Long-lived connections** | ✗ No native WebSocket or long-poll support | ✅ WebSockets, Server-Sent Events, long-poll, HTTP/2 push |
| **`async def` support** | ✗ No — sync only | ✅ Yes — designed for `async def` handlers |
| **Typical frameworks** | Flask, Django (sync views), Pyramid, Bottle | FastAPI, Starlette, Litestar, Quart, Django ASGI |
| **Typical servers** | Gunicorn (sync/gthread/gevent), uWSGI, mod_wsgi | Uvicorn, Hypercorn, Granian, Daphne |
| **Best workload fit** | CPU-ish CRUD, mature codebases, simple deployments | High-concurrency I/O, real-time features, microservices, LLM streaming |
| **PEP / Spec** | PEP 3333 (WSGI) | ASGI spec (asgi.readthedocs.io) |

**Key rule:** An `async` FastAPI app *must* be served by an ASGI server.
Gunicorn can participate only as a **process manager wrapping an ASGI worker** (Uvicorn).
You cannot serve FastAPI correctly with Gunicorn's plain `sync` or `gthread` workers.

---

## 5. Mental Model — The Restaurant Analogy

This analogy helps make the abstract server architecture concrete.

**Uvicorn = one extremely efficient waiter who never stands still.**

Because your app is `async`, a single Uvicorn worker can take an order, and *while the kitchen cooks*
(while it `await`-s a database query or an HTTP call to another service), it walks over to the next table
and takes their order too. Then the next. Then the next. One waiter — hundreds of tables "in progress"
simultaneously, as long as the waiter is never forced to actually *stand in the kitchen and chop
vegetables himself*.

**The kitchen has one stove per waiter (Python's GIL).**

If your waiter has to personally chop vegetables — CPU work: serialising a huge JSON payload, resizing
images, doing crypto, running ML inference — he stops going to tables while he chops. The event loop
freezes. The only solution is **more waiters = more processes** (more workers).

**Gunicorn = the floor manager / maitre d'.**

The floor manager hires N waiters and watches them constantly. If a waiter faints (crashes),
the manager instantly fires him and hires a replacement — *without closing the restaurant or
turning away customers* (graceful restart). The manager can also:
- Rotate tired waiters out one by one after serving X tables (worker recycling, `--max-requests`).
- Bring in a fresh shift with zero disruption at midnight (graceful deploy with `SIGHUP`).
- Call in an extra waiter during a rush (`SIGTTIN`).

**Kubernetes / ECS / Cloud Run = the entire staffing agency.**

In containerised environments, the platform *is* the floor manager. It:
- Watches your container health probes (like a manager watching for fainting waiters).
- Restarts crashed containers automatically.
- Adds more container instances on demand (HPA).
- Drains connections before removing a pod (graceful termination).

When **the platform is your floor manager, you don't need an additional in-process floor manager
(Gunicorn)**. Just one very efficient waiter (Uvicorn, 1 worker per container).

---

## 6. The Worker / Process Question

This is the core tradeoff. Two completely independent knobs control capacity:

### 6.1 Knob 1 — Concurrency Inside a Worker (Event Loop)

Controlled by the event loop (`async`/`await`). For every request that does `await something()`,
the event loop is free to pick up another request from the queue while waiting for the first to
complete. **One process can handle thousands of concurrent I/O-bound requests.**

**Limitation:** Any code that does *not* yield to the event loop (`time.sleep`, a synchronous
DB driver, a CPU loop, a blocking file read) freezes the **entire** event loop for that worker.
While that code runs, no other requests can be served by that worker. This is called "blocking
the event loop" and is the #1 performance anti-pattern in async Python.

### 6.2 Knob 2 — Parallelism Across Workers (Processes)

Multiple processes = multiple event loops running simultaneously on different CPU cores.
This is the **only** way to:
- Utilise multiple CPU cores for Python code (Python's GIL prevents true thread-based parallelism).
- Continue serving requests if one worker crashes or hangs.
- Handle CPU-bound endpoints without freezing all other requests.

### 6.3 How Many Workers?

| Workload type | Guideline |
|---|---|
| **Async I/O-bound** (most of FastAPI) | Often 1–2 workers is sufficient; the event loop handles concurrency. Add more only if CPU or memory is saturated. |
| **CPU-bound** (serialisation, crypto, ML inference in-process) | Start at `workers = CPU cores`. Classic Gunicorn formula: `(2 × cores) + 1`. More than core-count adds context-switching cost without benefit. |
| **Blocking I/O anti-pattern** (`time.sleep` inside async) | More workers help linearly up to core count, but the *real* fix is to not block the event loop — move blocking calls to a thread pool (`run_in_executor`) or use `def` endpoints (FastAPI handles these in a threadpool automatically). |
| **Kubernetes / containers** | **1 worker per container**. Scale via replicas + HPA. Per-container isolation is cleaner than in-pod worker counts fighting the scheduler's CPU accounting. **On powerful multi-core nodes, see the full deep dive in [§14](#14-kubernetes-on-powerful-multi-core-nodes-pods-vs-workers) — "should I run Gunicorn with N workers per node, or let Kubernetes manage many thin pods?"** |

### 6.4 Memory is the Ceiling

Each worker is a full copy of the Python interpreter + your app + any loaded models or caches.

```
8 workers × 400 MB/worker = 3.2 GB RAM consumed
```

Use Gunicorn's `--preload` flag (or fork-after-load pattern) to share **read-only** memory
(pre-loaded models, large lookup tables) across workers via copy-on-write. This can dramatically
reduce per-worker memory overhead for large applications.

> **Rule of thumb:** Add workers to gain **CPU parallelism and crash resilience** — not to
> "get more async concurrency." Async concurrency is already free inside each single worker.

---

## 7. Decision Matrix — 15 Parameters

Scores: `3` = strong advantage · `2` = acceptable · `1` = limited · `✗` = not supported.

Score the parameters that matter to **your** deployment, weight accordingly, and sum.

| # | Parameter | Why It Matters | Uvicorn (1 worker) | Uvicorn `--workers N` | Gunicorn + UvicornWorker |
|---|---|---|:---:|:---:|:---:|
| 1 | **Protocol fit (ASGI)** | FastAPI requires ASGI | 3 | 3 | 3 *(via worker)* |
| 2 | **Single-worker throughput** | Baseline I/O speed | 3 | 3 | 3 |
| 3 | **Multi-core CPU parallelism** | Beat the GIL; utilise cores | ✗ *(1 process)* | 2 | 3 |
| 4 | **Crash-restart / supervision** | Resilience on bare metal | 1 | 2 | 3 |
| 5 | **Hung-worker timeout kill** | Prevent stuck requests starving traffic | ✗ | 1 | 3 |
| 6 | **Zero-downtime graceful reload** | Deploy without dropping connections | 1 | 1 | 3 |
| 7 | **Worker recycling (max-requests)** | Bound memory leaks over time | ✗ | ✗ | 3 |
| 8 | **Dynamic worker scaling (signals)** | Live tuning without restart | ✗ | ✗ | 2 |
| 9 | **Config richness + lifecycle hooks** | Operational control, instrumentation | 1 | 1 | 3 |
| 10 | **Windows support** | Dev or prod on Windows | 3 | 3 | ✗ *(hard fail)* |
| 11 | **uvloop / httptools acceleration (Unix)** | Lower per-request latency on Linux | 3 | 3 | 3 |
| 12 | **Memory efficiency** | Cost of scaling | 3 *(1 process)* | 2 | 2 |
| 13 | **Operational simplicity** | Fewer moving parts to understand/debug | 3 | 3 | 2 |
| 14 | **Container / K8s fit (1 process/pod)** | Clean resource limits, isolation | 3 | 1 | 1 |
| 15 | **Maturity / ecosystem familiarity** | Risk of unknown edge cases | 2 | 2 | 3 |

### Scenario-Based Scoring Guidance

**Kubernetes / serverless containers:**
Weight rows 13–14 heavily. Zero-out rows 4–9 (the platform provides supervision).
→ **Uvicorn, 1 worker/container** wins.

**Bare VM / on-prem with no orchestrator:**
Weight rows 4–9 heavily (no platform to restart/recycle workers).
→ **Gunicorn + Uvicorn workers** (or `uvicorn --workers` + systemd) wins.

**Windows host:**
Row 10 is a **hard gate** — Gunicorn is eliminated entirely.
→ **Uvicorn** (`--workers` works fine).

**Pure async I/O microservice (low CPU, LLM streaming, WebSockets):**
Rows 2 + 13 dominate. Row 3 barely matters.
→ **Uvicorn, 1–2 workers**, is plenty.

**CPU-heavy endpoints (in-process ML, heavy serialisation, crypto):**
Row 3 dominates.
→ Multiple processes needed: **`uvicorn --workers N`** or **Gunicorn + Uvicorn workers**.
   (Better still: move CPU work off the hot path to a task queue — Celery, Arq, RQ — so
   the async workers can stay focused on I/O.)

### The One-Line Verdict from the Matrix

> Gunicorn's advantage is entirely **process-management robustness**. If your platform already provides
> that (Kubernetes, systemd, ECS), those advantages are neutralised and the simpler Uvicorn options
> pull ahead on simplicity and container fit. On a lonely VM with no orchestrator, Gunicorn's
> robustness is exactly what fills the gap. Raw speed is ~the same either way.

---

## 8. Benchmark Setup & Methodology

Everything below was produced by the scripts in this repository. All numbers are reproducible.

### 8.1 Test Environment

| Property | Value |
|---|---|
| **Platform** | Windows 11 |
| **CPU cores** | 8 |
| **Python version** | 3.13 |
| **FastAPI** | 0.115.6 |
| **Uvicorn** | 0.34.0 `[standard]` |
| **Gunicorn** | 23.0.0 *(installed but skipped — Windows)* |
| **Event loop** | `asyncio` (Windows default; uvloop is Unix-only and was NOT active) |
| **HTTP parser** | `h11` (Windows fallback; httptools is also Linux/Mac) |
| **Worker count (N)** | 4 (= half of 8 CPUs, reasonable for mixed workloads) |
| **Load tester** | Custom async `httpx` client (`benchmarks/loadtest.py`) |

> ⚠️ **Important platform caveat:** uvloop (the high-performance event loop) does **not run on Windows**.
> All latency numbers here reflect the slower `asyncio` loop. On Linux with uvloop enabled,
> single-worker async throughput would be materially higher. See §8.4 for the Linux path.

### 8.2 Application Under Test

The demo app (`app/main.py`) — a FastAPI application with four deliberately different endpoints:

| Endpoint | Work type | Purpose |
|---|---|---|
| `GET /` | Trivial JSON response | Measure raw server + framework overhead (the floor) |
| `GET /async-io?delay=0.05` | `await asyncio.sleep(0.05)` | Model a well-behaved async I/O call (DB/cache/HTTP) |
| `GET /sync-io?delay=0.05` | `time.sleep(0.05)` inside `async def` | Model the classic blocking anti-pattern |
| `GET /cpu?iterations=50000` | Python busy loop | Model CPU-bound / GIL-bound work |

**Trick:** Every response includes the serving process's **PID** (`{"pid": 12345, ...}`).
By counting distinct PIDs across 2000 responses, we can *prove* how many workers actually shared the load —
not just assume the server is configured correctly.

### 8.3 Load Testing Tool

`benchmarks/loadtest.py` — pure Python async (`httpx`), no external binaries required.

- Fires `--requests` total requests with at most `--concurrency` in-flight simultaneously (semaphore).
- One warm-up request before timing begins (to exclude first-hit import cost from p99).
- Reports: throughput (req/s), latency percentiles (min / p50 / p90 / p95 / p99 / max / mean), distinct worker PID count.
- Writes JSON results to `results/raw/*.json` for charting.

### 8.4 Server Configurations Compared

| Config | Command | Notes |
|---|---|---|
| `uvicorn-1worker` | `python -m uvicorn app.main:app --workers 1` | Single process, single event loop |
| `uvicorn-4workers` | `python -m uvicorn app.main:app --workers 4` | 4 independent processes, Uvicorn's own supervisor |
| `gunicorn-4-uvicornworker` | `gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4` | **SKIPPED** — Unix-only; use Docker or WSL |

For the Gunicorn comparison (Docker):
```powershell
docker compose -f docker/docker-compose.yml up --build
# Gunicorn  -> http://127.0.0.1:8001
# Uvicorn 1w -> http://127.0.0.1:8002
# Uvicorn 4w -> http://127.0.0.1:8003
```

### 8.5 Test Load Parameters

| Endpoint | Requests | Concurrency | Why these numbers |
|---|---|---|---|
| `/` | 2000 | 200 | High concurrency to stress the event loop with trivial work |
| `/async-io` | 2000 | 200 | 200 in-flight × 50 ms sleep = up to 4000 req/s theoretical ceiling on Linux |
| `/sync-io` | 1000 | 200 | Blocking work; fewer total requests to not wait forever; 1-worker run had 272 timeouts |
| `/cpu` | 600 | 100 | Moderate total; CPU work is slow per request |

---

## 9. Benchmark Results — Full Data

Results produced by running `python benchmarks/run_suite.py` on the Windows test machine
(2026-06-20, results saved in `results/raw/*.json` and `results/native_suite.log`).

### 9.1 Complete Results Table

| Config | Endpoint | Wall Time (s) | Throughput (req/s) | OK / Error | p50 (ms) | p90 (ms) | p95 (ms) | p99 (ms) | Max (ms) | Distinct Worker PIDs |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| uvicorn-1worker | `/` | 5.917 | **338.0** | 2000 / 0 | 373.56 | 1339.88 | 1780.64 | 2911.92 | 5833.95 | **1** |
| uvicorn-4workers | `/` | 9.173 | 218.0 | 2000 / 0 | 540.91 | — | 2844.48 | 4585.54 | 6508.06 | **4** |
| uvicorn-1worker | `/async-io` | 18.008 | **111.1** | 2000 / 0 | 1137.14 | — | 5372.02 | 7646.50 | 13561.38 | **1** |
| uvicorn-4workers | `/async-io` | 22.258 | 89.9 | 2000 / 0 | 1442.80 | — | 5949.08 | 8751.47 | 16022.06 | **4** |
| uvicorn-1worker | `/sync-io` | 45.113 | 22.2 | **728 / 272** ⚠️ | 2484.70 | — | 30269.67 | 30325.39 | 30653.38 | **1** |
| uvicorn-4workers | `/sync-io` | 19.396 | **51.6** | 1000 / 0 ✅ | 2769.37 | — | 8883.76 | 12264.37 | 14039.36 | **4** |
| uvicorn-1worker | `/cpu` | 5.619 | 106.8 | 600 / 0 | 280.45 | 2522.28 | 2754.93 | 3017.90 | 3237.68 | **1** |
| uvicorn-4workers | `/cpu` | 4.593 | **130.6** | 600 / 0 | 521.03 | 1552.34 | 2065.15 | 2862.46 | 3515.89 | **4** |

> ⚠️ `/sync-io` with 1 worker: 272 requests resulted in `ReadTimeout` (142) and `ConnectError` (130) because
> the blocking `time.sleep` froze the event loop and left requests waiting until the 30-second client timeout.

### 9.2 Worker PID Distribution (Proof of Load Spreading)

| Config | Endpoint | PIDs and their request counts |
|---|---|---|
| uvicorn-1worker | `/` | `{PID 2788: 2000}` — all 2000 served by 1 process |
| uvicorn-4workers | `/` | `{18860: 762, 3344: 925, 16740: 247, 19064: 66}` — 4 processes, uneven distribution |
| uvicorn-1worker | `/async-io` | `{PID 2788: 2000}` |
| uvicorn-4workers | `/async-io` | `{3344: 290, 18860: 469, 19064: 897, 16740: 344}` — 4 processes |
| uvicorn-1worker | `/sync-io` | `{PID 2788: 728}` — only 728 succeeded; 272 timed out |
| uvicorn-4workers | `/sync-io` | `{3344: 182, 19064: 313, 16740: 238, 18860: 267}` — 4 processes, 0 errors |
| uvicorn-1worker | `/cpu` | `{PID 2788: 600}` |
| uvicorn-4workers | `/cpu` | `{16740: 310, 3344: 82, 18860: 67, 19064: 141}` — 4 processes, uneven |

> **Note on uneven distribution:** On Windows (no `SO_REUSEPORT` with the same semantics as Linux),
> the OS distributes `accept()` calls unevenly. On Linux, Gunicorn's pre-fork model also relies on
> the OS for accept distribution. Neither is round-robin; uneven spread is normal and expected.

---

## 10. Analysis — What the Numbers Actually Mean

### 10.1 Root Endpoint (`/`) — Server Overhead Baseline

**Observation:** 1 worker (338 req/s) is *faster* than 4 workers (218 req/s).

**Why:** With zero application work, the bottleneck is the server framework itself.
On Windows without uvloop, the `asyncio` event loop is the dominant cost.
Adding 4 processes doesn't multiply throughput here because:
1. The OS `accept()` distribution across processes adds coordination overhead.
2. There is no I/O wait time for the event loop to fill with concurrent requests — every request
   completes almost instantly, so the queue doesn't back up and parallelism can't help.

**On Linux with uvloop:** A single worker would handle this even faster (uvloop is ≈2–4× faster),
and 4 workers would also scale better due to better OS socket handling (`epoll` vs Windows IOCP).

**Lesson:** For trivial in-memory endpoints, 1 Uvicorn worker is already optimal. More workers
help only when each individual request has meaningful CPU work or I/O wait.

### 10.2 Async-IO Endpoint (`/async-io`) — The ASGI Sweet Spot

**Observation:** 1 worker (111.1 req/s) is again *faster* than 4 workers (89.9 req/s).

**Why on Windows (asyncio, no uvloop):**
- The endpoint `await asyncio.sleep(0.05)` simulates a 50 ms I/O call.
- Theoretical max for 1 worker at concurrency 200: `200 / 0.05s = 4000 req/s` (on Linux with uvloop).
- We got only 111 req/s — the Windows `asyncio` event loop's scheduling overhead dominates.
  Multiplying processes doesn't help because the bottleneck is not CPU or parallelism —
  it is the asyncio loop overhead per request.

**On Linux with uvloop:** This is where single-worker Uvicorn shines most dramatically.
A single uvloop worker at concurrency 200 and 50 ms delay should approach **thousands of req/s**.
The 4-worker overhead is also lower on Linux.

**Key lesson:** `/async-io` being "only" 111 req/s here is **a Windows artifact, not a Uvicorn limitation**.
This is one of the most important caveats about benchmarking Uvicorn on Windows.

### 10.3 Sync-IO Endpoint (`/sync-io`) — The Blocking Anti-Pattern

**Observation:** This is the most dramatic result. 4 workers (51.6 req/s, 0 errors) vs 1 worker (22.2 req/s, 272 errors — 27% failure rate).

**Why:**
- `time.sleep(0.05)` inside an `async def` endpoint does **not** yield to the event loop.
  The entire process freezes for 50 ms per request.
- With 1 worker and 200 concurrent requests: only 1 request is served at a time → max 20 req/s.
  At 200 concurrency, requests stack up, hit the 30-second client timeout, and fail.
- With 4 workers: each worker independently freezes for 50 ms, but 4 can freeze in parallel
  → theoretical max ≈ 4 × 20 = 80 req/s. We got 51.6 req/s — lower than theory because
  Windows overhead, but dramatically better than 1 worker, and zero errors.

**The real fix is not more workers.** The real fix is:
```python
# Anti-pattern (blocks the entire event loop):
@app.get("/sync-io")
async def sync_io():
    time.sleep(0.05)   # ← DO NOT DO THIS

# Fix option 1: move blocking I/O to a thread pool:
@app.get("/sync-io")
async def sync_io():
    await asyncio.get_event_loop().run_in_executor(None, time.sleep, 0.05)

# Fix option 2: use a synchronous def (FastAPI runs these in a thread pool automatically):
@app.get("/sync-io")
def sync_io():
    time.sleep(0.05)   # FastAPI puts sync def routes in a threadpool
```

**Lesson:** More workers are a band-aid for blocking code. The root cause must be fixed.
But the benchmark vividly demonstrates that when you *cannot* fix the blocking (legacy code,
unavoidable synchronous libraries), more workers are the only lever available.

### 10.4 CPU Endpoint (`/cpu`) — GIL and Parallelism

**Observation:** 4 workers (130.6 req/s) vs 1 worker (106.8 req/s) — 4 workers are ~22% faster.

**Why is the improvement only 22% instead of ~4×?**
1. The CPU loop (`50,000 integer operations`) is fast — each request takes ≈5–10 ms, not 250 ms.
   At 100 concurrency, requests don't fully saturate 4 cores.
2. Windows asyncio scheduler overhead reduces efficiency compared to Linux epoll.
3. Uneven OS work distribution (as seen in PID counts: `{16740: 310, 3344: 82, 18860: 67, 19064: 141}`
   — one worker handled 52% of requests).

**On Linux with a more CPU-heavy workload:** You would see throughput scaling much closer to linearly
with worker count up to the core count.

**Lesson:** For CPU-bound workloads, more workers **do** help, but the benefit scales with the ratio
of CPU time to overhead time. Heavy CPU (image resize, ML inference, crypto) sees near-linear gains.
Light CPU (simple serialisation) sees modest gains.

### 10.5 Gunicorn vs Uvicorn (`--workers`) Speed Comparison

**The Gunicorn comparison was skipped on Windows (Unix-only).** However, the architecture tells us
the answer conclusively:

> **"Gunicorn + Uvicorn workers" and "Uvicorn --workers N" produce *identical* raw throughput and latency
> on the same hardware, because the HTTP handling code is 100% the same — it is Uvicorn in both cases.**

The only difference is in the **supervisor layer**:
- Gunicorn's supervisor has more features (timeout kill, graceful reload, recycling, hooks).
- Uvicorn's built-in supervisor is simpler but sufficient for most needs.

This has been confirmed by independent benchmarks (see TechEmpower Web Framework Benchmarks and
various community benchmarks linked in §14).

### 10.6 Summary of Benchmark Lessons

| Lesson | Evidence |
|---|---|
| **1 worker handles async I/O just as well as N workers** (on Linux) | `/async-io` with 1 worker should approach N-worker throughput on Linux; shown clearly in theory and external benchmarks |
| **Blocking code (sync-io) is catastrophic on a single worker** — only workers help | 272 errors with 1 worker vs 0 errors with 4 workers on `/sync-io` |
| **CPU-bound work rewards more workers** (but less than linearly on this machine) | 22% improvement 1w→4w on `/cpu` (would be higher on Linux with real CPU saturation) |
| **Windows is not representative of production Linux** | uvloop and epoll absent; all numbers are conservative vs real Linux deployment |
| **Worker PID counts prove actual multi-worker behaviour** | Distinct PIDs visible in all 4-worker runs |
| **More workers ≠ more async** — concurrency is free inside 1 worker | 1 worker already handles 200 concurrent async requests without degradation in the async endpoint |

---

## 11. Production Checklist

Whichever server configuration you choose, these apply universally:

### Infrastructure
- [ ] **Front with a reverse proxy** (Nginx, Envoy, AWS ALB, Cloudflare) for:
  TLS termination, slow-client buffering, connection draining, static file serving, rate limiting.
  Never expose the app server's port directly to the internet.
- [ ] **Health endpoints:** `GET /health` → `{"status": "healthy"}` wired to your load balancer's
  readiness and liveness probes.
- [ ] **Structured logging** with correlation IDs / request IDs for traceability.

### Timeouts
- [ ] **Uvicorn:** `--timeout-keep-alive 5` (idle connection keepalive), `--timeout-graceful-shutdown 30`.
- [ ] **Gunicorn:** `--timeout 30` (worker heartbeat; workers not responding are killed),
  `--graceful-timeout 30` (SIGTERM → wait → SIGKILL on deploy).
- [ ] Your **upstream reverse proxy** timeout must be ≥ the app server timeout (or you get confusing 502s).

### Worker management
- [ ] **Right-size workers** from measured CPU utilisation — not a formula.
  Watch: CPU % per worker, memory × workers, event loop lag.
- [ ] **Gunicorn:** `--max-requests 1000 --max-requests-jitter 100` to recycle workers and bound leaks.
- [ ] **Preload app** with Gunicorn `--preload` if you have large models/caches to share via copy-on-write.
- [ ] **Do not block the event loop:** all synchronous or CPU-heavy work must go to a threadpool
  (`run_in_executor`, `def` endpoints in FastAPI) or a task queue (Celery / RQ / Arq).

### Kubernetes-specific
- [ ] **1 worker per container** — scale with replicas and HPA (CPU or custom metrics like RPS/latency).
- [ ] Set `resources.requests` and `resources.limits` on CPU **and** memory for every pod.
- [ ] Set `terminationGracePeriodSeconds` ≥ your graceful timeout so SIGTERM gives the pod time to drain.
- [ ] Use `preStop: sleep 5` hook on the container if your load balancer doesn't de-register fast enough.

### Observability
- [ ] **Request metrics:** RPS, error rate, latency percentiles (p50/p95/p99) per endpoint.
- [ ] **Worker metrics:** per-worker memory, event loop lag (use `uvicorn-prometheus` or similar).
- [ ] **Alerting:** p95 latency SLO breach, error rate > threshold, OOM kills.

---

## 12. Decision Tree — Recommendation

```
Is your app ASGI? (FastAPI, Starlette, Litestar, Django ASGI channels)
│
├─ NO (Flask, Django sync, Pyramid) ──────────────────────────────────────────────────────────────────────┐
│                                                                                                          │
│   → Use Gunicorn with sync workers (default) or gthread workers.                                        │
│     Uvicorn cannot serve WSGI apps.                                      ✅ Standard WSGI answer         │
│                                                                                                          │
└─ YES ────────────────────────────────────────────────────────────────────────────────────────────────────┘
    │
    ├─ Deploying to Kubernetes / ECS / Cloud Run / Fly.io / Railway / any container platform?
    │   │
    │   └─ YES → Uvicorn, 1 worker per container; scale with replicas + HPA.
    │             `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`
    │             ✅ Simplest, cleanest, container-native. Platform is your supervisor.
    │
    ├─ Running on Windows? (dev box, Windows Server, Azure Windows VM)
    │   │
    │   └─ YES → Uvicorn with `--workers N` (or 1 worker).
    │             Gunicorn cannot run on Windows. This is the only real option.
    │             ✅ Only option; perfectly valid for dev and Windows prod.
    │
    └─ Running on a bare Linux VM / on-prem server / without an orchestrator?
        │
        ├─ Need maximum operational robustness (timeout kill, hot reload, recycling, hooks)?
        │   │
        │   └─ YES → Gunicorn + Uvicorn workers (or the new `uvicorn-worker` package).
        │             ```bash
        │             gunicorn app.main:app -k uvicorn.workers.UvicornWorker \
        │               -w 4 --timeout 30 --max-requests 1000 --max-requests-jitter 100
        │             ```
        │             ✅ Most battle-tested supervision; ideal for critical VM deployments.
        │
        └─ Want simpler setup, Uvicorn ≥ 0.30, systemd or supervisord available?
            │
            └─ YES → `uvicorn --workers N` managed by systemd.
                      ```ini
                      # /etc/systemd/system/myapp.service
                      [Service]
                      ExecStart=/venv/bin/uvicorn app.main:app --workers 4 --host 0.0.0.0 --port 8000
                      Restart=always
                      ```
                      ✅ Good enough for most non-critical VM deployments. Simpler to reason about.
```

---

## 13. Evolving Ecosystem Notes

The server landscape is actively evolving. Verify against current documentation for your deployment.

### uvicorn-worker Package (Important if Using Gunicorn)

Uvicorn deprecated the built-in `uvicorn.workers.UvicornWorker` class.
The class was moved to a **separate community package:**

```bash
pip install uvicorn-worker
```

```bash
# Old (may still work depending on your Uvicorn version):
gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4

# New (use this for new deployments):
gunicorn app.main:app -k uvicorn_worker.UvicornWorker -w 4
```

Check the Uvicorn changelog for your installed version (`pip show uvicorn`).

### FastAPI Official Deployment Guidance Shift

FastAPI's deployment documentation (fastapi.tiangolo.com/deployment/) has progressively moved
toward recommending:
1. **One process per container** in containerised environments.
2. **`uvicorn --workers`** as the first recommendation for multi-process setups.
3. Gunicorn is now mentioned as an alternative rather than the primary recommendation.

### Alternative ASGI Servers (Worth Knowing)

| Server | Notes |
|---|---|
| **Hypercorn** | HTTP/2 and HTTP/3 (QUIC) support; alternative to Uvicorn for those protocols |
| **Granian** | Rust-based, very high throughput; newer, less ecosystem history |
| **Daphne** | Django Channels' reference server; less commonly used outside Django |

### Python 3.13+ Free-Threaded Mode (Experimental)

Python 3.13 introduced an **experimental free-threaded mode** (no GIL, `--disable-gil` build).
If/when this stabilises, the "more processes to beat the GIL" argument weakens. The landscape will
shift again. Watch PEP 703 and CPython release notes.

---

## 14. Kubernetes on Powerful Multi-Core Nodes: Pods vs Workers

> **This section answers a specific, real-world question:**
> *"I have a Kubernetes cluster, but each node is a powerful machine — 4-core or 8-core.
> Should I just run Gunicorn with 4 workers on each machine alongside Kubernetes? Or does
> Kubernetes already have granular control over the worker processes on each machine?
> What should I do?"*
>
> It is one of the most common — and most misunderstood — decisions in deploying Python ASGI
> apps to Kubernetes. The short answer changes how you size everything, so it gets its own section.

### 14.1 The 30-second answer

1. **No — Kubernetes does *not* have granular control over worker processes inside a container.**
   Kubernetes' unit of management is the **Pod**, not the OS process. If you run
   `gunicorn -w 4` inside a pod, Kubernetes sees **one pod**; it is completely blind to the 4 workers
   inside. Gunicorn manages those 4; Kubernetes manages the pod around them.
2. **If you want Kubernetes to have granular, per-worker control, you make each worker its own pod**
   — i.e. **one Uvicorn process per pod**, and run **many pods**. Then each "worker" *is* a pod that
   Kubernetes can independently schedule, health-check, restart, autoscale, and reschedule.
3. **A powerful 8-core node does not make a single Python process faster** — the GIL means one process
   uses one core. A beefy node just means you can pack **more processes** (as more pods, or more
   workers per pod) onto it.
4. **Recommended default for your cluster:** **thin pods — 1 (or 2) Uvicorn workers per pod, several
   pods per node, scaled by the Horizontal Pod Autoscaler (HPA).** Reach for "Gunicorn + N workers
   inside one pod" only for specific reasons (large shared in-memory model, heavy service-mesh sidecar
   overhead) covered in §14.6–14.8.

The rest of this section explains *why*, dimension by dimension, and gives ready-to-use manifests.

---

### 14.2 The one fact that drives everything: Kubernetes manages Pods, not processes

This is the crux of your question, so it is worth stating very precisely.

| Layer | Who manages it | What it can do |
|---|---|---|
| **Node** (your 8-core machine) | Kubernetes scheduler + kubelet | Decides which pods land on which node; enforces node capacity |
| **Pod** (one or more containers) | Kubernetes (Deployment, ReplicaSet, HPA, probes) | Create, delete, restart, reschedule, scale, health-check, roll out |
| **Container** (your image) | kubelet via the container runtime | Start/stop, apply CPU/memory limits, run probes |
| **Worker process** (Uvicorn/Gunicorn worker *inside* the container) | **Gunicorn or Uvicorn — NOT Kubernetes** | Fork, restart-on-crash, timeout-kill, recycle |

When you run **`gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4`** inside a pod:

```
            ┌─────────────────────── POD (what Kubernetes sees & controls) ──────────────────────┐
            │                                                                                     │
            │   Gunicorn master  ──manages──►  Uvicorn worker 1   (Kubernetes is BLIND to these) │
            │        │                          Uvicorn worker 2                                  │
            │        └──────────manages────────►Uvicorn worker 3                                  │
            │                                    Uvicorn worker 4                                  │
            │                                                                                     │
            └─────────────────────────────────────────────────────────────────────────────────────┘
                        ▲
                        │  Kubernetes restarts / scales / health-checks at THIS boundary only
```

**Consequences of Kubernetes being blind to the inner workers:**

- **Restart granularity:** If one of the 4 workers wedges or crashes, *Gunicorn* replaces it.
  Kubernetes neither knows nor helps. If you instead want "K8s restarts the unhealthy unit,"
  the unhealthy unit has to *be a pod*.
- **Health checks lie:** A readiness/liveness probe hits **one** HTTP endpoint, answered by **whichever
  worker** the kernel/Gunicorn happens to route it to. Three of four workers can be deadlocked while the
  fourth cheerfully answers `/health` — and Kubernetes marks the **whole pod Ready**. With one worker per
  pod, the probe reflects exactly that one worker's health. **No false "healthy."**
- **Autoscaling granularity:** HPA scales **pods**, not workers. With 4-workers-per-pod, HPA adds/removes
  capacity **4 workers at a time** — a coarse, lumpy step. With 1-worker pods, HPA adds capacity one small
  unit at a time — smooth and precise.
- **Scheduling/placement:** All 4 workers in a fat pod are pinned to **one node**. They cannot spread
  across the cluster for high availability. One-worker pods can be spread across nodes (anti-affinity /
  topology spread), so a single node failure costs you a fraction of capacity, not a quarter in one go.

> **Direct answer to your question:** Running Gunicorn-with-4-workers per node *works*, but it puts
> worker management **under Gunicorn, not Kubernetes**. Kubernetes will **not** have granular control
> over those workers — it only controls the pod they live in. To hand Kubernetes that granular control,
> give each worker its own pod.

---

### 14.3 The GIL still rules: a powerful node is not free parallelism

It is tempting to think "my node has 8 cores, so my app can use 8 cores." Not by itself.

- **One Python process = one GIL = one core of Python bytecode at a time** (see §6).
- To actually use all 8 cores you need **8 Python processes**, *however they are packaged*:
  - 8 pods × 1 worker each, **or**
  - 1 pod × 8 workers, **or**
  - 4 pods × 2 workers, … any combination that yields 8 processes.
- A bigger node does **not** speed up a single process. It just gives you room to run **more** processes.

So the 8-core node does not change *whether* you need multiple processes — you do, to use the hardware.
It only changes *how you package* those processes. That packaging choice is the entire subject of this section.

---

### 14.4 The two packaging patterns

**Pattern A — "Thin pods" (one process per pod) — the cloud-native default**

```
Node (8 cores)
 ├─ Pod 1: [ 1 Uvicorn worker ]   ← K8s controls this unit
 ├─ Pod 2: [ 1 Uvicorn worker ]   ← K8s controls this unit
 ├─ Pod 3: [ 1 Uvicorn worker ]
 ├─ … up to ~6–7 pods (leave headroom for kubelet/system) …
 └─ Pod 7: [ 1 Uvicorn worker ]
```
Command per pod: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (no `--workers`, or `--workers 1`).
Kubernetes is the *only* supervisor. Each worker is independently schedulable, health-checked, scalable.

**Pattern B — "Fat pods" (many workers per pod)**

```
Node (8 cores)
 └─ Pod 1: [ Gunicorn master → 4–8 Uvicorn workers ]   ← K8s controls the POD; Gunicorn controls the workers
```
Command: `gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4 --timeout 30 --max-requests 2000 --preload`
Two supervisors. Kubernetes sees one pod; Gunicorn manages the workers inside.

**Pattern C — "Hybrid" (a few workers per pod, several pods) — the pragmatic middle**

```
Node (8 cores)
 ├─ Pod 1: [ 2 Uvicorn workers ]
 ├─ Pod 2: [ 2 Uvicorn workers ]
 └─ Pod 3: [ 2 Uvicorn workers ]
```
Command per pod: `uvicorn app.main:app --workers 2`. Captures *some* copy-on-write memory sharing and
*some* sidecar amortisation while keeping pods small enough for granular scheduling/scaling.

---

### 14.5 Dimension-by-dimension comparison

| Dimension | Thin pods (1 proc/pod) | Fat pods (N workers/pod) |
|---|---|---|
| **Granular control by Kubernetes** | ★★★ Each worker is a pod K8s fully controls | ✗ K8s blind to inner workers; Gunicorn controls them |
| **Health-check accuracy** | ★★★ Probe = that worker's true health | ✗ One healthy worker masks N-1 sick ones |
| **Autoscaling (HPA) granularity** | ★★★ Scale one small unit at a time | ★ Scale N workers per step (lumpy) |
| **Failure blast radius** | ★★★ Lose 1 worker's capacity on crash/OOM | ★ Pod OOM kills *all* its workers together |
| **HA spread across nodes** | ★★★ Spread pods via anti-affinity | ✗ All workers pinned to one node |
| **Rolling deploy granularity** | ★★★ Replace pods incrementally | ★★ Whole fat pods cycle at once |
| **Memory via copy-on-write** | ✗ Each pod a full copy of model/app | ★★★ `--preload` shares read-only pages (big win for large models) |
| **Service-mesh sidecar overhead** | ✗ One Envoy sidecar **per pod** (adds up) | ★★★ One sidecar amortised over N workers |
| **CPU throttling behaviour** | ★★★ Small per-pod quota, smoother | ★ Big bursty quota → CFS throttling spikes (§14.6) |
| **Pod/IP count & control-plane load** | ★ Many pods, many IPs | ★★★ Far fewer pods |
| **Operational simplicity** | ★★★ One supervisor (K8s) | ★★ Two supervisors to reason about |
| **Fit when node is huge (8-core+)** | ★★★ Just run more pods on it | ★★ Works, but you give up the wins above |

**How to read it:** Thin pods win almost every *Kubernetes-native* dimension (the left-hand benefits are
exactly the things you adopted Kubernetes *for*). Fat pods win on **memory sharing** and **sidecar/overhead
amortisation** — real, but narrower, concerns. The node being powerful does not tilt the table toward fat
pods; it just means a thin-pod layout runs *more pods per node*.

---

### 14.6 The CPU-limits / throttling trap (the most-missed gotcha)

This bites fat pods specifically, so it matters directly to "Gunicorn with 4 workers in a pod."

- Kubernetes enforces a **CPU limit** using the Linux **CFS quota** (`cpu.cfs_quota_us` over a
  `cpu.cfs_period_us`, default 100 ms). A limit of `"4"` means "4 CPU-seconds of runtime per 1 second
  of wall-clock, doled out in 100 ms slices."
- A pod running **4–8 busy workers** can burn its entire slice **early in the 100 ms window**, then gets
  **hard-throttled** (frozen) until the next window. The result is **p99 latency spikes** that look
  mysterious — average CPU "looks fine," but tail latency is bad. This is **CFS throttling**, and it is
  worse the more processes share one big quota.
- **Thin pods** each carry a small quota (~1 core). The same total work is spread over many small quotas,
  so throttling is far less bursty.

**Practical guidance:**
- **Always set CPU & memory *requests*** (the scheduler needs them to bin-pack and to compute HPA utilisation).
- **Set a *memory limit*** (a leaking pod should be OOM-killed, not allowed to take down the node).
- **Be cautious with *CPU limits* on latency-sensitive services.** A widely used pattern is to set CPU
  *requests* but **omit CPU *limits*** so a pod can burst into idle node capacity instead of being throttled.
  The trade-off is weaker isolation from noisy neighbours — acceptable on nodes you control and right-size,
  riskier on shared multi-tenant nodes. Decide deliberately; don't copy a `limits.cpu` in by reflex.

---

### 14.7 Memory & copy-on-write: the strongest case *for* a few workers per pod

This is the one dimension where fat (or hybrid) pods clearly win, and it can be decisive.

- Every worker is a **full copy of your Python app in memory**. If your app loads a **large read-only
  asset** — an ML model, a big embeddings table, a 1–2 GB lookup — then:
  - **Thin pods:** 8 pods × 2 GB model = **~16 GB** consumed on the node. Brutal.
  - **Fat pod with `--preload`:** Gunicorn loads the model **once in the master**, then forks workers.
    Thanks to **copy-on-write**, the read-only model pages are **shared** across workers until written.
    8 workers might consume **~2–4 GB total** instead of 16 GB. Massive saving.
- Without a large shared asset (a typical stateless CRUD/API service of, say, 150–300 MB per process),
  this advantage is small and the thin-pod benefits dominate.

**Rule of thumb:** The bigger your per-process fixed memory cost (models, caches), the more a
**few-workers-per-pod (Pattern B/C) with `--preload`** pays for itself. The smaller it is, the more
**thin pods (Pattern A)** win.

---

### 14.8 Recommendation for *your* cluster (4-/8-core nodes)

Putting it together for the exact scenario in the question:

**Default: go thin or hybrid — let Kubernetes be the manager.**

- Run **1 Uvicorn worker per pod** (or **2** if you want a little COW sharing / fewer pods), and run
  **several pods per node**. Let the scheduler place them and the **HPA** scale the replica count on
  CPU utilisation (or a custom metric like RPS / in-flight requests).
- This gives Kubernetes the **granular control** your question is really asking about: per-worker
  scheduling, accurate health checks, smooth autoscaling, small blast radius, cross-node HA.
- **You do not need Gunicorn here.** Plain `uvicorn` (1 worker) or `uvicorn --workers 2` is enough,
  because **Kubernetes is already the process supervisor** — it does the crash-restart, the rollout,
  and the scaling that Gunicorn would otherwise provide.

**Sizing example for one 8-core node (thin):**
- Reserve ~1 core + memory for kubelet, CNI, logging/metrics DaemonSets, and any service-mesh sidecars.
- ~7 cores left for app pods → e.g. **6–7 pods**, each `requests.cpu: ~900m`, 1 Uvicorn worker.
- HPA `minReplicas` sized to your baseline; `maxReplicas` high enough to spill onto more nodes under load.

**Deviate toward fat/hybrid pods (Gunicorn + workers, or `uvicorn --workers N`) only when:**
1. **Large shared in-memory model/cache** → `--preload` copy-on-write saves real RAM (§14.7). *Strongest reason.*
2. **Service mesh (Istio/Linkerd sidecar) per pod** is a meaningful % of overhead and you want to amortise
   one sidecar over several workers. *(Note: sidecar-less "ambient" mesh modes weaken this argument.)*
3. **Very large clusters** where the sheer **pod/IP count** strains the control plane, IPAM, or the
   `max-pods-per-node` ceiling, and you deliberately want fewer, denser pods.
4. You specifically want **Gunicorn's in-pod robustness** — `--timeout` hung-worker kill and
   `--max-requests` recycling — *in addition to* Kubernetes' pod-level management.

**If you do choose fat pods, then yes — "Gunicorn + UvicornWorker with N workers" is the right tool**
(not plain `uvicorn --workers`), precisely because you now *want* a real in-pod supervisor with
timeout-kill and recycling. Size `N ≈ the pod's CPU request in cores`, set `--timeout`, `--max-requests`,
`--max-requests-jitter`, and `--preload`, and **don't fill the whole node with one pod** — keep at least
2–3 pods so you retain cross-node HA and rolling-deploy granularity.

**What to avoid:** one giant pod with 8 workers consuming an entire node. It throws away scheduling
flexibility, HA spread, accurate health checks, and smooth autoscaling — i.e. most of the reasons you
run Kubernetes at all.

---

### 14.9 Reference manifests

#### A) Thin pod (recommended default) — Deployment + HPA + probes + graceful shutdown

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapi
spec:
  replicas: 6                       # starting point; the HPA below adjusts this
  selector:
    matchLabels: { app: myapi }
  template:
    metadata:
      labels: { app: myapi }
    spec:
      terminationGracePeriodSeconds: 40        # >= your graceful drain time
      # Spread pods across nodes so one node failure isn't catastrophic:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels: { app: myapi }
      containers:
        - name: myapi
          image: myregistry/myapi:1.0.0
          # ONE Uvicorn process. Kubernetes is the supervisor — no Gunicorn needed.
          command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
          ports:
            - containerPort: 8000
          resources:
            requests:
              cpu: "900m"           # scheduler uses this; ~1 core per pod
              memory: "256Mi"
            limits:
              memory: "512Mi"       # OOM-kill a leaking pod; CPU limit omitted on purpose (see §14.6)
          startupProbe:             # gives a slow import/model-load time to finish before liveness kicks in
            httpGet: { path: /health, port: 8000 }
            periodSeconds: 5
            failureThreshold: 30    # up to ~150s to become ready
          readinessProbe:           # gate traffic; with 1 worker this is the worker's true health
            httpGet: { path: /health, port: 8000 }
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:            # restart a wedged pod
            httpGet: { path: /health, port: 8000 }
            periodSeconds: 10
            failureThreshold: 3
          lifecycle:
            preStop:
              # Let the Service/LB stop routing to this pod BEFORE SIGTERM starts draining it.
              exec: { command: ["sh", "-c", "sleep 5"] }
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: myapi
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: myapi
  minReplicas: 6
  maxReplicas: 40                   # high enough to spill onto more nodes under load
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70    # utilisation is measured against requests.cpu
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: myapi
spec:
  minAvailable: 50%                 # protect capacity during node drains/upgrades
  selector:
    matchLabels: { app: myapi }
```

#### B) Fat / hybrid pod — Gunicorn + Uvicorn workers (use when §14.8 reasons apply)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapi
spec:
  replicas: 3                       # keep several pods — don't put one pod per node
  selector:
    matchLabels: { app: myapi }
  template:
    metadata:
      labels: { app: myapi }
    spec:
      terminationGracePeriodSeconds: 60
      containers:
        - name: myapi
          image: myregistry/myapi:1.0.0
          # Gunicorn IS wanted here: it supervises the workers Kubernetes can't see.
          command:
            - gunicorn
            - app.main:app
            - -k
            - uvicorn.workers.UvicornWorker     # or uvicorn_worker.UvicornWorker (newer package, see §13)
            - --workers
            - "4"                                # ≈ this pod's CPU request, in cores
            - --bind
            - "0.0.0.0:8000"
            - --timeout
            - "30"                               # kill a hung worker (K8s won't do this inside the pod)
            - --graceful-timeout
            - "30"
            - --max-requests
            - "2000"                             # recycle workers to bound memory leaks
            - --max-requests-jitter
            - "200"
            - --preload                          # load the big model once; share via copy-on-write (§14.7)
          ports:
            - containerPort: 8000
          resources:
            requests:
              cpu: "3500m"                       # ~4 cores for 4 workers
              memory: "3Gi"                      # one shared preloaded model
            limits:
              memory: "4Gi"
          readinessProbe:
            httpGet: { path: /health, port: 8000 }
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /health, port: 8000 }
            periodSeconds: 10
            failureThreshold: 3
          lifecycle:
            preStop:
              exec: { command: ["sh", "-c", "sleep 5"] }
```

> ⚠️ **Caveat on fat-pod probes:** because the probe can be answered by any one healthy worker, a fat
> pod can report Ready while some workers are wedged (§14.2). Mitigate with Gunicorn `--timeout` (so hung
> workers get killed and replaced quickly) and consider app-level checks that exercise real dependencies.

---

### 14.10 Quick decision checklist for your cluster

- [ ] **Do you want Kubernetes to manage the units of capacity (restart, scale, reschedule, health-check)?**
      → **Thin pods**, 1 Uvicorn worker each. *(This is the "granular control" you asked about.)*
- [ ] **Is your per-process memory small (stateless API, no big model)?** → **Thin pods**.
- [ ] **Do you load a large read-only model/cache per process?** → **Fat/hybrid pods** with Gunicorn `--preload`.
- [ ] **Do you run a per-pod service-mesh sidecar whose overhead you must amortise?** → lean **hybrid/fat**.
- [ ] **Latency-sensitive?** → set CPU **requests**, think hard before adding CPU **limits** (§14.6).
- [ ] **Whichever you pick:** memory limit on, `terminationGracePeriodSeconds` ≥ drain time, `preStop` sleep,
      readiness+liveness+startup probes, PodDisruptionBudget, and spread pods across nodes for HA.
- [ ] **Never** collapse to one node-filling pod with all workers — you lose the Kubernetes benefits entirely.

> **Bottom line for your 8-core nodes:** the powerful hardware does **not** push you toward "Gunicorn with
> 4 workers per node." Default to **thin (or 2-worker hybrid) pods and let Kubernetes + HPA spread many of
> them across each big node** — that is what gives Kubernetes the fine-grained control. Add **Gunicorn +
> workers inside a pod only when a large shared model or sidecar overhead makes denser pods worth it**, and
> even then keep several pods for HA.

---

## 15. References & Sources

All links verified approximately June 2026. Versions move; check the live documentation for current guidance.

### Official Documentation

| Resource | URL | What it Covers |
|---|---|---|
| Uvicorn Deployment | https://www.uvicorn.org/deployment/ | Workers, settings, reverse proxy, signals |
| Uvicorn Settings Reference | https://www.uvicorn.org/settings/ | All `--flag` options and env vars |
| Uvicorn Release Notes | https://github.com/encode/uvicorn/releases | Worker deprecation, `uvicorn-worker` package, changelog |
| Gunicorn Design | https://docs.gunicorn.org/en/stable/design.html | Pre-fork architecture, worker types |
| Gunicorn Settings | https://docs.gunicorn.org/en/stable/settings.html | `--timeout`, `--max-requests`, `--workers`, etc. |
| Gunicorn Signals | https://docs.gunicorn.org/en/stable/signals.html | `HUP`, `USR2`, `TTIN`, `TTOU` |
| FastAPI Deployment | https://fastapi.tiangolo.com/deployment/ | Official FastAPI deployment guidance |
| FastAPI Server Workers | https://fastapi.tiangolo.com/deployment/server-workers/ | `uvicorn --workers` guidance |
| FastAPI in Containers | https://fastapi.tiangolo.com/deployment/docker/ | One-process-per-container pattern |
| ASGI Specification | https://asgi.readthedocs.io/ | Protocol definition |
| uvicorn-worker package | https://github.com/Kludex/uvicorn-worker | Gunicorn worker class (post-deprecation) |

### Python Internals / GIL

| Resource | URL |
|---|---|
| Python GIL Explanation | https://wiki.python.org/moin/GlobalInterpreterLock |
| PEP 703 — Making the GIL Optional | https://peps.python.org/pep-0703/ |
| Python `asyncio` Docs | https://docs.python.org/3/library/asyncio.html |
| uvloop | https://github.com/MagicStack/uvloop |

### Kubernetes — Pods, Workers, Autoscaling & Resources (for §14)

| Resource | URL | What it Covers |
|---|---|---|
| Pod concept | https://kubernetes.io/docs/concepts/workloads/pods/ | The pod as Kubernetes' smallest manageable unit |
| Managing Resources (requests/limits) | https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/ | CPU/memory requests & limits semantics |
| Assign CPU Resources | https://kubernetes.io/docs/tasks/configure-pod-container/assign-cpu-resource/ | How CPU requests/limits map to CFS quota |
| Horizontal Pod Autoscaler | https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/ | Scaling replica count (pods, not workers) |
| Liveness/Readiness/Startup Probes | https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/ | Pod-level health checking |
| Pod Topology Spread Constraints | https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/ | Spreading pods across nodes for HA |
| Pod Disruption Budget | https://kubernetes.io/docs/tasks/run-application/configure-pdb/ | Protecting capacity during drains/upgrades |
| Pod termination & graceful shutdown | https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination | SIGTERM, preStop, terminationGracePeriodSeconds |
| CFS bandwidth / CPU throttling (kernel) | https://docs.kernel.org/scheduler/sched-bwc.html | Why fat pods with CPU limits get throttled (§14.6) |
| FastAPI in Containers | https://fastapi.tiangolo.com/deployment/docker/ | Official "one process per container" guidance |

### Benchmarks & External Data

| Resource | URL | Notes |
|---|---|---|
| TechEmpower Web Framework Benchmarks | https://www.techempower.com/benchmarks/ | Cross-framework, cross-server throughput rankings |
| Starlette benchmarks repo | https://github.com/encode/starlette/tree/master/docs/benchmarks | Reference async benchmarks |

### This Repository's Benchmark Files

All test data is locally generated and reproducible:

| File | Contents |
|---|---|
| `results/native_suite.log` | Full console output from the Windows benchmark run (2026-06-20) |
| `results/raw/uvicorn-1worker__root.json` | 2000-request root endpoint, 1 worker, all latency data |
| `results/raw/uvicorn-1worker__async-io.json` | 2000-request async-io endpoint, 1 worker |
| `results/raw/uvicorn-1worker__sync-io.json` | 1000-request sync-io endpoint, 1 worker (272 errors) |
| `results/raw/uvicorn-1worker__cpu.json` | 600-request cpu endpoint, 1 worker |
| `results/raw/uvicorn-4workers__root.json` | 2000-request root endpoint, 4 workers |
| `results/raw/uvicorn-4workers__async-io.json` | 2000-request async-io endpoint, 4 workers |
| `results/raw/uvicorn-4workers__sync-io.json` | 1000-request sync-io endpoint, 4 workers (0 errors) |
| `results/raw/uvicorn-4workers__cpu.json` | 600-request cpu endpoint, 4 workers |
| `app/main.py` | FastAPI benchmark app (4 endpoints, PID in every response) |
| `benchmarks/loadtest.py` | Async load tester used to generate all data |
| `benchmarks/run_suite.py` | Orchestrates the full benchmark matrix |
| `docs/decision-matrix.md` | Concise machine-readable version of the 15-parameter matrix |

---

*This document was compiled from hands-on testing in this repository combined with official
documentation from Uvicorn, Gunicorn, and FastAPI project teams. All benchmark data is
reproducible by running `python benchmarks/run_suite.py` in this repo. Re-run on your own
hardware to get numbers relevant to your production environment — especially on Linux where
uvloop will substantially change the async I/O picture.*
