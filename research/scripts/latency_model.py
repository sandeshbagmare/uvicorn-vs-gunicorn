"""
Analytical latency model for "N pods x M workers" on Kubernetes
==============================================================

Why this exists
---------------
A single laptop cannot honestly measure "4 pods x 4 CPUs each" -- it does not have
16 dedicated cores, and on Windows there is no Gunicorn and no uvloop. So instead of
pretending, we MODEL the cluster using queueing theory and a measured single-pod
service time. This predicts latency under load on a real cluster where each pod truly
gets its own 4 CPUs.

The model
---------
We treat the whole deployment as an M/M/c-style queue:
  * Offered load          lambda   (requests/second arriving at the Service)
  * Per-request service   S        (seconds a worker is busy with one request)
  * Servers               c        = pods * workers_per_pod   (concurrent "lanes")
  * Service rate/lane     mu       = 1 / S
  * System capacity       C        = c * mu  (max sustainable rps before saturation)
  * Utilisation           rho      = lambda / C

Mean waiting time uses the Erlang-C (M/M/c) formula; we then report:
  * mean latency  W   = Wq + S
  * a p95/p99 approximation for the M/M/c sojourn time

Two regimes the model captures (and that match our empirical findings):
  * CPU-bound work: a worker is busy on the CPU for the whole request -> c = total
    worker PROCESSES is the right server count (GIL: 1 core per process).
  * Async I/O-bound work: one worker overlaps many awaited requests, so the effective
    concurrency per worker is >> 1. You raise `--io-concurrency` to reflect that one
    Uvicorn worker keeps K awaited requests in flight; then c = pods*workers*K.

Usage
-----
    # CPU-bound: service time 25 ms/req, 4 pods x 4 workers, sweep load
    python research/scripts/latency_model.py --service-ms 25 \
        --pods 1 2 3 4 --workers 4 --lambda 100 200 400 600 800

    # I/O-bound: 50 ms downstream call, each worker overlaps ~40 awaits
    python research/scripts/latency_model.py --service-ms 50 --io-concurrency 40 \
        --pods 1 3 4 --workers 4 --lambda 200 500 1000 2000

Calibrate --service-ms from a single real pod:
    service_ms ~= 1000 / (single_pod_max_rps / workers_per_pod)    # CPU-bound
"""

from __future__ import annotations

import argparse
import json
import math
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def erlang_c(c: int, a: float) -> float:
    """Probability an arriving request must queue (Erlang-C). a = offered load in Erlangs = lambda*S.

    Computed with a numerically stable recurrence (no large factorials/powers), so it
    works for large server counts c (e.g. async I/O with hundreds of effective lanes).
    """
    if a >= c:
        return 1.0  # saturated; everyone queues
    # Iteratively build the Erlang-B blocking probability:
    #   B(0) = 1;  B(k) = (a * B(k-1)) / (k + a * B(k-1))
    b = 1.0
    for k in range(1, c + 1):
        b = (a * b) / (k + a * b)
    # Erlang-C from Erlang-B:  C = B / (1 - rho*(1-B)),  rho = a/c
    rho = a / c
    denom = 1.0 - rho * (1.0 - b)
    return b / denom if denom > 0 else 1.0


