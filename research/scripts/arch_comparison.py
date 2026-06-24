"""
Architecture A/B comparison over 10,000 requests
================================================

Two ways to deploy the SAME total worker count, then fire an identical load at each
and compare latency:

  Architecture A -- "slim containers" (the Kubernetes-native pattern)
      N pods, each running ONE Uvicorn worker. Kubernetes (here: a round-robin
      Service emulation) manages each pod independently.
        e.g. 4 pods x 1 worker = 4 worker processes

  Architecture B -- "fat pods" (Gunicorn manages the workers)
      M pods, each running Gunicorn with several Uvicorn workers. Kubernetes manages
      the pods; Gunicorn manages the workers inside each pod.
        e.g. 1 pod x 4 workers = 4 worker processes

Both architectures here use the SAME number of total worker processes (default 4), so
any latency difference is due to PACKAGING, not raw capacity -- which is exactly the
"pods vs workers" question.

Honesty notes (same as the rest of research/):
  * Gunicorn does not run on Windows (needs fcntl). On Windows, Architecture B falls
    back to `uvicorn --workers` -- same fat-pod shape, just without Gunicorn's
    supervisor. On Linux it uses real Gunicorn + UvicornWorker.
  * A single machine cannot give a real pod its own dedicated CPUs, so ABSOLUTE numbers
    are a same-hardware comparison, not a cluster ceiling. Use research/manifests/ +
    k8s_loadtest.sh for ground truth on a real cluster.

Usage
-----
    python research/scripts/arch_comparison.py --requests 10000 --concurrency 100 \
        --endpoint /async-io --slim-pods 4 --fat-pods 1 --fat-workers 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import time

# Reuse the emulation helpers (same folder).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster_emulation import (  # noqa: E402
    pod_command, start_pod, stop_pod, wait_healthy, run_against_pods, PROJECT_ROOT,
)

IS_WIN = platform.system() == "Windows"
CPU = os.cpu_count() or 4


def run_architecture(label: str, pod_specs: list[tuple[int, int]], endpoint: str,
                     total: int, concurrency: int) -> dict | None:
    """Boot the given pods (port, workers), fire the load, tear down, return metrics."""
    ports = [p for (p, _) in pod_specs]
    procs = []
    print(f"\n=== {label} ===")
    for port, workers in pod_specs:
        cmd, desc = pod_command(port, workers)
        print(f"    pod on :{port}  ->  {desc}")
        procs.append(start_pod(cmd))
    try:
        if not all(wait_healthy(p) for p in ports):
            print(f"    !! not all pods healthy; skipping {label}")
            return None
        print(f"    all {len(ports)} pod(s) healthy. firing {total} requests "
              f"(concurrency {concurrency}) at {endpoint} ...")
        t0 = time.perf_counter()
        res = asyncio.run(run_against_pods(ports, endpoint, total, concurrency))
        res["wall_clock_s"] = round(time.perf_counter() - t0, 2)
        res["architecture"] = label
        res["pod_specs"] = [{"port": p, "workers": w} for (p, w) in pod_specs]
        res["total_worker_processes"] = sum(w for (_, w) in pod_specs)
        lm = res["latency_ms"]
        print(f"    DONE in {res['wall_clock_s']}s: "
              f"rps={res['throughput_rps']}  ok={res['ok']}/{res['requests']}  "
              f"errors={res['errors']}  distinct_pids={res['distinct_pids']}")
        print(f"    latency ms: p50={lm['p50']}  p90={lm['p90']}  p95={lm['p95']}  "
              f"p99={lm['p99']}  max={lm['max']}  mean={lm['mean']}")
        return res
    finally:
        for pr in procs:
            stop_pod(pr)
        print(f"    stopped {label}")
        time.sleep(1.5)


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare slim-pods vs fat-pods over a fixed request count")
    ap.add_argument("--requests", type=int, default=10000)
    ap.add_argument("--concurrency", type=int, default=100)
    ap.add_argument("--endpoint", default="/async-io")
    ap.add_argument("--slim-pods", type=int, default=4, help="Architecture A: number of 1-worker pods")
    ap.add_argument("--fat-pods", type=int, default=1, help="Architecture B: number of fat pods")
    ap.add_argument("--fat-workers", type=int, default=4, help="Architecture B: workers per fat pod")
    ap.add_argument("--base-port", type=int, default=9300)
    ap.add_argument("--out", default="research/data")
    args = ap.parse_args()

    slim_total = args.slim_pods * 1
    fat_total = args.fat_pods * args.fat_workers
    bp = args.base_port

    # Architecture A: N slim pods, 1 worker each.
    arch_a_specs = [(bp + i, 1) for i in range(args.slim_pods)]
    # Architecture B: M fat pods, fat_workers each (use a disjoint port range).
    arch_b_specs = [(bp + 100 + i, args.fat_workers) for i in range(args.fat_pods)]

    print("=" * 90)
    print("ARCHITECTURE COMPARISON  (10k-request style load)")
    print(f"  platform={platform.system()}  host_cpus={CPU}")
    print(f"  endpoint={args.endpoint}  requests={args.requests}  concurrency={args.concurrency}")
    print(f"  A (slim): {args.slim_pods} pods x 1 worker  = {slim_total} worker processes")
    print(f"  B (fat) : {args.fat_pods} pod(s) x {args.fat_workers} workers = {fat_total} worker processes")
    if IS_WIN:
        print("  NOTE: Windows -> Architecture B uses `uvicorn --workers` (Gunicorn is Unix-only).")
    if slim_total != fat_total:
        print(f"  NOTE: total workers differ ({slim_total} vs {fat_total}); this is a capacity+packaging test.")
    else:
        print(f"  Fair test: both architectures use {slim_total} total worker processes.")
    over = max(slim_total, fat_total) + 1
    if over > CPU:
        print(f"  WARNING: peak processes (~{over}) > host cpus ({CPU}); some core contention expected.")
    print("=" * 90)

    results = []
    a = run_architecture(f"A_slim_{args.slim_pods}pods_x1worker", arch_a_specs,
                         args.endpoint, args.requests, args.concurrency)
    if a:
        results.append(a)
    b = run_architecture(f"B_fat_{args.fat_pods}pod_x{args.fat_workers}workers", arch_b_specs,
                         args.endpoint, args.requests, args.concurrency)
    if b:
        results.append(b)

    # Save + print side-by-side
    os.makedirs(os.path.join(PROJECT_ROOT, args.out), exist_ok=True)
    safe = args.endpoint.strip("/").replace("/", "_") or "root"
    out_path = os.path.join(PROJECT_ROOT, args.out, f"arch_comparison__{safe}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"meta": vars(args), "results": results}, f, indent=2)

    print("\n" + "=" * 90)
    print(f"SIDE-BY-SIDE  ({args.requests} requests, {args.endpoint}, concurrency {args.concurrency})")
    print("=" * 90)
    hdr = f"{'architecture':<34}{'rps':>9}{'p50':>8}{'p95':>9}{'p99':>9}{'max':>9}{'ok/err':>12}"
    print(hdr)
    print("-" * 90)
    for r in results:
        lm = r["latency_ms"]
        print(f"{r['architecture']:<34}{r['throughput_rps']:>9}{lm['p50']:>8}{lm['p95']:>9}"
              f"{lm['p99']:>9}{lm['max']:>9}{(str(r['ok']) + '/' + str(r['errors'])):>12}")
    print("=" * 90)
    if len(results) == 2:
        a_p99 = results[0]["latency_ms"]["p99"]
        b_p99 = results[1]["latency_ms"]["p99"]
        better = results[0]["architecture"] if a_p99 < b_p99 else results[1]["architecture"]
        print(f"Lower p99 (tail latency): {better}  "
              f"(A p99={a_p99} ms vs B p99={b_p99} ms)")
        print("Both architectures use the same total worker count, so this isolates the PACKAGING effect.")
    print(f"\nsaved -> {out_path}")


if __name__ == "__main__":
    main()
