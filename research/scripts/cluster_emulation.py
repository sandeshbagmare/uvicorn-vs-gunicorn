"""
Cluster emulation harness
==========================

Goal: approximate, on a single machine, what a Kubernetes deployment of
"N pods, each running 4 workers" does to *latency* as you scale the number of
pods and the offered load.

How it emulates a cluster
-------------------------
- Each "pod" is one server process bound to its own port:
    * Linux / macOS : gunicorn app.main:app -k uvicorn(.workers|_worker).UvicornWorker -w <workers>
    * Windows       : uvicorn app.main:app --workers <workers>   (Gunicorn can't run on Windows;
                      see docs/) -- still a valid N-pods-of-M-workers shape, just without Gunicorn.
- A round-robin async client emulates the Kubernetes Service / kube-proxy spreading
  connections across pod endpoints (Service ClusterIP load-balances per-connection).
- We sweep offered concurrency and record throughput + latency percentiles, and the
  spread of work across distinct worker PIDs (proof the workers/pods shared the load).

IMPORTANT honesty note
----------------------
A single laptop has a fixed number of physical cores. Emulating "4 pods x 4 workers
= 16 worker processes" on an 8-core box means processes contend for cores -- so the
ABSOLUTE numbers are NOT a substitute for measuring on a real cluster where each pod
gets its own 4-CPU allocation. What this harness gives you that is still valuable:
  1. Real, reproducible latency curves for 1 vs N pods on identical hardware.
  2. The SHAPE of how latency/throughput respond to scaling pods and concurrency.
  3. Per-pod single-instance numbers you can feed into the analytical model
     (research/scripts/latency_model.py) to PREDICT real-cluster latency.
Run the included Kubernetes manifests on a real cluster for ground-truth numbers.

Usage
-----
    # 1 pod x 4 workers, then 3 pods x 4 workers, sweeping concurrency, on /async-io
    python research/scripts/cluster_emulation.py --pods 1 3 --workers 4 \
        --endpoint /async-io --concurrency 50 100 200 400 --requests 2000

    # CPU endpoint, 1/2/4 pods
    python research/scripts/cluster_emulation.py --pods 1 2 4 --workers 4 \
        --endpoint /cpu --concurrency 50 100 200 --requests 1200

Outputs JSON to research/data/ and prints a comparison table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import signal
import statistics
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from itertools import cycle

import httpx

IS_WIN = platform.system() == "Windows"
CPU = os.cpu_count() or 4
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --------------------------------------------------------------------------- #
# Worker-class detection: prefer the new uvicorn-worker package, fall back to  #
# the (deprecated) built-in, so this works across Uvicorn versions.            #
# --------------------------------------------------------------------------- #
def _gunicorn_worker_class() -> str:
    try:
        import uvicorn_worker  # noqa: F401
        return "uvicorn_worker.UvicornWorker"
    except Exception:
        return "uvicorn.workers.UvicornWorker"


def pod_command(port: int, workers: int) -> tuple[list[str], str]:
    """Return (command, human description) for one pod bound to `port`."""
    app = "app.main:app"
    if IS_WIN:
        cmd = [sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1",
               "--port", str(port), "--workers", str(workers), "--log-level", "warning"]
        desc = f"uvicorn --workers {workers} (Windows; Gunicorn unavailable)"
    else:
        cmd = [sys.executable, "-m", "gunicorn", app, "-k", _gunicorn_worker_class(),
               "-w", str(workers), "-b", f"127.0.0.1:{port}", "--log-level", "warning"]
        desc = f"gunicorn -k {_gunicorn_worker_class()} -w {workers}"
    return cmd, desc


def start_pod(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict = {"cwd": PROJECT_ROOT}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def stop_pod(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    finally:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def wait_healthy(port: int, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1,
                   int(round((pct / 100.0) * len(sorted_vals) + 0.5)) - 1))
    return sorted_vals[k]


async def _one(client, url, sem, latencies, statuses, pids, errors):
    async with sem:
        t0 = time.perf_counter()
        try:
            r = await client.get(url)
            latencies.append((time.perf_counter() - t0) * 1000.0)
            statuses[r.status_code] += 1
            try:
                pid = r.json().get("pid")
                if pid is not None:
                    pids[pid] += 1
            except Exception:
                pass
        except Exception as exc:
            latencies.append((time.perf_counter() - t0) * 1000.0)
            errors.append(type(exc).__name__)


async def run_against_pods(ports: list[int], endpoint: str, total: int,
                           concurrency: int, timeout: float = 30.0) -> dict:
    """Fire `total` requests, round-robined across pod ports (emulates a K8s Service)."""
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: Counter = Counter()
    pids: Counter = Counter()
    errors: list[str] = []
    port_iter = cycle(ports)
    targets = [f"http://127.0.0.1:{next(port_iter)}{endpoint}" for _ in range(total)]

    limits = httpx.Limits(max_connections=concurrency,
                          max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        for p in ports:  # warm up each pod
            try:
                await client.get(f"http://127.0.0.1:{p}{endpoint}")
            except Exception:
                pass
        wall0 = time.perf_counter()
        await asyncio.gather(*[
            _one(client, t, sem, latencies, statuses, pids, errors) for t in targets
        ])
        wall = time.perf_counter() - wall0

    s = sorted(latencies)
    ok = sum(c for st, c in statuses.items() if 200 <= st < 400)
    err = len(errors) + sum(c for st, c in statuses.items() if st >= 400)
    return {
        "endpoint": endpoint,
        "pods": len(ports),
        "concurrency": concurrency,
        "requests": total,
        "wall_time_s": round(wall, 3),
        "throughput_rps": round(total / wall, 1) if wall > 0 else 0.0,
        "ok": ok,
        "errors": err,
        "distinct_pids": len(pids),
        "latency_ms": {
            "min": round(min(s), 2) if s else 0.0,
            "p50": round(_percentile(s, 50), 2),
            "p90": round(_percentile(s, 90), 2),
            "p95": round(_percentile(s, 95), 2),
            "p99": round(_percentile(s, 99), 2),
            "max": round(max(s), 2) if s else 0.0,
            "mean": round(statistics.fmean(s), 2) if s else 0.0,
        },
        "pid_distribution": dict(pids),
        "transport_errors": dict(Counter(errors)) if errors else {},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Emulate N-pods-x-M-workers and measure latency vs load")
    ap.add_argument("--pods", type=int, nargs="+", default=[1, 3],
                    help="pod counts to test, e.g. --pods 1 3 4")
    ap.add_argument("--workers", type=int, default=4, help="workers per pod (default 4)")
    ap.add_argument("--endpoint", default="/async-io")
    ap.add_argument("--concurrency", type=int, nargs="+", default=[50, 100, 200, 400])
    ap.add_argument("--requests", type=int, default=2000)
    ap.add_argument("--base-port", type=int, default=9100)
    ap.add_argument("--out", default="research/data")
    args = ap.parse_args()

    _, pod_desc = pod_command(args.base_port, args.workers)
    print("=" * 88)
    print("CLUSTER EMULATION")
    print(f"  platform={platform.system()}  host_cpus={CPU}  workers/pod={args.workers}")
    print(f"  pod server = {pod_desc}")
    print(f"  endpoint={args.endpoint}  requests/run={args.requests}")
    print(f"  pod counts={args.pods}  concurrency sweep={args.concurrency}")
    if not IS_WIN:
        print("  (Gunicorn active)")
    else:
        print("  NOTE: Windows -> Uvicorn --workers stands in for Gunicorn (see docs/).")
    over = max(args.pods) * args.workers
    if over > CPU:
        print(f"  WARNING: max processes ({over}) > host cpus ({CPU}): expect core contention; "
              f"use the analytical model for real-cluster prediction.")
    print("=" * 88 + "\n")

    runs: list[dict] = []
    for n_pods in args.pods:
        ports = [args.base_port + i for i in range(n_pods)]
        procs: list[subprocess.Popen] = []
        print(f"--- Booting {n_pods} pod(s) x {args.workers} workers on ports {ports} ---")
        try:
            for port in ports:
                cmd, _ = pod_command(port, args.workers)
                procs.append(start_pod(cmd))
            if not all(wait_healthy(p) for p in ports):
                print(f"    !! not all pods healthy; skipping {n_pods}-pod case\n")
                continue
            print("    all pods healthy. sweeping concurrency...\n")
            for conc in args.concurrency:
                res = asyncio.run(run_against_pods(ports, args.endpoint, args.requests, conc))
                res["workers_per_pod"] = args.workers
                res["total_worker_processes"] = n_pods * args.workers
                res["platform"] = platform.system()
                res["server"] = pod_desc
                runs.append(res)
                lm = res["latency_ms"]
                print(f"    pods={n_pods} conc={conc:>4}  "
                      f"rps={res['throughput_rps']:>8}  "
                      f"p50={lm['p50']:>8}  p95={lm['p95']:>9}  p99={lm['p99']:>9}  "
                      f"ok={res['ok']}/{res['requests']}  pids={res['distinct_pids']}")
        finally:
            for pr in procs:
                stop_pod(pr)
            print(f"--- stopped {n_pods}-pod case ---\n")
            time.sleep(1.0)

    os.makedirs(os.path.join(PROJECT_ROOT, args.out), exist_ok=True)
    safe_ep = args.endpoint.strip("/").replace("/", "_") or "root"
    out_path = os.path.join(PROJECT_ROOT, args.out, f"emulation__{safe_ep}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"meta": vars(args), "runs": runs}, f, indent=2)
    print(f"saved -> {out_path}")
    _print_table(runs)


def _print_table(runs: list[dict]) -> None:
    if not runs:
        print("No runs collected.")
        return
    print("\n" + "=" * 92)
    print("LATENCY vs SCALE  (throughput rps; latency ms)")
    print("=" * 92)
    print(f"{'pods':>5}{'workers':>9}{'conc':>7}{'rps':>10}{'p50':>9}{'p95':>10}{'p99':>10}{'errors':>9}")
    print("-" * 92)
    for r in runs:
        lm = r["latency_ms"]
        print(f"{r['pods']:>5}{r['workers_per_pod']:>9}{r['concurrency']:>7}"
              f"{r['throughput_rps']:>10}{lm['p50']:>9}{lm['p95']:>10}{lm['p99']:>10}{r['errors']:>9}")
    print("=" * 92)


if __name__ == "__main__":
    main()
