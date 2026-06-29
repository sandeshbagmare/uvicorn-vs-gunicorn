"""
Worker-recycling demo app — the Windows-friendly stand-in for Gunicorn
`--max-requests`, driven through Uvicorn's identical `--limit-max-requests`.

Gunicorn does not run on Windows (needs the Unix-only `fcntl`), but Uvicorn's
`--limit-max-requests` / `--limit-max-requests-jitter` are the exact same idea:
recycle a worker after N requests so leaked/fragmented memory is returned to the
OS when the process dies. Run this under `uvicorn --workers 2` and each worker
is a faithful local analog of "one worker per pod" — two single-process workers
behind one supervisor, exactly the shape that produces synchronized restarts.

Each worker process exposes its identity so the load driver can SEE recycles:
  - pid + boot_id : change when a worker is (re)spawned  -> a recycle happened
  - req_count     : per-worker counter, resets to 1 after a recycle (the sawtooth)
  - rss_mb        : this worker's resident memory right now

Endpoints:
  GET /invoke  -> runs one LangGraph+PostgresSaver step (if RECYCLE_USE_PG=1),
                  optionally leaks RECYCLE_LEAK_KB to make the sawtooth visible,
                  returns the identity block.
  GET /mem     -> identity block only (no work, no leak)
  GET /health  -> liveness

Env knobs:
  RECYCLE_LEAK_KB     synthetic, CLEARLY-LABELLED leak per /invoke (default 40).
                      This exists ONLY to make the recycle MECHANISM visible in a
                      short run; it is NOT a claim about LangGraph's real memory.
                      The real LangGraph+PostgresSaver growth question is measured
                      honestly by langgraph_memory_probe.py instead.
  RECYCLE_BOOT_DELAY_S  sleep during import to model a HEAVY app's slow boot
                      (imports + model load + checkpointer.setup). This is what
                      makes a synchronized restart turn into a real outage window.
  RECYCLE_USE_PG      "1" (default) to exercise the real graph+PostgresSaver.
  LG_PG_URI           Postgres URI for the checkpointer.

Run:
  uvicorn research.scripts.recycle_app:app --workers 2 --limit-max-requests 500 \
      --limit-max-requests-jitter 250 --port 8010
"""

from __future__ import annotations

import operator
import os
import random
import threading
import time
import uuid
from typing import Annotated, TypedDict

import psutil
from fastapi import FastAPI

# Model a heavy app's slow startup (the reason a recycle becomes a visible gap).
_BOOT_DELAY = float(os.environ.get("RECYCLE_BOOT_DELAY_S", "0"))
if _BOOT_DELAY:
    time.sleep(_BOOT_DELAY)

PID = os.getpid()
BOOT_ID = uuid.uuid4().hex[:8]
REQ_COUNT = 0
_LEAK: list[bytes] = []  # module-level => dies with the process => recycling frees it
_LEAK_KB = int(os.environ.get("RECYCLE_LEAK_KB", "40"))
_PROC = psutil.Process()

# App-level recycling. Uvicorn's --limit-max-requests has NO jitter flag (that is
# Gunicorn-only: --max-requests-jitter), so to demonstrate the jitter FIX we let
# each worker pick its own randomized recycle target at boot and self-exit once it
# is reached (the supervisor respawns it). RECYCLE_SELF_LIMIT=0 disables this and
# lets uvicorn's own --limit-max-requests drive the recycle instead.
_SELF_BASE = int(os.environ.get("RECYCLE_SELF_LIMIT", "0"))
_SELF_JITTER = int(os.environ.get("RECYCLE_SELF_JITTER", "0"))
_SELF_TARGET = (_SELF_BASE + random.randrange(_SELF_JITTER + 1)) if _SELF_BASE else 0


def _schedule_exit() -> None:
    # exit a hair after the current response flushes, so this request still succeeds
    def _bye():
        time.sleep(0.05)
        os._exit(0)
    threading.Thread(target=_bye, daemon=True).start()

app = FastAPI(title="worker-recycling demo")

# --- optional real LangGraph + PostgresSaver work -------------------------- #
_GRAPH = None
if os.environ.get("RECYCLE_USE_PG", "1") == "1":
    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.graph import END, START, StateGraph

        class _State(TypedDict):
            messages: Annotated[list, operator.add]
            n: int

        def _u(s: _State) -> dict:
            return {"messages": [f"user {s['n']}"], "n": s["n"]}

        def _a(s: _State) -> dict:
            return {"messages": [f"assistant {s['n']}"]}

        _uri = os.environ.get("LG_PG_URI", "postgresql://postgres:password@127.0.0.1:5432/lg_memtest")
        _pool = ConnectionPool(conninfo=_uri, max_size=3,
                               kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
                               open=True)
        _cp = PostgresSaver(_pool)
        _cp.setup()
        _b = StateGraph(_State)
        _b.add_node("user", _u)
        _b.add_node("assistant", _a)
        _b.add_edge(START, "user")
        _b.add_edge("user", "assistant")
        _b.add_edge("assistant", END)
        _GRAPH = _b.compile(checkpointer=_cp)
    except Exception as exc:  # DB down / package missing -> mechanism demo still runs
        print(f"[recycle_app] PG graph disabled: {exc}")
        _GRAPH = None


def _identity() -> dict:
    return {
        "pid": PID,
        "boot_id": BOOT_ID,
        "req_count": REQ_COUNT,
        "rss_mb": round(_PROC.memory_info().rss / 1e6, 2),
    }


@app.get("/health")
async def health():
    return {"status": "ok", **_identity()}


@app.get("/mem")
async def mem():
    return _identity()


@app.get("/invoke")
async def invoke():
    global REQ_COUNT
    REQ_COUNT += 1
    if _GRAPH is not None:
        _GRAPH.invoke({"messages": [], "n": REQ_COUNT},
                      config={"configurable": {"thread_id": f"{BOOT_ID}-{REQ_COUNT}"}})
    if _LEAK_KB:
        _LEAK.append(b"x" * (_LEAK_KB * 1024))  # synthetic, labelled (see module docstring)
    body = {"kind": "invoke", "leaked_kb_total": len(_LEAK) * _LEAK_KB,
            "self_target": _SELF_TARGET, **_identity()}
    if _SELF_TARGET and REQ_COUNT >= _SELF_TARGET:
        _schedule_exit()
    return body
