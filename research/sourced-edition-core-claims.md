# Sourced Edition — the core Uvicorn vs Gunicorn claims, line by line

> **Format:** Every factual line ends with an inline source tag `[S#]`, resolved in [Sources](#sources).
> Click the tag → open the exact page → verify the line. Lines marked *(ours)* are our own
> synthesis/illustration, not external claims. This is the line-traceable companion to the narrative
> docs ([FINAL_CONFLUENCE_PAGE.md](../FINAL_CONFLUENCE_PAGE.md), [DEV_GUIDE.md](../DEV_GUIDE.md),
> [BEGINNERS_GUIDE.md](../BEGINNERS_GUIDE.md)); the prose there is the same claims, unbroken by tags.
>
> **Verified:** 2026-06-24.

---

## 1. What ASGI and WSGI are

- WSGI (Web Server Gateway Interface) is the Python standard interface between web servers and synchronous Python web applications, defined in PEP 3333. [S1]
- ASGI (Asynchronous Server Gateway Interface) is the spiritual successor to WSGI, providing an async-capable interface for Python web servers, frameworks, and applications. [S2]
- ASGI is designed to support long-lived connections such as WebSockets, which WSGI cannot natively handle. [S2]
- An async FastAPI/Starlette app therefore needs an ASGI server; a plain WSGI server cannot run it. [S3] *(framework requirement, per FastAPI's deployment docs)*

## 2. What Uvicorn is

- Uvicorn is an ASGI web server implementation for Python. [S4]
- The `uvicorn[standard]` install pulls in optional extras including `uvloop` and `httptools`. [S5]
- `uvloop` is a fast, drop-in replacement for the asyncio event loop, built on libuv, that *"makes asyncio 2-4x faster."* [S6]
- `uvloop` does not run on Windows — it is POSIX-only; attempts to use it on Windows historically raise `RuntimeError: uvloop does not support Windows`. [S7]
- Uvicorn supports running multiple worker processes via the `--workers` option. [S8]

## 3. What Gunicorn is

- Gunicorn is a Python WSGI HTTP server using a pre-fork worker model: a master process manages a set of worker processes. [S9]
- Gunicorn's `--timeout` causes the master to kill and restart workers that are silent for more than the configured number of seconds (i.e. hung). [S10]
- Gunicorn's `--max-requests` restarts each worker after it has handled a set number of requests, which helps limit the damage of memory leaks. [S10]
- Gunicorn's `--preload` loads the application code before forking workers, enabling read-only memory to be shared between them. [S10]
- Gunicorn's classic worker-count guidance is `(2 x $num_cores) + 1`. [S9]
- Gunicorn does not run on Windows: it imports the Unix-only `fcntl` module, so on Windows it fails with `ModuleNotFoundError: No module named 'fcntl'`. [S11]

## 4. The "Gunicorn + Uvicorn workers" combo, and its deprecation

- You can run an ASGI app under Gunicorn by using the Uvicorn worker class: `gunicorn -k uvicorn.workers.UvicornWorker`. [S12]
- That built-in worker class is deprecated: the `uvicorn.workers` module emits a deprecation warning and points users to the separate `uvicorn-worker` package. [S13]
- The replacement is `pip install uvicorn-worker`, then `-k uvicorn_worker.UvicornWorker`. [S14]

## 5. The Python concurrency model (the "why" behind workers)

- CPython has a Global Interpreter Lock (GIL): a mutex that allows only one thread to execute Python bytecode at a time. [S15]
- Therefore, to use multiple CPU cores for Python-bytecode work you run multiple processes (e.g. via `multiprocessing` or multiple workers), not multiple threads. [S16]
- FastAPI: a normal `def` path operation is run in an external threadpool, while an `async def` one runs on the event loop — and you must not put blocking work directly in `async def`, or it blocks the loop. [S17]
- PEP 703 proposes making the GIL optional (a free-threaded CPython build). [S18]
- A free-threaded (no-GIL) build is available experimentally starting in Python 3.13. [S19]

## 6. FastAPI's deployment guidance

- FastAPI documents using `--workers` (via the `fastapi` or `uvicorn` command) to run multiple worker processes and use multiple CPU cores. [S20]
- For Kubernetes, FastAPI says: *"when running on Kubernetes you will probably not want to use workers and instead run a single Uvicorn process per container."* [S20]
- For clusters generally, FastAPI says to handle replication at the cluster level *"instead of using a process manager (like Uvicorn with workers) in each container."* [S21]

## 7. Performance positioning

- "Gunicorn + Uvicorn workers" and "Uvicorn `--workers`" do the actual HTTP/ASGI work with the same Uvicorn code; Gunicorn's role is process management, not request handling. [S9][S12] *(architectural fact: each Gunicorn worker is a Uvicorn instance)*
- Cross-framework/server throughput can be compared on the TechEmpower Web Framework Benchmarks. [S22]

## 8. Our own measured numbers (this repository)

- On Windows 11 / 8 CPUs / Python 3.13 / Uvicorn 0.34.0, the blocking `/sync-io` endpoint failed 272 of 1000 requests with 1 worker but 0 of 1000 with 4 workers. [S23]
- On the same setup, the CPU endpoint rose from 106.8 req/s (1 worker) to 130.6 req/s (4 workers). [S23]
- These are reproducible via `python benchmarks/run_suite.py`; raw JSON lives in `results/raw/`. [S23]

> *(ours)* The Windows figures understate Linux because uvloop is absent on Windows [S7]; treat them as a conservative floor, not a production ceiling.

---

## Sources

| Tag | Source | Link |
|---|---|---|
| **S1** | PEP 3333 — Python Web Server Gateway Interface v1.0.1 | https://peps.python.org/pep-3333/ |
| **S2** | ASGI documentation — Introduction | https://asgi.readthedocs.io/en/latest/introduction.html |
| **S3** | FastAPI — Deployment | https://fastapi.tiangolo.com/deployment/ |
| **S4** | Uvicorn — home | https://www.uvicorn.org/ |
| **S5** | Uvicorn — Installation / `[standard]` extras | https://www.uvicorn.org/#installation |
| **S6** | MagicStack/uvloop — README ("makes asyncio 2-4x faster") | https://github.com/MagicStack/uvloop |
| **S7** | uvloop issue #352 — "Any chance to get available on Windows?" | https://github.com/MagicStack/uvloop/issues/352 |
| **S8** | Uvicorn — Settings (`--workers`) | https://www.uvicorn.org/settings/ |
| **S9** | Gunicorn — Design (pre-fork model; "How Many Workers?") | https://docs.gunicorn.org/en/stable/design.html |
| **S10** | Gunicorn — Settings (`timeout`, `max-requests`, `preload_app`) | https://docs.gunicorn.org/en/stable/settings.html |
| **S11** | Gunicorn issue #524 — "Add Windows support" (fcntl/Unix-only) | https://github.com/benoitc/gunicorn/issues/524 |
| **S12** | Uvicorn — Deployment (Gunicorn worker class) | https://www.uvicorn.org/deployment/ |
| **S13** | Uvicorn — `uvicorn/workers.py` (deprecation warning) | https://github.com/encode/uvicorn/blob/master/uvicorn/workers.py |
| **S14** | `uvicorn-worker` on PyPI | https://pypi.org/project/uvicorn-worker/ |
| **S15** | Python glossary — Global Interpreter Lock | https://docs.python.org/3/glossary.html#term-global-interpreter-lock |
| **S16** | Python — `multiprocessing` (sidesteps the GIL with subprocesses) | https://docs.python.org/3/library/multiprocessing.html |
| **S17** | FastAPI — Concurrency and async / await | https://fastapi.tiangolo.com/async/ |
| **S18** | PEP 703 — Making the Global Interpreter Lock Optional in CPython | https://peps.python.org/pep-0703/ |
| **S19** | Python 3.13 — What's New (free-threaded CPython) | https://docs.python.org/3/whatsnew/3.13.html#free-threaded-cpython |
| **S20** | FastAPI — Server Workers | https://fastapi.tiangolo.com/deployment/server-workers/ |
| **S21** | FastAPI — FastAPI in Containers - Docker | https://fastapi.tiangolo.com/deployment/docker/ |
| **S22** | TechEmpower Web Framework Benchmarks | https://www.techempower.com/benchmarks/ |
| **S23** | This repo — `results/native_suite.log`, `results/raw/*.json` (reproduce: `python benchmarks/run_suite.py`) | [../results/](../results/) |

> The narrative documents in this repo make these same claims in prose; this page exists so every line is
> independently checkable. Where a line is our synthesis rather than an external fact, it is marked *(ours)*.
> The full claim→source map (including more rows) is [CLAIMS_AND_SOURCES.md](../CLAIMS_AND_SOURCES.md).
