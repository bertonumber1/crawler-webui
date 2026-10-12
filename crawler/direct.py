"""Polite downloads from sites that serve their own files.

JDownloader opens up to 20 downloads with 3 connections each. Pointed at a
small self-hosted site (ravetapepacks.com) that is 60 connections to one
server, and the site's firewall dropped the VPN address within minutes. A
file host is built for that; a hobby site is not.

So a host marked `direct` is fetched here instead: ONE file at a time, ONE
connection, a pause between files, the post as Referer (the site's hotlink
guard wants one), straight into <root>/<folder>/<file name>. That also makes
the names exact -- no renaming inside JD afterwards.

The queue is a JSON file beside the database, so a restart carries on. A
partial file is kept as .part and resumed with a Range request. A file
already on disk at the expected size is never fetched again.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time

from . import trace

_lock = threading.Lock()
_worker: threading.Thread | None = None
_stop = threading.Event()
_status: dict = {"current": "", "bytes": 0, "total": 0, "done": 0, "failed": 0,
                 "last_error": "", "paused_until": 0}

CHUNK = 256 * 1024


def _env(name, default=""):
    return os.getenv(name, default)


def root() -> str:
    from . import crawljob
    return _env("CW_DIRECT_ROOT") or crawljob.download_root()


def _gap() -> tuple[float, float]:
    raw = _env("CW_DIRECT_GAP", "3-8")
    try:
        lo, hi = (float(x) for x in raw.split("-", 1))
        return max(0.0, lo), max(lo, hi)
    except ValueError:
        return 3.0, 8.0


def _queue_path() -> str:
    from . import db
    return os.path.join(db.DATA_DIR, "direct_queue.json")


def _load() -> list[dict]:
    try:
        with open(_queue_path(), encoding="utf-8") as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    p = _queue_path()
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def _owner():
    """Files belong to the desktop user, not root (the container's user)."""
    try:
        return int(_env("CW_OUTPUT_UID", "1000")), int(_env("CW_OUTPUT_GID", "1000"))
    except ValueError:
        return None


def _chown(path: str):
    own = _owner()
    if own and hasattr(os, "chown"):
        try:
            os.chown(path, *own)
        except OSError:
            pass


def _mkdirs(path: str):
    """Create the folder chain, each new level owned by the desktop user."""
    missing = []
    p = path
    while p and not os.path.isdir(p):
        missing.append(p)
        p = os.path.dirname(p)
    for d in reversed(missing):
        os.makedirs(d, exist_ok=True)
        _chown(d)


def enqueue(files: list[dict], package: str = "") -> int:
    """files: [{url, folder, filename, referer}]. Returns how many were new.
    A url already queued is not queued twice."""
    with _lock:
        rows = _load()
        have = {r["url"] for r in rows}
        new = [dict(f, package=package, state="queued", tries=0, error="")
               for f in files if f.get("url") and f["url"] not in have]
        rows.extend(new)
        _save(rows)
    trace.event("direct", "queued", package=package[:80], files=len(new))
    ensure_worker()
    return len(new)


def target(row: dict) -> str:
    from .naming import safe_component
    # A folder may nest ("Site/Event/Pack"): each level is made safe on its
    # own, so "/" can only ever separate levels, never escape the root.
    parts = [safe_component(x) for x in
             (row.get("folder") or row.get("package") or "untitled").split("/")
             if x.strip() and x.strip() not in (".", "..")]
    name = safe_component(row.get("filename") or row["url"].rsplit("/", 1)[-1])
    return os.path.join(root(), *(parts or ["untitled"]), name)


def _client():
    import httpx
    from . import fetch
    ua = fetch.UA
    return httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, connect=20.0),
                        headers={"User-Agent": ua, "Accept": "*/*"})