def mmc_metrics(lam: float, service_s: float, c: int) -> dict:
    """M/M/c queue metrics for arrival rate lam (req/s), service time service_s (s), c servers."""
    mu = 1.0 / service_s
    a = lam * service_s            # offered load in Erlangs
    rho = a / c                    # utilisation per server
    capacity = c * mu              # max sustainable rps

    if rho >= 1.0:
        return {
            "stable": False, "rho": round(rho, 3), "capacity_rps": round(capacity, 1),
            "mean_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None,
            "note": "OVERLOADED: arrival rate >= capacity; queue grows without bound",
        }

    pq = erlang_c(c, a)
    wq = pq / (c * mu - lam)       # mean wait in queue (s)
    w = wq + service_s             # mean sojourn time (s)

    # Sojourn-time distribution for M/M/c is approximately exponential in the tail with
    # rate theta = c*mu - lam for the queued part; we use a standard tail approximation.
    theta = c * mu - lam
    # P(W > t) ~ pq * exp(-theta * t) for the waiting component, plus service.
    def quantile(p: float) -> float:
        # invert tail: find t where P(wait>t) = 1-p (only meaningful while pq>1-p)
        target = 1.0 - p
        if pq <= target:
            wait_q = 0.0
        else:
            wait_q = math.log(pq / target) / theta
        return (wait_q + service_s) * 1000.0  # ms, +1 service time

    return {
        "stable": True,
        "rho": round(rho, 3),
        "capacity_rps": round(capacity, 1),
        "mean_ms": round(w * 1000.0, 2),
        "p50_ms": round(quantile(0.50), 2),
        "p95_ms": round(quantile(0.95), 2),
        "p99_ms": round(quantile(0.99), 2),
        "note": "",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Predict N-pods x M-workers latency vs load (queueing model)")
    ap.add_argument("--service-ms", type=float, required=True,
                    help="per-request service time in ms (time one worker is busy)")
    ap.add_argument("--pods", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--workers", type=int, default=4, help="workers per pod")
    ap.add_argument("--lambda", dest="lam", type=float, nargs="+",
                    default=[100, 200, 400, 600, 800, 1000],
                    help="offered load(s) in requests/second")
    ap.add_argument("--io-concurrency", type=int, default=1,
                    help="awaited requests one worker overlaps (1 = CPU-bound; >1 = async I/O)")
    ap.add_argument("--out", default="research/data/model_predictions.json")
    args = ap.parse_args()

    service_s = args.service_ms / 1000.0
    print("=" * 100)
    print("ANALYTICAL LATENCY MODEL  (M/M/c queue)")
    print(f"  service_time={args.service_ms} ms/req   workers/pod={args.workers}   "
          f"io_concurrency/worker={args.io_concurrency}")
    print(f"  regime = {'ASYNC I/O-bound' if args.io_concurrency > 1 else 'CPU-bound'}")
    print("=" * 100)
    print(f"{'pods':>5}{'lanes(c)':>10}{'cap rps':>10}{'load':>8}{'rho':>7}"
          f"{'mean':>9}{'p50':>9}{'p95':>10}{'p99':>10}  status")
    print("-" * 100)

    out: list[dict] = []
    for pods in args.pods:
        c = pods * args.workers * args.io_concurrency
        for lam in args.lam:
            m = mmc_metrics(lam, service_s, c)
            row = {"pods": pods, "workers_per_pod": args.workers,
                   "io_concurrency": args.io_concurrency, "lanes": c,
                   "offered_rps": lam, **m}
            out.append(row)
            if m["stable"]:
                print(f"{pods:>5}{c:>10}{m['capacity_rps']:>10}{lam:>8.0f}{m['rho']:>7}"
                      f"{m['mean_ms']:>9}{m['p50_ms']:>9}{m['p95_ms']:>10}{m['p99_ms']:>10}  ok")
            else:
                print(f"{pods:>5}{c:>10}{m['capacity_rps']:>10}{lam:>8.0f}{m['rho']:>7}"
                      f"{'-':>9}{'-':>9}{'-':>10}{'-':>10}  OVERLOADED")
        print("-" * 100)

    os.makedirs(os.path.dirname(os.path.join(PROJECT_ROOT, args.out)), exist_ok=True)
    with open(os.path.join(PROJECT_ROOT, args.out), "w", encoding="utf-8") as f:
        json.dump({"meta": vars(args), "predictions": out}, f, indent=2)
    print(f"\nsaved -> {args.out}")
    print("\nHow to read it:")
    print("  * cap rps  = max sustainable throughput before the queue blows up (c / service_time).")
    print("  * rho      = utilisation; keep < ~0.7-0.8 for healthy tail latency.")
    print("  * Adding pods raises c -> raises capacity -> lowers rho at the same load -> lower p95/p99.")
    print("  * OVERLOADED rows show where that pod count CANNOT serve the load (need more pods).")


if __name__ == "__main__":
    main()
