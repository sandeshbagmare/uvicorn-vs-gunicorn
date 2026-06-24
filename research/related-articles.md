# Related Articles & Further Reading (curated, with what each one supports)

> A vetted reading list for the Uvicorn vs Gunicorn / Kubernetes deployment question.
> Each entry says **what claim it backs** and **what type of source it is**, so you can weigh it.
> Primary sources (official docs, specs, maintainer repos) outrank engineering blogs; blogs are
> labelled as field reports / opinion. Verified 2026-06-24.

---

## Official documentation (highest trust)

- **FastAPI — Server Workers** — `--workers` for multi-core; "on Kubernetes … run a single Uvicorn process per container."
  https://fastapi.tiangolo.com/deployment/server-workers/
- **FastAPI — FastAPI in Containers (Docker)** — replication at the cluster level vs a process manager in each container; one-process-per-container; memory reasoning; the explicit "special cases" and "not rules written in stone."
  https://fastapi.tiangolo.com/deployment/docker/
- **FastAPI — Deployment Concepts** — the underlying checklist (replication, restarts, memory, HTTPS) that frames the whole workers-vs-pods discussion.
  https://fastapi.tiangolo.com/deployment/concepts/
- **Uvicorn — Deployment** — running Uvicorn in production; the Gunicorn worker-class pattern.
  https://www.uvicorn.org/deployment/
- **Gunicorn — Design** — the pre-fork master/worker model and "How Many Workers?" `(2×cores)+1`.
  https://docs.gunicorn.org/en/stable/design.html
- **Gunicorn — Settings** — `timeout`, `max-requests`, `preload_app`, `graceful-timeout` (the in-pod robustness knobs).
  https://docs.gunicorn.org/en/stable/settings.html
- **Kubernetes — Pods** — the Pod as the smallest manageable unit (why K8s can't see workers inside a container).
  https://kubernetes.io/docs/concepts/workloads/pods/
- **Kubernetes — Horizontal Pod Autoscaling** — HPA scales pods on metrics (why per-pod metric clarity matters).
  https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
- **Kubernetes — Liveness, Readiness & Startup Probes** — probe-per-container; the liveness/readiness split; startup-probe gating.
  https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/
- **Kubernetes — Resource Management for Pods and Containers** — memory non-compressible (OOM) vs CPU compressible (throttle).
  https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
- **Kubernetes — Assign CPU Resources** + **Linux CFS bandwidth control** — how CPU limits become throttling.
  https://kubernetes.io/docs/tasks/configure-pod-container/assign-cpu-resource/ · https://docs.kernel.org/scheduler/sched-bwc.html

## Specs & language internals

- **PEP 3333 — WSGI** and **ASGI spec** — the two interface standards.
  https://peps.python.org/pep-3333/ · https://asgi.readthedocs.io/
- **Python GIL (glossary)**, **PEP 703 (optional GIL)**, **Python 3.13 free-threaded** — why you need multiple processes, and how that may change.
  https://docs.python.org/3/glossary.html#term-global-interpreter-lock · https://peps.python.org/pep-0703/ · https://docs.python.org/3/whatsnew/3.13.html#free-threaded-cpython

## Maintainer issues / packages

- **Gunicorn issue #2467 — "Kubernetes: dedicated worker for healthcheck?"** — the canonical thread on probes vs multiple workers; liveness-should-pass-under-load; use `--timeout`.
  https://github.com/benoitc/gunicorn/issues/2467
- **Gunicorn issue #524 — "Add Windows support"** — why Gunicorn can't run on Windows (`fcntl`).
  https://github.com/benoitc/gunicorn/issues/524
- **uvicorn-worker (PyPI)** + **uvicorn `workers.py`** — the deprecation of `uvicorn.workers` and its replacement.
  https://pypi.org/project/uvicorn-worker/ · https://github.com/encode/uvicorn/blob/master/uvicorn/workers.py
- **MagicStack/uvloop** + **issue #352** — uvloop's 2–4× claim and its Windows unavailability.
  https://github.com/MagicStack/uvloop · https://github.com/MagicStack/uvloop/issues/352

## Engineering blogs & field reports (useful, but opinion/experience — weigh accordingly)

- **pythonspeed.com — "Configuring Gunicorn for Docker" (Hynek Schlawack)** — the heartbeat/health-check problem with one worker; the "1–2 workers + gthread threads" middle ground; OOM-without-logging risk.
  https://pythonspeed.com/articles/gunicorn-in-docker/
- **DEV — "From Chaos to Control: Tailored Autoscaling in Kubernetes"** — field report: multiple workers confused the HPA/metrics-server; resource-splitting OOM; one worker per pod fixed scaling.
  https://dev.to/check/from-chaos-to-control-the-importance-of-tailored-autoscaling-in-kubernetes-2kpn
- **blog.graywind.org — "gunicorn in Containers"** — practitioner notes on Gunicorn worker/thread choices in containers.
  https://blog.graywind.org/posts/gunicorn-in-containers/
- **Robusta — "For the Love of God, Stop Using CPU Limits on Kubernetes"** — the CPU-limits/throttling argument (notes endorsement by a Kubernetes maintainer); pair with the K8s CFS docs above for the primary-source mechanism.
  https://home.robusta.dev/blog/stop-using-cpu-limits
- **Instagram Engineering — "Dismissing Python GC" / "Copy-on-write friendly Python GC"** — why pre-fork CoW memory sharing erodes under Python's GC/refcounting, and the `gc.freeze()` fix (relevant if you use `--preload` for a shared model).
  https://instagram-engineering.com/dismissing-python-garbage-collection-at-instagram-4dca40b29172 · https://instagram-engineering.com/copy-on-write-friendly-python-garbage-collection-ad6ed5233ddf

## Benchmarks & queueing background

- **TechEmpower Web Framework Benchmarks** — cross-framework/server throughput context.
  https://www.techempower.com/benchmarks/
- **M/M/c queue & Erlang-C (Wikipedia)** — the math behind this repo's [`latency_model.py`](scripts/latency_model.py).
  https://en.wikipedia.org/wiki/M/M/c_queue · https://en.wikipedia.org/wiki/Erlang_(unit)

---

## In this repository

- [`why-not-gunicorn-workers-on-kubernetes.md`](why-not-gunicorn-workers-on-kubernetes.md) — the line-by-line-sourced article on the core question.
- [`sourced-edition-core-claims.md`](sourced-edition-core-claims.md) — every core Uvicorn-vs-Gunicorn claim with an inline source tag.
- [`README.md`](README.md) — the "N pods × 4 workers" latency research (measured + modelled + manifests).
- [`../CLAIMS_AND_SOURCES.md`](../CLAIMS_AND_SOURCES.md) — the full claim→source map for the whole repo.
