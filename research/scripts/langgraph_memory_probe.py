"""
LangGraph + PostgresSaver memory probe — does the worker's memory really grow
request-after-request, and if so, why?

This is the *measured* half of the "memory leaks & worker recycling" research
(see research/memory-leaks-and-worker-recycling.md). It drives a real LangGraph
graph compiled with a real PostgresSaver checkpointer against a real Postgres,
and samples the **process RSS**, the **Python heap** (tracemalloc), the **thread
count** and the **live-object count** every N invocations. It writes a JSON time
series you can plot or eyeball, plus a verdict line.

Why this matters: the user's report is "LangGraph app with PostgresSaver, no
cache — memory gradually grows request after request, then the worker restarts
hard (529/530)." Before reaching for Gunicorn --max-requests (a band-aid), we
want to KNOW whether memory truly grows, and separate three different causes:

  1. true leak      -> RSS climbs and never comes back, even after gc.collect()
  2. cyclic garbage -> RSS climbs but gc.collect() reclaims it (a GC-tuning issue)
  3. fragmentation  -> heap (tracemalloc) is flat but RSS drifts up and plateaus
                       (CPython rarely returns freed arenas to the OS)

It runs two configurations so you can see the difference:

  A) "shared-pool"   : compile the graph ONCE, one shared psycopg ConnectionPool,
                       reuse both across every request. This is the recommended
                       pattern (LangChain's own guidance).
  B) "per-request"   : build a NEW graph and a NEW PostgresSaver connection on
                       EVERY request. This is a common real-world mistake and a
                       reliable way to manufacture growth — included as a contrast.

Usage:
    # point at any Postgres; defaults to the local one used in the research run
    set LG_PG_URI=postgresql://postgres:password@127.0.0.1:5432/lg_memtest
    python research/scripts/langgraph_memory_probe.py --config shared-pool --requests 3000
    python research/scripts/langgraph_memory_probe.py --config per-request --requests 3000

Output: research/data/langgraph_mem__<config>.json
"""

from __future__ import annotations

import argparse
import gc
import json
import operator
import os
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Annotated, TypedDict

import psutil
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph

DEFAULT_URI = "postgresql://postgres:password@127.0.0.1:5432/lg_memtest"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


# --------------------------------------------------------------------------- #
# The graph under test. Two pure-Python nodes that append to a growing message
# list and do a little work, so the only thing exercising memory is the LangGraph
# runtime + the checkpointer (no LLM/network — that is deliberately out of scope:
# we are measuring the framework + saver, not a model call).
# --------------------------------------------------------------------------- #
class State(TypedDict):
    messages: Annotated[list, operator.add]
    n: int


def node_user(state: State) -> dict:
    return {"messages": [f"user turn {state['n']}"], "n": state["n"]}


def node_assistant(state: State) -> dict:
    # a touch of allocation per node, like a real handler building a response
    blob = [state["n"] * i % 7 for i in range(200)]
    return {"messages": [f"assistant reply {sum(blob)}"]}


def build_graph(checkpointer):
    b = StateGraph(State)
    b.add_node("user", node_user)
    b.add_node("assistant", node_assistant)
    b.add_edge(START, "user")
    b.add_edge("user", "assistant")
    b.add_edge("assistant", END)
    return b.compile(checkpointer=checkpointer)


# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #
def snapshot(proc: psutil.Process, i: int, do_gc: bool) -> dict:
    rss_before = proc.memory_info().rss
    collected = gc.collect() if do_gc else 0
    rss_after = proc.memory_info().rss
    cur, peak = tracemalloc.get_traced_memory()
    return {
        "request": i,
        "rss_mb": round(rss_before / 1e6, 2),
        "rss_after_gc_mb": round(rss_after / 1e6, 2),
        "heap_cur_mb": round(cur / 1e6, 2),   # live Python allocations tracemalloc sees
        "heap_peak_mb": round(peak / 1e6, 2),
        "threads": threading.active_count(),
        "gc_objects": len(gc.get_objects()),
        "gc_collected": collected,
    }


def make_pool(uri: str) -> ConnectionPool:
    # one shared pool; autocommit + prepare_threshold=0 are the documented
    # settings for PostgresSaver / poolers (avoids prepared-statement growth).
    return ConnectionPool(
        conninfo=uri,
        max_size=5,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
        open=True,
    )


