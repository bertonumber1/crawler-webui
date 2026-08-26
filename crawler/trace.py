"""Temporary diagnostic tracing, safe to leave running.

Debugging this crawler means answering "where did that link go?" across four
stages -- fetch, parse, resolve, handoff -- and until now the only record was
whatever the terminal happened to catch. This keeps a bounded in-memory ring
for the UI plus a rotating file for after the fact.

Two limits are deliberate, both learned the hard way on this box: the ring is
capped and drops oldest rather than growing, and the file rotates rather than
appending forever. A log that fills the disk takes the machine with it.

Off by default in the sense that it costs nothing to leave on:

    CRAWLER_TRACE=0        disable entirely
    CRAWLER_TRACE_FILE     path (default: trace.log beside the database)
    CRAWLER_TRACE_KEEP     ring size (default 2000 events)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler

from . import db

ENABLED = (os.getenv("CRAWLER_TRACE", "1").lower() not in ("0", "false", "no"))
KEEP = max(100, int(os.getenv("CRAWLER_TRACE_KEEP", "2000")))
PATH = os.getenv("CRAWLER_TRACE_FILE") or os.path.join(db.DATA_DIR, "trace.log")

_ring: deque = deque(maxlen=KEEP)
_lock = threading.Lock()
_seq = 0

_log = logging.getLogger("crawler.trace")
if ENABLED and not _log.handlers:
    _log.setLevel(logging.INFO)
    _log.propagate = False
    try:
        h = RotatingFileHandler(PATH, maxBytes=5_000_000, backupCount=3)
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _log.addHandler(h)
    except Exception:
        # A trace that cannot open its file must not stop the crawler.
        ENABLED = False


def event(stage: str, msg: str, **fields) -> None:
    """Record one step. Never raises -- tracing must not break the caller."""
    if not ENABLED:
        return
    global _seq
    try:
        with _lock:
            _seq += 1
            row = {"n": _seq, "ts": time.time(), "stage": stage, "msg": msg}
            row.update({k: v for k, v in fields.items() if v is not None})
            _ring.append(row)
        extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        _log.info("[%s] %s%s", stage, msg, (" " + extra) if extra else "")
    except Exception:
        pass


def tail(limit: int = 200, stage: str = "", since: int = 0) -> list[dict]:
    with _lock:
        rows = list(_ring)
    if stage:
        rows = [r for r in rows if r["stage"] == stage]
    if since:
        rows = [r for r in rows if r["n"] > since]
    return rows[-limit:]


def stages() -> dict:
    with _lock:
        rows = list(_ring)
    out: dict[str, int] = {}
    for r in rows:
        out[r["stage"]] = out.get(r["stage"], 0) + 1
    return out


def clear() -> int:
    with _lock:
        n = len(_ring)
        _ring.clear()
    return n


def status() -> dict:
    size = 0
    try:
        size = os.path.getsize(PATH)
    except OSError:
        pass
    with _lock:
        held = len(_ring)
    return {"enabled": ENABLED, "path": PATH, "bytes": size,
            "held": held, "capacity": KEEP, "stages": stages()}
