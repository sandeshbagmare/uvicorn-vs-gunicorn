# Claims & Sources — Evidence Ledger

> **What this page is.** Every substantive assertion made anywhere in this repository
> ([README](README.md), [BEGINNERS_GUIDE.md](BEGINNERS_GUIDE.md),
> [FINAL_CONFLUENCE_PAGE.md](FINAL_CONFLUENCE_PAGE.md), [docs/](docs/)) paired with the
> source where you can verify it — official documentation, specifications/PEPs, maintainer
> repositories, engineering blogs, or this repo's own measured data.
>
> **How to read the "Source type" column:**
> - **Official** — first-party docs/specs from the project that owns the thing (Uvicorn, Gunicorn, FastAPI, Python, Kubernetes).
> - **Spec/PEP** — a formal standard (PEP, ASGI spec).
> - **Maintainer** — the maintainer's repository, PR, issue, or release note.
> - **Blog/Eng** — a reputable engineering blog or write-up (clearly opinion/experience, not first-party spec).
> - **Measured (this repo)** — a number we produced ourselves; the "source" is the raw result file in this repo, reproducible via `benchmarks/run_suite.py`.
>
> **Verification date:** Links checked 2026-06-23. Versions move — re-verify against live docs before relying on any version-specific detail.

---

## 1. ASGI, WSGI, and the protocol distinction

