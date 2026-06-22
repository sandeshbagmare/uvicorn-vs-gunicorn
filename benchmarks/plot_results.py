"""
Turn the JSON files written by run_suite.py / loadtest.py into bar charts.

    python benchmarks/plot_results.py --in results/raw --out results/charts

Produces, per endpoint, a throughput chart and a p95-latency chart comparing the
server configs. Degrades gracefully: if matplotlib isn't installed it prints an
ASCII table instead so you still get the comparison.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict


def load(in_dir: str) -> list[dict]:
    out = []
    for path in glob.glob(os.path.join(in_dir, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            pass
    return out


def endpoint_of(label: str) -> str:
    return label.split("__")[-1] if "__" in label else "all"


def config_of(label: str) -> str:
    return label.split("__")[0] if "__" in label else label


def ascii_tables(records: list[dict]) -> None:
    by_ep: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_ep[endpoint_of(r["label"])].append(r)
    for ep, rows in sorted(by_ep.items()):
        print(f"\n# endpoint: {ep}")
        print(f"{'config':<40}{'rps':>10}{'p95 ms':>10}{'PIDs':>8}")
        print("-" * 68)
        for r in sorted(rows, key=lambda x: -x["throughput_rps"]):
            print(f"{config_of(r['label']):<40}{r['throughput_rps']:>10}"
                  f"{r['latency_ms']['p95']:>10}{len(r['pid_counts']):>8}")


def charts(records: list[dict], out_dir: str) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False

    os.makedirs(out_dir, exist_ok=True)
    by_ep: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_ep[endpoint_of(r["label"])].append(r)

    for ep, rows in by_ep.items():
        rows = sorted(rows, key=lambda x: config_of(x["label"]))
        configs = [config_of(r["label"]) for r in rows]
        for metric, getter, ylabel in [
            ("throughput", lambda r: r["throughput_rps"], "requests / second (higher better)"),
            ("p95", lambda r: r["latency_ms"]["p95"], "p95 latency ms (lower better)"),
        ]:
            fig, ax = plt.subplots(figsize=(8, 4.5))
            vals = [getter(r) for r in rows]
            bars = ax.bar(configs, vals)
            ax.set_title(f"{ep}  -  {metric}")
            ax.set_ylabel(ylabel)
            ax.tick_params(axis="x", rotation=20)
            for b, v in zip(bars, vals):
                ax.text(b.get_x() + b.get_width() / 2, v, str(v),
                        ha="center", va="bottom", fontsize=8)
            fig.tight_layout()
            path = os.path.join(out_dir, f"{ep}_{metric}.png")
            fig.savefig(path, dpi=120)
            plt.close(fig)
            print(f"wrote {path}")
    return True


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_dir", default="results/raw")
    p.add_argument("--out", dest="out_dir", default="results/charts")
    args = p.parse_args()

    records = load(args.in_dir)
    if not records:
        print(f"No JSON results in {args.in_dir}. Run benchmarks/run_suite.py first.")
        return
    if not charts(records, args.out_dir):
        print("matplotlib not available -- printing ASCII comparison instead:")
        ascii_tables(records)


if __name__ == "__main__":
    main()
