"""
One-command benchmark suite. Boots each server configuration, runs a matrix of
workloads against it, tears the server (and all its worker children) down, then
prints a side-by-side comparison and writes JSON results.

It is cross-platform: it can drive Uvicorn (1 worker / N workers) on Windows,
Linux and macOS, and Gunicorn+UvicornWorker on Unix-like systems (Gunicorn does
not run on Windows -- see docs/uvicorn-vs-gunicorn.md). On Windows the Gunicorn
rows are skipped automatically with a printed note.

Usage:
    python benchmarks/run_suite.py                  # full default matrix
    python benchmarks/run_suite.py --quick          # smaller/faster matrix
    python benchmarks/run_suite.py --workers 8      # override worker count for the multi-worker rows
    python benchmarks/run_suite.py --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

# Import the load tester from the same folder.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loadtest import run_load_test, print_result, save_result, Result  # noqa: E402

IS_WIN = platform.system() == "Windows"
CPU = os.cpu_count() or 4


def server_specs(host: str, port: int, workers: int):
    """The set of server configurations we benchmark."""
    app = "app.main:app"
    bind = f"{host}:{port}"
    specs = [
        {
            "label": "uvicorn-1worker",
            "server": "Uvicorn, single worker (1 process, 1 event loop)",
            "cmd": [sys.executable, "-m", "uvicorn", app, "--host", host,
                    "--port", str(port), "--workers", "1", "--log-level", "warning"],
            "skip_on_windows": False,
        },
        {
            "label": f"uvicorn-{workers}workers",
            "server": f"Uvicorn, {workers} workers (Uvicorn's own process supervisor)",
            "cmd": [sys.executable, "-m", "uvicorn", app, "--host", host,
                    "--port", str(port), "--workers", str(workers), "--log-level", "warning"],
            "skip_on_windows": False,
        },
        {
            "label": f"gunicorn-{workers}-uvicornworker",
            "server": f"Gunicorn master + {workers} UvicornWorker processes",
            "cmd": [sys.executable, "-m", "gunicorn", app,
                    "-k", "uvicorn.workers.UvicornWorker", "-w", str(workers),
                    "-b", bind, "--log-level", "warning"],
            "skip_on_windows": True,  # Gunicorn needs fcntl -> Unix only
        },
    ]
    return specs


def workload_matrix(quick: bool):
    """(endpoint, total_requests, concurrency) tuples to run against every server."""
    if quick:
        return [
            ("/", 1000, 100),
            ("/async-io", 1000, 100),
            ("/cpu", 300, 50),
        ]
    return [
        ("/",         2000, 200),   # baseline overhead
        ("/async-io", 2000, 200),   # async I/O: where a single event loop shines
        ("/sync-io",  1000, 200),   # blocking I/O: event-loop starvation demo
        ("/cpu",       600, 100),   # CPU bound: where extra processes win
    ]


def _wait_healthy(url: str, timeout_s: float = 25.0) -> bool:
    """Poll /health until the server answers 200 or we give up."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _start(cmd: list[str], cwd: str) -> subprocess.Popen:
    """Start the server in its own process group/session so we can kill the whole tree."""
    kwargs = {"cwd": cwd}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # new session -> killpg later
    return subprocess.Popen(cmd, **kwargs)


def _stop(proc: subprocess.Popen) -> None:
    """Terminate the server and ALL worker children (cross-platform)."""
    if proc.poll() is not None:
        return
    try:
        if IS_WIN:
            # taskkill /T kills the whole process tree (parent + uvicorn/gunicorn workers).
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


def main() -> None:
    p = argparse.ArgumentParser(description="Boot each server config and benchmark it")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--workers", type=int, default=max(2, min(CPU, 4)),
                   help="worker count for the multi-worker rows (default: min(cpu,4))")
    p.add_argument("--quick", action="store_true", help="smaller, faster matrix")
    p.add_argument("--out", default="results/raw", help="dir for per-run JSON")
    args = p.parse_args()

    # Project root = parent of benchmarks/ ; servers must run from here so `app.main:app` imports.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = f"http://{args.host}:{args.port}"
    health = base + "/health"

    print(f"Platform: {platform.system()} | CPUs: {CPU} | worker count for multi-worker rows: {args.workers}")
    print(f"Project root: {project_root}\n")

    all_results: list[Result] = []
    for spec in server_specs(args.host, args.port, args.workers):
        if IS_WIN and spec["skip_on_windows"]:
            print(f"--- SKIP {spec['label']}: Gunicorn is Unix-only (no fcntl on Windows). "
                  f"Run via WSL/Docker -- see docker/ folder. ---\n")
            continue
        if "gunicorn" in spec["label"] and shutil.which("gunicorn") is None and not IS_WIN:
            # gunicorn module may still be importable; only skip if clearly absent
            pass

        print(f"=== Booting: {spec['label']} ===")
        print(f"    {' '.join(spec['cmd'])}")
        proc = _start(spec["cmd"], cwd=project_root)
        try:
            if not _wait_healthy(health):
                print(f"    !! server did not become healthy; skipping {spec['label']}\n")
                continue
            print("    healthy. running workloads...\n")
            for endpoint, total, conc in workload_matrix(args.quick):
                label = f"{spec['label']}__{endpoint.strip('/') or 'root'}"
                res = asyncio.run(run_load_test(
                    base + endpoint, total, conc, label=label, server=spec["server"],
                ))
                print_result(res)
                save_result(res, os.path.join(project_root, args.out))
                all_results.append(res)
        finally:
            _stop(proc)
            print(f"--- stopped {spec['label']} ---\n")
            time.sleep(1.0)  # let the port free up before the next boot

    _print_comparison(all_results)


def _print_comparison(results: list[Result]) -> None:
    if not results:
        print("No results collected.")
        return
    print("\n" + "=" * 92)
    print("COMPARISON (throughput req/s, p95 latency ms, distinct worker PIDs)")
    print("=" * 92)
    header = f"{'config / endpoint':<46}{'rps':>10}{'p95 ms':>10}{'p99 ms':>10}{'PIDs':>8}"
    print(header)
    print("-" * 92)
    for r in results:
        print(f"{r.label:<46}{r.throughput_rps:>10}{r.latency_ms['p95']:>10}"
              f"{r.latency_ms['p99']:>10}{len(r.pid_counts):>8}")
    print("=" * 92)
    print("Tip: compare the same endpoint across configs. /cpu should reward more workers;")
    print("/async-io should already be fast on a single worker; /sync-io should stay slow until")
    print("you add workers (blocking work can't be saved by the event loop).")


if __name__ == "__main__":
    main()
