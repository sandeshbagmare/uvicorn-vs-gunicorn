"""
Async load tester for the Uvicorn-vs-Gunicorn demo.

It fires `--requests` total requests at a target endpoint with at most
`--concurrency` of them in flight at once, then reports:

  * throughput            requests / second (wall clock)
  * latency percentiles   min / p50 / p90 / p95 / p99 / max / mean
  * success vs error      HTTP status tally + transport errors
  * worker distribution   how many distinct server PIDs answered, and the split
                          (this is the proof that N workers are really sharing load)

Why a custom tester instead of `ab`/`wrk`? Two reasons: it is pure-Python so it
runs identically on Windows/Linux/WSL with no extra binaries, and it reads the
`pid` field our demo app returns so we can show worker spread -- something the
generic tools cannot do.

Examples
--------
    # 1000 requests, 100 in parallel, against the async endpoint
    python benchmarks/loadtest.py --url http://127.0.0.1:8000 --endpoint /async-io \
        --requests 1000 --concurrency 100

    # same but write a JSON record we can chart later
    python benchmarks/loadtest.py --endpoint /cpu --requests 500 --concurrency 50 \
        --label "uvicorn-4w-cpu" --out results/raw
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass, field

import httpx


@dataclass
class Result:
    label: str
    url: str
    requests: int
    concurrency: int
    wall_time_s: float
    throughput_rps: float
    ok: int
    errors: int
    status_counts: dict
    pid_counts: dict
    latency_ms: dict
    server: str = ""
    notes: str = ""
    # raw per-request latencies kept out of the printed summary but useful for charts
    _latencies_ms: list = field(default_factory=list, repr=False)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile on an already-sorted list. Returns 0.0 if empty."""
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round((pct / 100.0) * len(sorted_vals) + 0.5)) - 1))
    return sorted_vals[k]


async def _one(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore,
               latencies: list, statuses: Counter, pids: Counter, errors: list):
    async with sem:
        t0 = time.perf_counter()
        try:
            r = await client.get(url)
            latencies.append((time.perf_counter() - t0) * 1000.0)
            statuses[r.status_code] += 1
            # The demo app returns {"pid": ...}; tolerate non-JSON / other apps.
            try:
                pid = r.json().get("pid")
                if pid is not None:
                    pids[pid] += 1
            except Exception:
                pass
        except Exception as exc:  # transport error, timeout, connection refused...
            latencies.append((time.perf_counter() - t0) * 1000.0)
            errors.append(type(exc).__name__)


async def run_load_test(url: str, total: int, concurrency: int, label: str = "",
                        timeout: float = 30.0, server: str = "") -> Result:
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: Counter = Counter()
    pids: Counter = Counter()
    errors: list[str] = []

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        # Warm up one request so first-hit import/JIT costs don't skew p99.
        try:
            await client.get(url)
        except Exception:
            pass

        wall0 = time.perf_counter()
        await asyncio.gather(*[
            _one(client, url, sem, latencies, statuses, pids, errors)
            for _ in range(total)
        ])
        wall = time.perf_counter() - wall0

    ok = sum(c for s, c in statuses.items() if 200 <= s < 400)
    err = len(errors) + sum(c for s, c in statuses.items() if s >= 400)
    s = sorted(latencies)
    lat = {
        "min": round(min(s), 2) if s else 0.0,
        "p50": round(_percentile(s, 50), 2),
        "p90": round(_percentile(s, 90), 2),
        "p95": round(_percentile(s, 95), 2),
        "p99": round(_percentile(s, 99), 2),
        "max": round(max(s), 2) if s else 0.0,
        "mean": round(statistics.fmean(s), 2) if s else 0.0,
    }
    return Result(
        label=label or url,
        url=url,
        requests=total,
        concurrency=concurrency,
        wall_time_s=round(wall, 3),
        throughput_rps=round(total / wall, 1) if wall > 0 else 0.0,
        ok=ok,
        errors=err,
        status_counts=dict(statuses),
        pid_counts=dict(pids),
        latency_ms=lat,
        server=server,
        notes=(f"transport_errors={Counter(errors)}" if errors else ""),
        _latencies_ms=s,
    )


def print_result(res: Result) -> None:
    """Pretty console summary. Uses `rich` if available, else plain text."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        t = Table(title=f"[bold]{res.label}[/bold]  ({res.url})", show_lines=False)
        t.add_column("metric", style="cyan", no_wrap=True)
        t.add_column("value", style="white")
        t.add_row("requests", str(res.requests))
        t.add_row("concurrency", str(res.concurrency))
        t.add_row("wall time", f"{res.wall_time_s} s")
        t.add_row("throughput", f"[bold green]{res.throughput_rps} req/s[/bold green]")
        t.add_row("ok / errors", f"{res.ok} / {res.errors}")
        t.add_row("latency p50", f"{res.latency_ms['p50']} ms")
        t.add_row("latency p95", f"{res.latency_ms['p95']} ms")
        t.add_row("latency p99", f"{res.latency_ms['p99']} ms")
        t.add_row("latency max", f"{res.latency_ms['max']} ms")
        t.add_row("distinct worker PIDs", f"[bold]{len(res.pid_counts)}[/bold]  {res.pid_counts}")
        if res.notes:
            t.add_row("notes", res.notes)
        console.print(t)
    except Exception:
        print(f"\n=== {res.label} ({res.url}) ===")
        print(f"requests={res.requests} concurrency={res.concurrency} wall={res.wall_time_s}s")
        print(f"throughput={res.throughput_rps} req/s  ok={res.ok} errors={res.errors}")
        print(f"latency ms: {res.latency_ms}")
        print(f"distinct worker PIDs={len(res.pid_counts)}  {res.pid_counts}")
        if res.notes:
            print(f"notes: {res.notes}")


def save_result(res: Result, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (res.label or "run"))
    path = os.path.join(out_dir, f"{safe}.json")
    payload = asdict(res)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def main() -> None:
    p = argparse.ArgumentParser(description="Async load tester (Uvicorn vs Gunicorn demo)")
    p.add_argument("--url", default="http://127.0.0.1:8000", help="base URL of the server")
    p.add_argument("--endpoint", default="/async-io", help="path to hit, e.g. /, /cpu, /sync-io")
    p.add_argument("--requests", type=int, default=1000, help="total requests to send")
    p.add_argument("--concurrency", type=int, default=100, help="max requests in flight")
    p.add_argument("--label", default="", help="label for the result record")
    p.add_argument("--server", default="", help="free-text server description for the record")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--out", default="", help="if set, write JSON result into this dir")
    args = p.parse_args()

    target = args.url.rstrip("/") + args.endpoint
    label = args.label or f"{args.endpoint.strip('/') or 'root'}-c{args.concurrency}-n{args.requests}"
    res = asyncio.run(run_load_test(
        target, args.requests, args.concurrency, label=label,
        timeout=args.timeout, server=args.server,
    ))
    print_result(res)
    if args.out:
        path = save_result(res, args.out)
        print(f"\nsaved -> {path}")


if __name__ == "__main__":
    main()
