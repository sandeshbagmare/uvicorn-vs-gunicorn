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
├── BEGINNERS_GUIDE.md             ← plain-English explainer (no experience needed)
├── FINAL_CONFLUENCE_PAGE.md       ← full technical reference (matrix + all benchmark numbers)
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
- **New to all this?** Read [BEGINNERS_GUIDE.md](BEGINNERS_GUIDE.md) — explains web servers, WSGI vs
  ASGI, Uvicorn, and Gunicorn from scratch, with the real benchmark results in plain English.
- **Want the full reference?** [FINAL_CONFLUENCE_PAGE.md](FINAL_CONFLUENCE_PAGE.md) — every detail:
  15-parameter decision matrix, complete benchmark tables, analysis, production checklist, sources.
- Start with the **TL;DR** and **30-second mental model** in [docs/uvicorn-vs-gunicorn.md](docs/uvicorn-vs-gunicorn.md).
- Score your own situation with [docs/decision-matrix.md](docs/decision-matrix.md).
- Then run the benchmarks above and paste your numbers into the article's *Benchmark results* section.

## License
Released under the [MIT License](LICENSE) © 2026 Sandesh Bagmare.
