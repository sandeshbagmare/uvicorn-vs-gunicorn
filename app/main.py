"""
Demo ASGI app used to benchmark Uvicorn vs Gunicorn+UvicornWorker.

It deliberately exposes four *different shapes of work* so the benchmarks can
show where the server model actually matters:

    /            -> trivial JSON          (pure framework/server overhead baseline)
    /async-io    -> async I/O bound       (await asyncio.sleep -> models a well-behaved DB/HTTP call)
    /sync-io     -> BLOCKING I/O bound     (time.sleep inside async -> models a *mistake*: blocks the event loop)
    /cpu         -> CPU bound              (busy compute -> models the GIL / why you need multiple processes)

Every response includes the OS process id (`pid`) and the worker boot id. That
is the whole trick for *proving* multi-worker behaviour: fire 1000 requests and
count how many distinct pids answered. One worker -> one pid. Four workers ->
up to four pids, and you can see the load spread across them.

Run it with any of:
    uvicorn app.main:app --workers 1
    uvicorn app.main:app --workers 4
    gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4   # Linux/WSL/Docker only
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="Uvicorn vs Gunicorn benchmark app",
    description="Endpoints that exercise baseline / async-IO / blocking-IO / CPU work.",
    version="1.0.0",
)

# A per-process identity. `BOOT_ID` is generated once when the worker imports
# this module, so every worker process gets a unique value. Combined with the
# pid it lets the load tester attribute each response to a specific worker.
PID = os.getpid()
BOOT_ID = uuid.uuid4().hex[:8]


def _identity() -> dict:
    """Identity block attached to every response so we can see which worker served it."""
    return {"pid": PID, "boot_id": BOOT_ID}


@app.get("/")
async def root():
    """Baseline: as little work as possible. Measures raw server+framework overhead."""
    return {"message": "ok", **_identity()}


@app.get("/health")
async def health():
    """Liveness probe. Never blocks; used by orchestrators / the warm-up step."""
    return {"status": "healthy", **_identity()}


@app.get("/async-io")
async def async_io(delay: float = 0.05):
    """
    Correct async I/O: `await asyncio.sleep(delay)` yields control back to the
    event loop, so a SINGLE worker can keep thousands of these in flight at once.
    This is the case Uvicorn/ASGI is built for. `delay` defaults to 50 ms to
    imitate a fast downstream dependency (cache/DB/microservice).
    """
    start = time.perf_counter()
    await asyncio.sleep(delay)
    return {
        "kind": "async-io",
        "delay_s": delay,
        "served_in_ms": round((time.perf_counter() - start) * 1000, 2),
        **_identity(),
    }


@app.get("/sync-io")
async def sync_io(delay: float = 0.05):
    """
    The classic anti-pattern: a BLOCKING call (`time.sleep`) inside an async
    endpoint. It does NOT yield to the event loop, so while this request sleeps,
    the worker's event loop is frozen and cannot serve anyone else. Throughput
    here collapses to (workers / delay) req/s no matter how much concurrency you
    throw at it -- a great demonstration of why blocking work needs either a
    threadpool (def endpoint) or more processes.
    """
    start = time.perf_counter()
    time.sleep(delay)  # noqa: ASYNC101 -- intentional, this is the demo
    return {
        "kind": "sync-io-blocking",
        "delay_s": delay,
        "served_in_ms": round((time.perf_counter() - start) * 1000, 2),
        **_identity(),
    }


@app.get("/cpu")
async def cpu(iterations: int = 50_000):
    """
    CPU-bound work. Python's GIL means a single process executes Python bytecode
    on one core at a time, so this saturates one core and blocks the event loop
    for its duration. The ONLY way to scale this is more processes (workers),
    which is the headline reason multi-process serving exists. `iterations`
    controls how heavy each request is.
    """
    start = time.perf_counter()
    total = 0
    for i in range(iterations):
        total += i * i % 7
    return {
        "kind": "cpu",
        "iterations": iterations,
        "result": total,
        "served_in_ms": round((time.perf_counter() - start) * 1000, 2),
        **_identity(),
    }


@app.exception_handler(Exception)
async def unhandled(_request, exc):  # pragma: no cover - safety net for load tests
    return JSONResponse(status_code=500, content={"error": str(exc), **_identity()})
