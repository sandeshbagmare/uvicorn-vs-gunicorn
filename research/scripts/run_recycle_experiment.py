"""
One-command worker-recycling experiment (the measured half of
research/memory-leaks-and-worker-recycling.md).

Boots recycle_app.py under Uvicorn in four configurations and drives the same
load at each, so you can SEE — with real numbers on this machine — how worker
recycling interacts with the "one worker per pod, a couple of pods" topology:

  1. baseline-norecycle : 2 workers, no recycling. RSS climbs forever (-> OOM).
  2. single-worker-death: 1 worker + uvicorn --limit-max-requests. The worker
                          hits the limit and the WHOLE server exits (uvicorn has
                          no master to respawn a lone worker) -> total outage.
  3. sync-recycle       : 2 workers + uvicorn --limit-max-requests (NO jitter —
                          uvicorn has no jitter flag). Both workers recycle at the
                          same count, near-simultaneously -> a shared stall window
                          (the 529/530 analog: requests wait with nobody to serve).
  4. jitter-recycle     : 2 workers, app-level randomized self-recycle (base+jitter)
                          -> staggered restarts -> the other worker always serves
                          -> the stall window disappears.

A modeled "heavy app" boot delay (RECYCLE_BOOT_DELAY_S) makes each respawn take
real time, which is what turns a synchronized restart into a visible outage —
exactly like a LangGraph app whose workers re-import + re-run checkpointer.setup()
on boot.

Run:  python research/scripts/run_recycle_experiment.py
Out:  research/data/recycle_experiment.json  (+ per-scenario recycle__*.json)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from recycle_loadtest import run as drive, summarize  # noqa: E402

REPO = HERE.parents[1]
DATA = HERE.parents[0] / "data"
PORT = 8020
REQUESTS = 400
CONCURRENCY = 2
BOOT_DELAY = 1.2          # models a heavy LangGraph app's slow (re)boot
LEAK_KB = 40              # synthetic, labelled — makes the RSS sawtooth visible

SCENARIOS = [
    # label, workers, extra uvicorn args, extra env
    ("baseline-norecycle", 2, [], {}),
    ("single-worker-death", 1, ["--limit-max-requests", "120"], {}),
    ("sync-recycle", 2, ["--limit-max-requests", "60"], {}),
    ("jitter-recycle", 2, [], {"RECYCLE_SELF_LIMIT": "60", "RECYCLE_SELF_JITTER": "60"}),
]


def wait_health(port: int, timeout: float) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def run_scenario(label: str, workers: int, extra: list[str], extra_env: dict) -> dict:
    env = dict(os.environ)
    env.update({
        "RECYCLE_USE_PG": "0",          # isolate the recycle mechanism (PG memory is measured separately)
        "RECYCLE_LEAK_KB": str(LEAK_KB),
        "RECYCLE_BOOT_DELAY_S": str(BOOT_DELAY),
        "PYTHONUNBUFFERED": "1",
    })
    env.update(extra_env)
    cmd = [sys.executable, "-m", "uvicorn", "research.scripts.recycle_app:app",
           "--workers", str(workers), "--port", str(PORT), "--no-access-log", *extra]
    print(f"\n=== {label}: workers={workers} {' '.join(extra)} {extra_env or ''} ===")
    proc = subprocess.Popen(cmd, cwd=str(REPO), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_health(PORT, timeout=20 + BOOT_DELAY * workers):
            print("  server did not come up; skipping")
            return {"label": label, "error": "no-start"}
        series = drive(f"http://127.0.0.1:{PORT}", REQUESTS, CONCURRENCY, "/invoke", timeout=6.0)
        series = [r for r in series if r is not None]
        summary = summarize(series, label, workers)
        (DATA / f"recycle__{label}.json").write_text(json.dumps({"summary": summary, "series": series}, indent=2))
        s = summary
        print(f"  ok {s['ok']}/{s['requests']}  failed {s['failed']} ({s['failed_pct']}%)  "
              f"recycles {s['recycles']}")
        print(f"  latency p50 {s['p50_ms']}  p95 {s['p95_ms']}  p99 {s['p99_ms']}  max {s['max_ms']}ms  "
              f"| stalled>1s {s['slow_over_1s']}")
        print(f"  RSS sawtooth {s['rss_min_max_mb'][0]}->{s['rss_min_max_mb'][1]}MB")
        return summary
    finally:
        kill_tree(proc)
        time.sleep(1.0)  # let the port free between scenarios


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    results = []
    for label, workers, extra, extra_env in SCENARIOS:
        results.append(run_scenario(label, workers, extra, extra_env))

    combined = {
        "params": {"requests": REQUESTS, "concurrency": CONCURRENCY,
                   "boot_delay_s": BOOT_DELAY, "leak_kb_per_request": LEAK_KB,
                   "note": "Gunicorn N/A on Windows; uvicorn --limit-max-requests is the same idea."},
        "results": results,
    }
    (DATA / "recycle_experiment.json").write_text(json.dumps(combined, indent=2))

    print("\n================ SUMMARY ================")
    hdr = f"{'scenario':<22}{'ok%':>6}{'failed':>8}{'recyc':>7}{'p99ms':>8}{'maxms':>9}{'stall>1s':>9}"
    print(hdr)
    for r in results:
        if r.get("error"):
            print(f"{r['label']:<22}{'ERR':>6}")
            continue
        okpct = round(100 * r["ok"] / max(1, r["requests"]), 1)
        print(f"{r['label']:<22}{okpct:>6}{r['failed']:>8}{r['recycles']:>7}"
              f"{r['p99_ms']:>8}{r['max_ms']:>9}{r['slow_over_1s']:>9}")
    print(f"\nwrote {DATA / 'recycle_experiment.json'}")


if __name__ == "__main__":
    main()
