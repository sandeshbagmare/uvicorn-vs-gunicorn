# Uvicorn vs Gunicorn — Researched, Benchmarked, Reproducible

A complete, hands-on comparison of **Uvicorn** vs **Gunicorn (+ Uvicorn workers)** for running
async Python (FastAPI) apps in production. It ships with:

- 📄 A detailed article / Confluence page: [docs/uvicorn-vs-gunicorn.md](docs/uvicorn-vs-gunicorn.md)
- 📊 A 15-parameter decision matrix: [docs/decision-matrix.md](docs/decision-matrix.md)
- 🧪 A FastAPI demo app that exposes baseline / async-IO / blocking-IO / CPU endpoints: [app/main.py](app/main.py)
- ⚡ A pure-Python async load tester (1000+ parallel requests, latency percentiles, worker-PID proof): [benchmarks/loadtest.py](benchmarks/loadtest.py)
- 🤖 A one-command benchmark suite that boots each server and compares them: [benchmarks/run_suite.py](benchmarks/run_suite.py)
- 🐳 Docker setup so you can run **Gunicorn even on Windows**: [docker/](docker/)

> **Platform note:** **Gunicorn does not run on Windows** (it needs the Unix-only `fcntl`).
> On Windows you can still benchmark Uvicorn (1 vs N workers) natively, and run the Gunicorn
> comparison via **WSL** or the **Docker** setup in [docker/](docker/).

---

## Project layout

```
uvicornvsgunicorn/
├── README.md                      ← you are here
├── RESEARCH_PAPER.md              ← 8-min research paper: slim vs fat pods, with the conclusion
├── DEV_GUIDE.md                   ← 7-min dev quick guide: what to use where + commands
├── FINAL_WORD.md                  ← the capstone: Kubernetes-first conclusion + every finding
├── BEGINNERS_GUIDE.md             ← plain-English explainer (no experience needed)
├── FINAL_CONFLUENCE_PAGE.md       ← full technical reference (matrix + all benchmark numbers)
├── CLAIMS_AND_SOURCES.md          ← every assertion paired with its source link
├── LICENSE                        ← MIT
├── app/
│   ├── main.py                    ← FastAPI demo app (/, /async-io, /sync-io, /cpu)
│   └── requirements.txt
├── benchmarks/
│   ├── loadtest.py                ← async load tester (the core demo tool)
│   ├── run_suite.py               ← boots each server, runs the matrix, compares
│   ├── plot_results.py            ← turns results/raw/*.json into charts
│   ├── run_uvicorn.ps1 / .sh      ← start Uvicorn (Windows / Unix)
│   └── run_gunicorn.sh            ← start Gunicorn + Uvicorn workers (Unix only)
├── docs/
│   ├── uvicorn-vs-gunicorn.md     ← the full article
│   └── decision-matrix.md         ← 15-parameter scoring table
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml         ← gunicorn + uvicorn-1w + uvicorn-Nw side by side
├── research/                      ← deep-dive: latency of "N pods × 4 workers" on Kubernetes
│   ├── README.md                  ← the research write-up (measured + modelled + manifests)
│   ├── scripts/                   ← cluster_emulation.py, latency_model.py, k8s_loadtest.sh
│   ├── manifests/                 ← k8s Deployment + Service + HPA + PDB for the scenario
│   └── data/                      ← measured emulation + model prediction JSON
└── results/                       ← benchmark JSON + charts land here
```

## Setup

```powershell
# from the project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # Windows PowerShell
# source .venv/bin/activate             # Linux / macOS / WSL
pip install -r app/requirements.txt
```

## Quickstart — prove it in 3 commands

**1) Start a server** (Windows, 4 workers):
```powershell
.\benchmarks\run_uvicorn.ps1 -Workers 4
```

**2) Fire 1000 requests, 100 in parallel**, at the async endpoint (new terminal):
```powershell
python benchmarks\loadtest.py --url http://127.0.0.1:8000 --endpoint /async-io --requests 1000 --concurrency 100
```
You'll see throughput, latency p50/p95/p99, and **how many distinct worker PIDs** answered
(this is the proof that your 4 workers really shared the load).

**3) Compare the work shapes** — re-run step 2 against `/cpu` and `/sync-io` and watch the story
change (CPU rewards more workers; blocking I/O stays slow no matter the concurrency).

## One command for the whole comparison

