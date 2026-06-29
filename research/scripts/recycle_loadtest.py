"""
Driver for the worker-recycling demo (recycle_app.py).

Fires N requests at /invoke and records, for every response, the worker identity
(pid, boot_id, per-worker req_count, rss_mb) plus any failures. From that stream
it reconstructs:

  - the RSS sawtooth        : memory climbs, then drops when a worker recycles
  - recycle events          : a new boot_id (or a req_count reset) for a pid
  - the outage window        : consecutive FAILED requests while worker(s) were
                               mid-respawn -> the measured analog of the 529/530
                               the user sees when all single-worker pods recycle
                               at once.

Usage:
  python research/scripts/recycle_loadtest.py --url http://127.0.0.1:8010 \
      --requests 1600 --concurrency 2 --label jitter

Writes research/data/recycle__<label>.json
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _get(url: str, timeout: float):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = json.loads(r.read().decode())
            return {"ok": True, "status": r.status, "ms": round((time.perf_counter() - t0) * 1000, 1), **body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "ms": round((time.perf_counter() - t0) * 1000, 1), "err": f"HTTP{e.code}"}
    except Exception as e:  # connection refused/reset/timeout while worker respawns
        return {"ok": False, "status": "ERR", "ms": round((time.perf_counter() - t0) * 1000, 1),
                "err": type(e).__name__}


def run(url: str, total: int, concurrency: int, endpoint: str, timeout: float) -> list[dict]:
    target = url.rstrip("/") + endpoint
    results: list[dict] = [None] * total  # type: ignore
    counter = {"i": 0}
    lock = threading.Lock()
    t_start = time.perf_counter()

    def worker():
        while True:
            with lock:
                i = counter["i"]
                if i >= total:
                    return
                counter["i"] = i + 1
            res = _get(target, timeout)
            res["i"] = i
            res["t"] = round(time.perf_counter() - t_start, 3)
            results[i] = res

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def summarize(series: list[dict], label: str, workers: int) -> dict:
    ok = [r for r in series if r["ok"]]
    fails = [r for r in series if not r["ok"]]
    # per-boot_id sawtooth
    boots: dict[str, dict] = {}
    for r in ok:
        b = r.get("boot_id")
        if b is None:
            continue
        d = boots.setdefault(b, {"pid": r.get("pid"), "n": 0, "rss_min": 1e9, "rss_max": 0, "first_i": r["i"], "last_i": r["i"]})
        d["n"] += 1
        d["rss_min"] = min(d["rss_min"], r["rss_mb"])
        d["rss_max"] = max(d["rss_max"], r["rss_mb"])
        d["last_i"] = r["i"]
    pids = sorted({b["pid"] for b in boots.values()})
    # On Windows (spawn) a respawn gets a NEW pid too, so count recycles as the
    # number of worker generations beyond the initial pool: boot_ids - workers.
    recycles = max(0, len(boots) - workers)
    # longest consecutive failure streak == worst outage burst
    longest = cur = 0
    for r in series:
        if not r["ok"]:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    rss_span = [round(min((b["rss_min"] for b in boots.values()), default=0), 1),
                round(max((b["rss_max"] for b in boots.values()), default=0), 1)]
    lat = sorted(r["ms"] for r in ok)
    return {
        "label": label,
        "requests": len(series),
        "ok": len(ok),
        "failed": len(fails),
        "failed_pct": round(100 * len(fails) / max(1, len(series)), 2),
        "distinct_pids": len(pids),
        "distinct_boot_ids": len(boots),
        "recycles": recycles,
        "longest_fail_burst": longest,
        # the synchronized-restart outage shows up here: requests that had to WAIT
        # for a worker to finish (re)booting before they could be served.
        "p50_ms": _pct(lat, 50),
        "p95_ms": _pct(lat, 95),
        "p99_ms": _pct(lat, 99),
        "max_ms": round(lat[-1], 1) if lat else 0,
        "slow_over_1s": sum(1 for x in lat if x > 1000),
        "rss_min_max_mb": rss_span,
        "fail_kinds": _counts([r.get("err") for r in fails]),
    }


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round((p / 100) * (len(sorted_vals) - 1)))))
    return round(sorted_vals[k], 1)


def _counts(xs):
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8010")
    ap.add_argument("--requests", type=int, default=1600)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--endpoint", default="/invoke")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--label", default="run")
    ap.add_argument("--workers", type=int, default=2, help="worker count, for recycle accounting")
    args = ap.parse_args()

    series = run(args.url, args.requests, args.concurrency, args.endpoint, args.timeout)
    series = [r for r in series if r is not None]
    summary = summarize(series, args.label, args.workers)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / f"recycle__{args.label}.json"
    out.write_text(json.dumps({"summary": summary, "series": series}, indent=2))

    s = summary
    print(f"[{args.label}] {s['requests']} reqs | ok {s['ok']} | FAILED {s['failed']} ({s['failed_pct']}%) "
          f"| longest fail burst {s['longest_fail_burst']}")
    print(f"  recycles {s['recycles']} (boot_ids {s['distinct_boot_ids']}) | "
          f"RSS {s['rss_min_max_mb'][0]}->{s['rss_min_max_mb'][1]}MB sawtooth")
    print(f"  latency p50 {s['p50_ms']}ms  p95 {s['p95_ms']}ms  p99 {s['p99_ms']}ms  max {s['max_ms']}ms  "
          f"| stalled>1s: {s['slow_over_1s']} reqs")
    print(f"  wrote {out}")


if __name__ == "__main__":
    main()