def run_shared_pool(uri: str, requests: int, every: int) -> list[dict]:
    """Recommended pattern: one pool, one compiled graph, reused for every request."""
    proc = psutil.Process()
    series: list[dict] = []
    pool = make_pool(uri)
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()
    graph = build_graph(checkpointer)
    for i in range(1, requests + 1):
        # fresh thread_id per request == a new short conversation, like a busy API
        cfg = {"configurable": {"thread_id": f"req-{i}"}}
        graph.invoke({"messages": [], "n": i}, config=cfg)
        if i % every == 0 or i == 1:
            series.append(snapshot(proc, i, do_gc=True))
    pool.close()
    return series


def run_per_request(uri: str, requests: int, every: int) -> list[dict]:
    """Anti-pattern: build a new graph + new connection on every request."""
    proc = psutil.Process()
    series: list[dict] = []
    for i in range(1, requests + 1):
        # NEW connection + NEW compiled graph each time (what NOT to do)
        with PostgresSaver.from_conn_string(uri) as checkpointer:
            if i == 1:
                checkpointer.setup()
            graph = build_graph(checkpointer)
            cfg = {"configurable": {"thread_id": f"req-{i}"}}
            graph.invoke({"messages": [], "n": i}, config=cfg)
        if i % every == 0 or i == 1:
            series.append(snapshot(proc, i, do_gc=True))
    return series


def verdict(series: list[dict]) -> dict:
    first, last = series[0], series[-1]
    rss_growth = last["rss_mb"] - first["rss_mb"]
    rss_growth_after_gc = last["rss_after_gc_mb"] - first["rss_after_gc_mb"]
    heap_growth = last["heap_cur_mb"] - first["heap_cur_mb"]
    n = last["request"] - first["request"]
    per_req_kb = (rss_growth * 1000 / n) if n else 0.0
    # crude classification
    if heap_growth > 2 and rss_growth > 5:
        kind = "LIKELY TRUE LEAK (live heap keeps growing)"
    elif rss_growth > 5 and heap_growth <= 2:
        kind = "FRAGMENTATION / RSS DRIFT (heap flat, RSS up — recycling helps, GC won't)"
    elif rss_growth <= 5:
        kind = "STABLE (no material growth over this run)"
    else:
        kind = "INCONCLUSIVE"
    return {
        "requests": n,
        "rss_growth_mb": round(rss_growth, 2),
        "rss_growth_after_gc_mb": round(rss_growth_after_gc, 2),
        "heap_growth_mb": round(heap_growth, 2),
        "thread_delta": last["threads"] - first["threads"],
        "gc_object_delta": last["gc_objects"] - first["gc_objects"],
        "approx_kb_per_request": round(per_req_kb, 2),
        "classification": kind,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", choices=["shared-pool", "per-request"], default="shared-pool")
    ap.add_argument("--requests", type=int, default=3000)
    ap.add_argument("--every", type=int, default=100, help="snapshot interval")
    ap.add_argument("--uri", default=os.environ.get("LG_PG_URI", DEFAULT_URI))
    args = ap.parse_args()

    tracemalloc.start()
    gc.enable()
    t0 = time.perf_counter()
    print(f"[{args.config}] {args.requests} requests against {args.uri.split('@')[-1]}")
    runner = run_shared_pool if args.config == "shared-pool" else run_per_request
    series = runner(args.uri, args.requests, args.every)
    elapsed = time.perf_counter() - t0

    v = verdict(series)
    out = {
        "config": args.config,
        "uri": args.uri.split("@")[-1],
        "requests": args.requests,
        "elapsed_s": round(elapsed, 1),
        "rps": round(args.requests / elapsed, 1),
        "verdict": v,
        "series": series,
        "langgraph": _versions(),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"langgraph_mem__{args.config}.json"
    out_path.write_text(json.dumps(out, indent=2))

    print(f"  elapsed {elapsed:.1f}s  ({out['rps']} req/s)")
    print(f"  RSS {series[0]['rss_mb']:.1f}MB -> {series[-1]['rss_mb']:.1f}MB "
          f"(+{v['rss_growth_mb']}MB, ~{v['approx_kb_per_request']}KB/req)")
    print(f"  heap +{v['heap_growth_mb']}MB | threads {series[0]['threads']}->{series[-1]['threads']} | "
          f"objects delta {v['gc_object_delta']:+}")
    print(f"  VERDICT: {v['classification']}")
    print(f"  wrote {out_path}")


def _versions() -> dict:
    import importlib.metadata as m
    out = {}
    for p in ("langgraph", "langgraph-checkpoint", "langgraph-checkpoint-postgres", "psycopg"):
        try:
            out[p] = m.version(p)
        except Exception:
            out[p] = "?"
    return out


if __name__ == "__main__":
    main()