```powershell
# boots uvicorn-1w, uvicorn-Nw (and gunicorn on Unix), runs the matrix, prints a comparison table
python benchmarks\run_suite.py            # full matrix
python benchmarks\run_suite.py --quick    # faster
python benchmarks\plot_results.py         # charts -> results/charts/
```

## Running the Gunicorn comparison on Windows (via Docker)

```powershell
docker compose -f docker/docker-compose.yml up --build
# gunicorn  -> http://127.0.0.1:8001
# uvicorn-1 -> http://127.0.0.1:8002
# uvicorn-4 -> http://127.0.0.1:8003
python benchmarks\loadtest.py --url http://127.0.0.1:8001 --endpoint /cpu --requests 600 --concurrency 100 --label gunicorn-cpu --out results/raw
python benchmarks\loadtest.py --url http://127.0.0.1:8003 --endpoint /cpu --requests 600 --concurrency 100 --label uvicorn-4w-cpu --out results/raw
```

## What to read next
- **Want the research & the verdict? (8-min read)** [RESEARCH_PAPER.md](RESEARCH_PAPER.md) — the full study:
  the 10,000-request slim-vs-fat experiment, analysis, threats to validity, and the conclusion
  (default to slim containers; use fat Gunicorn pods only for named reasons).
- **Shipping today? (7-min read)** [DEV_GUIDE.md](DEV_GUIDE.md) — pick-your-row recommendations,
  copy-paste commands, the one perf rule, and inline source links.
- **Just want the conclusion?** Read [FINAL_WORD.md](FINAL_WORD.md) — the one-page capstone: a
  Kubernetes-first verdict that folds in every finding and tackles the "won't one worker per pod
  underutilise my multi-core node?" question head-on.
- **New to all this?** Read [BEGINNERS_GUIDE.md](BEGINNERS_GUIDE.md) — explains web servers, WSGI vs
  ASGI, Uvicorn, and Gunicorn from scratch, with the real benchmark results in plain English.
- **Want the full reference?** [FINAL_CONFLUENCE_PAGE.md](FINAL_CONFLUENCE_PAGE.md) — every detail:
  15-parameter decision matrix, complete benchmark tables, analysis, production checklist, the
  Kubernetes pods-vs-workers deep dive (§14), and sources.
- **Want to verify a claim?** [CLAIMS_AND_SOURCES.md](CLAIMS_AND_SOURCES.md) — every assertion in this
  repo paired with its official doc, spec/PEP, maintainer repo, or engineering-blog source.
- **Researching cluster latency?** [research/](research/) — "what is the latency of N pods × 4 workers
  (Gunicorn) on Kubernetes?", measured by an emulation harness, predicted by a queueing model, and
  reproducible on a real cluster via the included manifests.
- **Hitting memory growth / hard restarts / 529-530?** [research/memory-leaks-and-worker-recycling.md](research/memory-leaks-and-worker-recycling.md)
  — a measured study of whether LangGraph+PostgresSaver actually leaks, how Gunicorn `max_requests`
  (and Uvicorn `--limit-max-requests`) recycle memory, and why "one worker per pod × two pods" makes both
  restart at once → 529/530, with the fix (jitter + replicas + readiness + a `kubectl rollout restart` CronJob).
- **One Uvicorn worker per pod, no Gunicorn — how does Kubernetes recycle it?** [research/recycling-one-uvicorn-worker-per-pod-on-kubernetes.md](research/recycling-one-uvicorn-worker-per-pod-on-kubernetes.md)
  — Kubernetes as the process manager: the Gunicorn→K8s knob-for-knob map, the **CrashLoopBackOff** trap with
  `--limit-max-requests`, and a scheduled `kubectl rollout restart` as the native `max_requests` (full manifest included).
- **"Is that actually the industry standard?"** [research/is-one-process-per-pod-industry-standard.md](research/is-one-process-per-pod-industry-standard.md)
  — the claim proven with primary-source quotes from **five independent authorities** (Kubernetes, Google Cloud,
  AWS, the Twelve-Factor App, FastAPI), with an honest grading of what's settled vs common practice.
- Start with the **TL;DR** and **30-second mental model** in [docs/uvicorn-vs-gunicorn.md](docs/uvicorn-vs-gunicorn.md).
- Score your own situation with [docs/decision-matrix.md](docs/decision-matrix.md).
- Then run the benchmarks above and paste your numbers into the article's *Benchmark results* section.

## License
Released under the [MIT License](LICENSE) © 2026 Sandesh Bagmare.