| # | Claim (as stated in our docs) | Source type | Source(s) |
|---|---|---|---|
| 1.1 | ASGI (Asynchronous Server Gateway Interface) is the async successor to WSGI; defines how async servers talk to async Python apps | Spec/PEP | [ASGI specification](https://asgi.readthedocs.io/) |
| 1.2 | WSGI (Web Server Gateway Interface) is the older, synchronous standard (one request per worker at a time) | Spec/PEP | [PEP 3333 — WSGI v1.0.1](https://peps.python.org/pep-3333/), [PEP 333 — original WSGI](https://peps.python.org/pep-0333/) |
| 1.3 | An async FastAPI app must be served by an ASGI server; Gunicorn's plain sync workers cannot serve ASGI apps | Official | [ASGI spec — Introduction](https://asgi.readthedocs.io/en/latest/introduction.html), [FastAPI deployment](https://fastapi.tiangolo.com/deployment/) |
| 1.4 | WSGI frameworks = Flask, classic Django; ASGI frameworks = FastAPI, Starlette, Litestar, Django (ASGI) | Official | [Flask docs](https://flask.palletsprojects.com/), [Starlette](https://www.starlette.io/), [Django ASGI](https://docs.djangoproject.com/en/stable/howto/deployment/asgi/) |
| 1.5 | ASGI enables WebSockets / long-lived connections that WSGI cannot natively support | Spec/PEP | [ASGI spec — WebSocket](https://asgi.readthedocs.io/en/latest/specs/www.html#websocket) |

---

## 2. Uvicorn — what it is and how it performs

| # | Claim | Source type | Source(s) |
|---|---|---|---|
| 2.1 | Uvicorn is an ASGI web server (HTTP/1.1 + WebSockets) built by Encode | Official | [uvicorn.org](https://www.uvicorn.org/), [encode/uvicorn](https://github.com/encode/uvicorn) |
| 2.2 | `pip install "uvicorn[standard]"` pulls uvloop + httptools (and other extras) | Official | [Uvicorn — Installation / extras](https://www.uvicorn.org/#installation) |
| 2.3 | uvloop is a libuv-based event loop that makes asyncio ~2–4× faster | Maintainer | [MagicStack/uvloop (README: "makes asyncio 2-4x faster")](https://github.com/MagicStack/uvloop), [uvloop on PyPI](https://pypi.org/project/uvloop/) |
| 2.4 | **uvloop does not run on Windows** (POSIX-only); Uvicorn falls back to stock `asyncio` on Windows | Maintainer | [uvloop issue #352 "Any chance to get available on Windows?"](https://github.com/MagicStack/uvloop/issues/352), [Winloop — Windows fork of uvloop](https://github.com/Vizonex/Winloop) |
| 2.5 | httptools is a fast C HTTP parser; h11 is the pure-Python fallback | Maintainer | [MagicStack/httptools](https://github.com/MagicStack/httptools), [python-hyper/h11](https://github.com/python-hyper/h11) |
| 2.6 | `uvicorn --workers N` starts Uvicorn's own built-in multiprocess supervisor (N worker processes behind one socket) | Official | [Uvicorn — Settings (`--workers`)](https://www.uvicorn.org/settings/), [Uvicorn — Deployment](https://www.uvicorn.org/deployment/) |
| 2.7 | Uvicorn runs on Windows (incl. `--workers`) | Official | [Uvicorn — Deployment](https://www.uvicorn.org/deployment/) |

---

## 3. Gunicorn — what it is and its capabilities

| # | Claim | Source type | Source(s) |
|---|---|---|---|
| 3.1 | Gunicorn ("Green Unicorn") is a pre-fork WSGI HTTP server; a master forks and supervises workers | Official | [Gunicorn — Design (pre-fork model)](https://docs.gunicorn.org/en/stable/design.html) |
| 3.2 | Gunicorn has been in production use since ~2010 (ported from Ruby's Unicorn) | Official | [Gunicorn home/docs](https://docs.gunicorn.org/en/stable/), [benoitc/gunicorn](https://github.com/benoitc/gunicorn) |
| 3.3 | **Gunicorn does not run on Windows** — it imports the Unix-only `fcntl` module (and uses `os.fork`) | Maintainer | [gunicorn issue #524 "Add Windows support"](https://github.com/benoitc/gunicorn/issues/524), [issue #587 (fcntl on Windows)](https://github.com/benoitc/gunicorn/issues/587), [issue #3015 ("fnctl" on Windows)](https://github.com/benoitc/gunicorn/issues/3015) |
| 3.4 | `--timeout` kills workers that hang / stop sending heartbeats; master restarts them | Official | [Gunicorn — Settings (`timeout`)](https://docs.gunicorn.org/en/stable/settings.html#timeout), [Gunicorn — Design](https://docs.gunicorn.org/en/stable/design.html) |
| 3.5 | `--max-requests` (+ `--max-requests-jitter`) recycles workers after N requests to bound memory leaks | Official | [Gunicorn — Settings (`max-requests`)](https://docs.gunicorn.org/en/stable/settings.html#max-requests) |
| 3.6 | Signals: `HUP` graceful reload, `USR2` hot binary upgrade, `TTIN`/`TTOU` add/remove workers | Official | [Gunicorn — Signal Handling](https://docs.gunicorn.org/en/stable/signals.html) |
| 3.7 | `--preload` loads the app in the master before forking, sharing read-only memory via copy-on-write | Official | [Gunicorn — Settings (`preload_app`)](https://docs.gunicorn.org/en/stable/settings.html#preload-app) |
| 3.8 | Recommended worker count starting point: `(2 × cores) + 1` | Official | [Gunicorn — Design (How Many Workers?)](https://docs.gunicorn.org/en/stable/design.html#how-many-workers) |
| 3.9 | Gunicorn serves WSGI; to serve ASGI you supply the Uvicorn worker class (see §4) | Official | [Gunicorn — Design](https://docs.gunicorn.org/en/stable/design.html), [Uvicorn — Deployment](https://www.uvicorn.org/deployment/) |

---

## 4. "Gunicorn + Uvicorn workers" — the combo and its deprecation

| # | Claim | Source type | Source(s) |
|---|---|---|---|
| 4.1 | `gunicorn app:app -k uvicorn.workers.UvicornWorker -w 4` runs Gunicorn as master with Uvicorn ASGI workers | Official | [Uvicorn — Deployment (Gunicorn worker)](https://www.uvicorn.org/deployment/) |
| 4.2 | **`uvicorn.workers` is deprecated (since Uvicorn 0.30)** and moved to a separate package `uvicorn-worker` (`uvicorn_worker.UvicornWorker`) | Maintainer | [PR #2302 "Deprecate the `uvicorn.workers` module"](https://github.com/Kludex/uvicorn/pull/2302), [Kludex/uvicorn-worker](https://github.com/Kludex/uvicorn-worker), [uvicorn-worker on PyPI](https://pypi.org/project/uvicorn-worker/) |
| 4.3 | The new package is maintained by Marcelo Trylesinski (Kludex); legacy import still works but emits a `DeprecationWarning` | Maintainer | [uvicorn-worker repo](https://github.com/Kludex/uvicorn-worker), [uvicorn/workers.py (deprecation warning)](https://github.com/encode/uvicorn/blob/master/uvicorn/workers.py) |

---

## 5. Python concurrency model — GIL, asyncio, the no-GIL future

| # | Claim | Source type | Source(s) |
|---|---|---|---|
| 5.1 | The GIL allows only one thread to execute Python bytecode at a time per process | Official | [Python glossary — GIL](https://docs.python.org/3/glossary.html#term-global-interpreter-lock), [Python wiki — GlobalInterpreterLock](https://wiki.python.org/moin/GlobalInterpreterLock) |
| 5.2 | To use multiple CPU cores for Python code you need multiple processes (not threads) | Official | [Python — multiprocessing](https://docs.python.org/3/library/multiprocessing.html), [Python wiki — GIL](https://wiki.python.org/moin/GlobalInterpreterLock) |
| 5.3 | The event loop gives one process high concurrency for awaited I/O; blocking calls freeze it | Official | [Python — asyncio](https://docs.python.org/3/library/asyncio.html), [FastAPI — Concurrency and async/await](https://fastapi.tiangolo.com/async/) |
| 5.4 | A blocking sync call inside `async def` blocks the whole event loop; fix via threadpool (`run_in_executor`) or `def` endpoints | Official | [FastAPI — async/await ("don't block")](https://fastapi.tiangolo.com/async/), [asyncio — `loop.run_in_executor`](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor) |
| 5.5 | FastAPI runs plain `def` (sync) path operations in an external threadpool automatically | Official | [FastAPI — async/await ("path operation functions")](https://fastapi.tiangolo.com/async/#path-operation-functions) |
| 5.6 | PEP 703 proposes making the GIL optional (free-threaded CPython); experimental build in Python 3.13 | Spec/PEP | [PEP 703 — Making the GIL Optional](https://peps.python.org/pep-0703/), [What's New in Python 3.13 — Free-threaded CPython](https://docs.python.org/3/whatsnew/3.13.html#free-threaded-cpython) |

---

## 6. FastAPI's deployment guidance

| # | Claim | Source type | Source(s) |
|---|---|---|---|
| 6.1 | FastAPI's current docs lead with `uvicorn --workers` (or `fastapi run --workers`) for multi-core, and **do not feature Gunicorn** on the server-workers page | Official | [FastAPI — Server Workers](https://fastapi.tiangolo.com/deployment/server-workers/) |
| 6.2 | FastAPI explicitly recommends **one Uvicorn process per container** in Kubernetes (do **not** use `--workers` there) | Official | [FastAPI — Server Workers ("not...workers...single Uvicorn process per container")](https://fastapi.tiangolo.com/deployment/server-workers/), [FastAPI — FastAPI in Containers](https://fastapi.tiangolo.com/deployment/docker/) |
| 6.3 | Using workers helps with "replication" (multi-core) but not the other deployment concerns (HTTPS, restarts on boot, etc.) | Official | [FastAPI — Server Workers](https://fastapi.tiangolo.com/deployment/server-workers/), [FastAPI — Deployment Concepts](https://fastapi.tiangolo.com/deployment/concepts/) |

---

## 7. Kubernetes — pods vs workers (the §14 deep dive)

| # | Claim | Source type | Source(s) |
|---|---|---|---|
| 7.1 | The Pod is the smallest deployable/manageable unit in Kubernetes | Official | [Kubernetes — Pods](https://kubernetes.io/docs/concepts/workloads/pods/) |
| 7.2 | Kubernetes manages pods, not processes inside a container — it cannot independently restart/scale/health-check a worker process inside a multi-worker container | Official | [Kubernetes — Pods](https://kubernetes.io/docs/concepts/workloads/pods/), [Kubernetes — Pod Lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/) |
| 7.3 | The Horizontal Pod Autoscaler scales the number of **pods** (replicas), based on metrics | Official | [Kubernetes — Horizontal Pod Autoscaling](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) |
| 7.4 | Liveness/readiness/startup probes operate at the **container/pod** level (a probe is answered by whichever worker responds) | Official | [Kubernetes — Configure Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/) |
| 7.5 | CPU limits are enforced via the Linux CFS quota; a multi-process pod can be **throttled** even with spare node capacity | Official | [Kubernetes — Assign CPU Resources](https://kubernetes.io/docs/tasks/configure-pod-container/assign-cpu-resource/), [Linux kernel — CFS Bandwidth Control](https://docs.kernel.org/scheduler/sched-bwc.html) |
| 7.6 | Common (debated) guidance: set CPU **requests**, be cautious about CPU **limits** on latency-sensitive services (avoid throttling) — endorsed by Tim Hockin | Blog/Eng | [Robusta — "For the Love of God, Stop Using CPU Limits on Kubernetes"](https://home.robusta.dev/blog/stop-using-cpu-limits) |
| 7.7 | Memory is non-compressible (over-limit → OOMKill); CPU is compressible (throttled) — so treat the two limits differently | Official + Blog/Eng | [Kubernetes — Manage Resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/), [Robusta blog](https://home.robusta.dev/blog/stop-using-cpu-limits) |
| 7.8 | Spread pods across nodes for HA with topology spread constraints / anti-affinity | Official | [Kubernetes — Pod Topology Spread Constraints](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/) |
| 7.9 | Protect capacity during voluntary disruptions with a PodDisruptionBudget | Official | [Kubernetes — Specifying a Disruption Budget](https://kubernetes.io/docs/tasks/run-application/configure-pdb/) |
| 7.10 | Graceful shutdown: SIGTERM → drain within `terminationGracePeriodSeconds`; `preStop` hook helps deregister before SIGTERM | Official | [Kubernetes — Pod termination](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination) |
| 7.11 | `fork()` shares parent memory copy-on-write until written — the basis for `--preload` memory savings | Official | [Linux man — fork(2)](https://man7.org/linux/man-pages/man2/fork.2.html) |
| 7.12 | Real-world: Python pre-fork web servers lose CoW-shared memory due to GC/refcount writes; Instagram fixed this and upstreamed `gc.freeze()` (Python 3.7) | Blog/Eng + Official | [Instagram Eng — Dismissing Python GC](https://instagram-engineering.com/dismissing-python-garbage-collection-at-instagram-4dca40b29172), [Instagram Eng — Copy-on-write friendly Python GC](https://instagram-engineering.com/copy-on-write-friendly-python-garbage-collection-ad6ed5233ddf), [Python docs — `gc.freeze()`](https://docs.python.org/3/library/gc.html#gc.freeze) |
| 7.13 | Service meshes inject a per-pod sidecar (e.g. Istio Envoy), an overhead amortised better by fewer, denser pods; "ambient" mode removes per-pod sidecars | Official | [Istio — Sidecar injection](https://istio.io/latest/docs/setup/additional-setup/sidecar-injection/), [Istio — Ambient mesh](https://istio.io/latest/docs/ambient/) |

---

## 8. Benchmark results — our own measured data

> These numbers were produced **in this repository** on the test machine described below.
> They are not external claims; the "source" is the raw JSON each row was read from, and they
> are reproducible with `python benchmarks/run_suite.py`. **Caveat:** measured on Windows, where
> uvloop is absent — see [FINAL_CONFLUENCE_PAGE.md §8.1 & §10](FINAL_CONFLUENCE_PAGE.md#81-test-environment).

| # | Claim | Source type | Source(s) in this repo |
|---|---|---|---|
| 8.1 | Test environment: Windows 11, 8 CPUs, Python 3.13, FastAPI 0.115.6, Uvicorn 0.34.0`[standard]`, worker count 4 | Measured (this repo) | [`results/native_suite.log`](results/native_suite.log), [`app/requirements.txt`](app/requirements.txt) |
| 8.2 | `/` (trivial): 1 worker = 338.0 req/s vs 4 workers = 218.0 req/s | Measured (this repo) | [`results/raw/uvicorn-1worker__root.json`](results/raw/uvicorn-1worker__root.json), [`...4workers__root.json`](results/raw/uvicorn-4workers__root.json) |
| 8.3 | `/async-io`: 1 worker = 111.1 req/s vs 4 workers = 89.9 req/s | Measured (this repo) | [`...1worker__async-io.json`](results/raw/uvicorn-1worker__async-io.json), [`...4workers__async-io.json`](results/raw/uvicorn-4workers__async-io.json) |
| 8.4 | `/sync-io` (blocking): 1 worker = 22.2 req/s with **272/1000 failures**; 4 workers = 51.6 req/s with **0 failures** | Measured (this repo) | [`...1worker__sync-io.json`](results/raw/uvicorn-1worker__sync-io.json), [`...4workers__sync-io.json`](results/raw/uvicorn-4workers__sync-io.json) |
| 8.5 | `/cpu`: 1 worker = 106.8 req/s vs 4 workers = 130.6 req/s (~22% faster) | Measured (this repo) | [`...1worker__cpu.json`](results/raw/uvicorn-1worker__cpu.json), [`...4workers__cpu.json`](results/raw/uvicorn-4workers__cpu.json) |
| 8.6 | Distinct worker PIDs prove load spread (e.g. 4-worker async-io served by PIDs 3344/18860/19064/16740) | Measured (this repo) | [`...4workers__async-io.json` (`pid_counts`)](results/raw/uvicorn-4workers__async-io.json) |
| 8.7 | Gunicorn was **skipped** on the Windows run (Unix-only) — see §3.3 | Measured (this repo) | [`results/native_suite.log` (SKIP line)](results/native_suite.log) |

---

## 9. Cross-server performance context & alternative servers

| # | Claim | Source type | Source(s) |
|---|---|---|---|
| 9.1 | "Gunicorn+Uvicorn workers" and "Uvicorn --workers" have the same request-handling speed (same Uvicorn underneath); difference is operational | Official (architecture) | [Uvicorn — Deployment](https://www.uvicorn.org/deployment/), [Gunicorn — Design](https://docs.gunicorn.org/en/stable/design.html) |
| 9.2 | Cross-framework/server throughput context | Blog/Eng | [TechEmpower Web Framework Benchmarks](https://www.techempower.com/benchmarks/) |
| 9.3 | Other ASGI servers: Hypercorn (HTTP/2, HTTP/3), Granian (Rust-based), Daphne (Django Channels) | Maintainer | [Hypercorn](https://github.com/pgjones/hypercorn), [Granian](https://github.com/emmett-framework/granian), [Daphne](https://github.com/django/daphne) |
| 9.4 | Winloop is a community Windows-compatible fork of uvloop | Maintainer | [Vizonex/Winloop](https://github.com/Vizonex/Winloop) |
| 9.5 | httpx is the async HTTP client used by our load tester | Maintainer | [python-httpx.org](https://www.python-httpx.org/), [encode/httpx](https://github.com/encode/httpx) |

---

## 10. How to re-verify everything yourself

1. **Re-run our benchmarks** to regenerate every number in §8:
   ```bash
   python benchmarks/run_suite.py        # writes results/raw/*.json + a comparison table
   ```
   See [README.md](README.md) for setup and the Gunicorn-on-Windows-via-Docker path.

2. **Re-check external links.** All URLs above were live on 2026-06-23. For any version-specific
   claim (e.g. the `uvicorn.workers` deprecation version, the Python free-threaded build status),
   confirm against the current release notes — the linked changelog/PR/PEP is authoritative.

3. **Spot a discrepancy?** Trust the primary source (official docs / spec / maintainer repo) over
   any summary here, and over any blog. Blog/Eng sources are included for real-world colour and are
   labelled as such — they are experience reports, not specifications.

---

*This ledger backs the narrative documents in this repo. If you cite this project, cite the
underlying primary sources linked here rather than this page.*