def fetch_one(row: dict, client=None) -> tuple[bool, str]:
    """Download one file. (ok, detail). Never raises."""
    path = target(row)
    part = path + ".part"
    try:
        _mkdirs(os.path.dirname(path))
    except OSError as e:
        return False, f"mkdir: {e}"
    own = client is None
    client = client or _client()
    try:
        headers = {}
        if row.get("referer"):
            headers["Referer"] = row["referer"]
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if os.path.exists(path):
            # Only a complete file is ever renamed off .part.
            row["size"] = os.path.getsize(path)
            return True, "already on disk"
        if have:
            headers["Range"] = f"bytes={have}-"
        with client.stream("GET", row["url"], headers=headers) as r:
            if r.status_code == 416 and have:
                os.replace(part, path)
                _chown(path)
                return True, "already complete"
            if r.status_code not in (200, 206):
                return False, f"HTTP {r.status_code}"
            ctype = r.headers.get("content-type", "")
            if "text/html" in ctype:
                return False, "got an HTML page, not the file"
            if r.status_code == 200:
                have = 0                       # server ignored Range: start over
            total = int(r.headers.get("content-length", 0) or 0) + have
            _status.update(current=os.path.basename(path), bytes=have, total=total)
            with open(part, "ab" if have else "wb") as f:
                for chunk in r.iter_bytes(CHUNK):
                    if _stop.is_set():
                        return False, "stopped"
                    f.write(chunk)
                    _status["bytes"] += len(chunk)
        size = os.path.getsize(part)
        if total and size != total:
            return False, f"short read {size}/{total}"
        os.replace(part, path)
        _chown(path)
        row["size"] = size
        return True, f"{size} bytes"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:200]
    finally:
        if own:
            client.close()


def _workers() -> int:
    try:
        return max(1, min(8, int(_env("CW_DIRECT_WORKERS", "1"))))
    except ValueError:
        return 1


def _claim():
    """Take the next queued file and mark it active, so no other worker
    takes it. Called under _lock."""
    rows = _load()
    for r in rows:
        if r.get("state") == "queued":
            r["state"] = "active"
            _save(rows)
            return dict(r)
    return None


def _loop():
    global _worker
    backoff = 60
    client = _client()
    try:
        while not _stop.is_set():
            with _lock:
                row = _claim()
            if row is None:
                break
            ok, detail = fetch_one(row, client)
            with _lock:
                rows = _load()
                for r in rows:
                    if r["url"] == row["url"]:
                        r["tries"] = r.get("tries", 0) + 1
                        if ok:
                            r.update(state="done", error="", size=row.get("size"))
                        elif detail == "stopped":
                            r["state"] = "queued"
                        elif r["tries"] >= 6 or detail.startswith(("HTTP 404", "HTTP 410")):
                            r.update(state="failed", error=detail)
                        else:
                            r.update(state="queued", error=detail)
                _save(rows)
            if ok:
                _status["done"] += 1
                backoff = 60
                trace.event("direct", "saved", file=target(row)[-120:], detail=detail)
                time.sleep(random.uniform(*_gap()))
            elif detail != "stopped":
                _status["failed"] += 1
                _status["last_error"] = f"{row.get('filename', '')}: {detail}"
                trace.event("direct", "failed", url=row["url"][-100:], detail=detail,
                            wait=backoff)
                # Timeouts and refusals are how a site says "too much":
                # back off, doubling, up to half an hour.
                _status["paused_until"] = time.time() + backoff
                _stop.wait(backoff)
                backoff = min(backoff * 2, 1800)
    finally:
        client.close()


_pool: list[threading.Thread] = []


def _alive() -> bool:
    return any(t.is_alive() for t in _pool)


def ensure_worker():
    """Start CW_DIRECT_WORKERS fetchers (default 1). Files left 'active' by
    a restart go back in the queue first."""
    global _worker
    with _lock:
        if _alive():
            return
        rows = _load()
        if any(r.get("state") == "active" for r in rows):
            for r in rows:
                if r.get("state") == "active":
                    r["state"] = "queued"
            _save(rows)
        _stop.clear()
        _pool[:] = [threading.Thread(target=_loop, name=f"direct-fetch-{i}", daemon=True)
                    for i in range(_workers())]
        for t in _pool:
            t.start()
        _worker = _pool[0]


def stop():
    _stop.set()


def status() -> dict:
    rows = _load()
    counts = {}
    for r in rows:
        counts[r.get("state", "?")] = counts.get(r.get("state", "?"), 0) + 1
    return {"counts": counts, "running": _alive(), "workers": sum(t.is_alive() for t in _pool),
            "root": root(), **_status,
            "failed_files": [{"file": r.get("filename"), "folder": r.get("folder"),
                              "error": r.get("error")} for r in rows if r.get("state") == "failed"][:50]}


def retry_failed() -> int:
    with _lock:
        rows = _load()
        n = 0
        for r in rows:
            if r.get("state") == "failed":
                r.update(state="queued", tries=0, error="")
                n += 1
        _save(rows)
    if n:
        ensure_worker()
    return n
