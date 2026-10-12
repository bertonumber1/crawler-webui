"""Talk to JDownloader properly, instead of reading its save files.

jdstate.py reads JD's linkcollector/downloadList archives off disk. That works
and needs no credentials, but it can only look, and only at whatever JD last
chose to write: additions do not appear until JD's next save, and there is no
way to act on what is found.

MyJDownloader's API answers both. It is already enabled on this install and
auto-connecting, so nothing about JD has to change. The connection is made
directly to the device on the LAN rather than round-tripping through
api.jdownloader.org -- both containers share a network namespace, so the
device port is simply local.

What it gives that reading files cannot:

  add          returns a job id the moment the links are accepted, so a
               handoff is confirmed rather than assumed.
  availability JD checks each link itself and reports ONLINE or OFFLINE with
               an exact byte size. A dead link can be seen before it is ever
               recorded as sent.
  remove       dead packages can be cleared out instead of accumulating.

Credentials come from the environment and belong in .env, which is ignored by
git. With none set this module reports itself unavailable and callers fall
back to jdstate.py, so the crawler still works without an account.
"""
from __future__ import annotations

import os
import threading
import time

from . import trace

# Read at call time, not import time. .env is loaded by fetch.py when it is
# imported, and binding these at module level would capture whatever happened
# to be set first depending on import order.
def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)
# A session can be dropped by the far end; re-connect rather than fail.
RETRY_ON_ERROR = True

_lock = threading.Lock()
_jd = None
_device = None
_last_error = ""


def configured() -> bool:
    return bool(_env("CW_MYJD_EMAIL") and _env("CW_MYJD_PASSWORD"))


def _connect(force: bool = False):
    """Return a device handle, connecting or reconnecting as needed."""
    global _jd, _device, _last_error
    if not configured():
        raise RuntimeError("MyJDownloader credentials are not set")
    if _device is not None and not force:
        return _device
    import myjdapi

    jd = myjdapi.Myjdapi()
    jd.set_app_key(_env("CW_MYJD_APP_KEY", "crawler-webui"))
    jd.connect(_env("CW_MYJD_EMAIL"), _env("CW_MYJD_PASSWORD"))
    jd.update_devices()
    devices = jd.list_devices()
    if not devices:
        raise RuntimeError("no JDownloader devices on this account")
    name = _env("CW_MYJD_DEVICE") or devices[0]["name"]
    _jd, _device = jd, jd.get_device(name)
    _last_error = ""
    trace.event("jdapi", "connected", device=name, devices=len(devices))
    return _device


def _call(fn, *a, **kw):
    """Run an API call, reconnecting once if the session has gone stale."""
    global _last_error, _device
    with _lock:
        try:
            return fn(_connect(), *a, **kw)
        except Exception as e:
            if not RETRY_ON_ERROR:
                _last_error = f"{type(e).__name__}: {e}"
                raise
            _device = None
            try:
                return fn(_connect(force=True), *a, **kw)
            except Exception as e2:
                _last_error = f"{type(e2).__name__}: {e2}"
                trace.event("jdapi", "call failed", error=_last_error[:160])
                raise


def status() -> dict:
    if not configured():
        return {"ok": False, "configured": False,
                "detail": "no CW_MYJD_EMAIL / CW_MYJD_PASSWORD set"}
    try:
        dev = _call(lambda d: d)
        return {"ok": True, "configured": True, "device": dev.name,
                "detail": "connected"}
    except Exception as e:
        return {"ok": False, "configured": True,
                "detail": f"{type(e).__name__}: {e}"[:200]}


# ------------------------------------------------------------------ reading

# packageUUIDs is a FILTER and must be a list; passing it as a boolean makes
# the device reject the whole query with BAD_PARAMETERS. An empty list means
# "no filter". The package a link belongs to comes back as packageUUID.
LINK_FIELDS = [{"availability": True, "bytesTotal": True, "name": True,
                "url": True, "status": True, "enabled": True,
                "host": True, "packageUUIDs": []}]
PACKAGE_FIELDS = [{"name": True, "saveTo": True, "bytesTotal": True,
                   "childCount": True, "enabled": True}]


def linkgrabber() -> dict:
    links = _call(lambda d: d.linkgrabber.query_links(LINK_FIELDS)) or []
    packages = _call(lambda d: d.linkgrabber.query_packages(PACKAGE_FIELDS)) or []
    return {"links": links, "packages": packages}


def downloads() -> dict:
    links = _call(lambda d: d.downloads.query_links(LINK_FIELDS)) or []
    packages = _call(lambda d: d.downloads.query_packages(PACKAGE_FIELDS)) or []
    return {"links": links, "packages": packages}


def snapshot() -> dict:
    """Everything JD holds, as file identities plus per-link availability."""
    from . import links as linkmod

    lg, dl = linkgrabber(), downloads()

    def keys(rows):
        out = set()
        for r in rows:
            u = r.get("url") or ""
            if linkmod.is_known_file_host(u):
                out.add(linkmod.canonical_download_key(u))
        return out

    offline = []
    for r in lg["links"] + dl["links"]:
        if str(r.get("availability", "")).upper() == "OFFLINE":
            u = r.get("url") or ""
            if linkmod.is_known_file_host(u):
                offline.append(linkmod.canonical_download_key(u))

    lg_keys, dl_keys = keys(lg["links"]), keys(dl["links"])
    return {
        "ok": True,
        "source": "myjdownloader",
        "detail": "live API",
        "linkgrabber": sorted(lg_keys),
        "downloads": sorted(dl_keys),
        "keys": lg_keys | dl_keys,
        "offline": sorted(set(offline)),
        "packages": [{"name": p.get("name", ""), "folder": p.get("saveTo", ""),
                      "bytes": p.get("bytesTotal", 0),
                      "children": p.get("childCount", 0)}
                     for p in lg["packages"] + dl["packages"]],
    }


# ------------------------------------------------------------------ writing

def add(urls: list[str], package: str, folder: str = "",
        auto_start: bool = False) -> dict:
    """Hand links straight to JD and get an answer immediately.

    The folderwatch file is fire-and-forget: JD consumes it and reports
    nothing. This returns the collector job id, so a handoff that did not
    happen is visible at the time rather than by its absence later.
    """
    urls = [u for u in urls if u]
    if not urls:
        raise ValueError("no links given")
    job = {
        "autostart": bool(auto_start),
        "links": "\n".join(urls),
        "packageName": package or "crawler-webui",
        "overwritePackagizerRules": False,
    }
    if folder:
        job["destinationFolder"] = folder
    res = _call(lambda d: d.linkgrabber.add_links([job]))
    trace.event("jdapi", "links added", package=package, count=len(urls),
                job=(res or {}).get("id"))
    return {"ok": True, "job": (res or {}).get("id"), "count": len(urls)}


def _uuids_for(rows, names: set[str]):
    packages = [p for p in rows["packages"] if p.get("name") in names]
    puuids = {p["uuid"] for p in packages}
    luuids = [l["uuid"] for l in rows["links"]
              if l.get("packageUUID") in puuids]
    return luuids, sorted(puuids)


def remove_packages(names) -> dict:
    """Remove named packages from both lists."""
    names = {n for n in names if n}
    if not names:
        return {"ok": True, "removed": 0}
    removed = 0
    for where, rows in (("linkgrabber", linkgrabber()), ("downloads", downloads())):
        luuids, puuids = _uuids_for(rows, names)
        if not puuids:
            continue
        _call(lambda d, w=where, l=luuids, p=puuids:
              getattr(d, w).remove_links(l, p))
        removed += len(puuids)
    trace.event("jdapi", "packages removed", count=removed)
    return {"ok": True, "removed": removed}


def remove_offline() -> dict:
    """Clear out every link JD has determined is dead.

    Left alone these accumulate, and each one is a release that looks handled
    and is not. Removing them is only half the job -- the caller also drops
    them from the sent-history, so the release can be found again.
    """
    from . import links as linkmod

    dropped, keys = 0, []
    for where in ("linkgrabber", "downloads"):
        rows = linkgrabber() if where == "linkgrabber" else downloads()
        dead = [l for l in rows["links"]
                if str(l.get("availability", "")).upper() == "OFFLINE"]
        if not dead:
            continue
        luuids = [l["uuid"] for l in dead]
        _call(lambda d, w=where, l=luuids: getattr(d, w).remove_links(l, []))
        dropped += len(luuids)
        for l in dead:
            u = l.get("url") or ""
            if linkmod.is_known_file_host(u):
                keys.append(linkmod.canonical_download_key(u))
    trace.event("jdapi", "offline links removed", count=dropped)
    return {"ok": True, "removed": dropped, "keys": sorted(set(keys))}


# ------------------------------------------------------------------ named files
# MyJDownloader's addLinks takes a package name and a folder but no per-link
# file name, and a site's upload names are often poor (Tape3SwanEandVibes.mp3).
# So a named release is added PAUSED, each link is renamed once JD has
# collected it, and only then is it moved to the download list. A worker does
# the renaming so a crawl never waits on JD's collector. Pending work is kept
# in a file beside the database: a restart resumes it rather than leaving a
# package of upload names.

import json as _json
from urllib.parse import unquote as _unquote

RENAME_TIMEOUT = 15 * 60
_pending_lock = threading.Lock()
_worker = None


def _pending_path() -> str:
    from . import db
    return os.path.join(db.DATA_DIR, "jd_pending_names.json")


def _load_pending() -> list[dict]:
    try:
        with open(_pending_path(), encoding="utf-8") as f:
            rows = _json.load(f)
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def _save_pending(rows: list[dict]) -> None:
    path = _pending_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(rows, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def url_key(url: str) -> str:
    """Compare URLs the way JD may echo them back: scheme, www, case and
    percent-encoding are not differences."""
    u = _unquote(_unquote(url or "")).strip().lower()
    u = u.split("://", 1)[-1].removeprefix("www.")
    return u.rstrip("/")


def add_named(files: list[tuple[str, str]], package: str, folder: str = "",
              auto_start: bool = False, source_url: str = "") -> dict:
    """Add [(url, file name)] as one package, paused; the worker renames each
    link to its file name and then starts it if auto_start."""
    files = [(u, n) for u, n in files if u]
    if not files:
        raise ValueError("no links given")
    job = {
        "autostart": False,
        "links": "\n".join(u for u, _ in files),
        "packageName": package or "crawler-webui",
        "overwritePackagizerRules": False,
    }
    if folder:
        job["destinationFolder"] = folder
    if source_url:
        # Sent as the referrer of every link: some sites guard their files
        # with a Referer check.
        job["sourceUrl"] = source_url
    res = _call(lambda d: d.linkgrabber.add_links([job]))
    with _pending_lock:
        rows = _load_pending()
        rows.append({"package": package, "start": bool(auto_start),
                     "names": {url_key(u): n for u, n in files},
                     "since": time.time()})
        _save_pending(rows)
    trace.event("jdapi", "named links added", package=package, count=len(files),
                job=(res or {}).get("id"), autostart=bool(auto_start))
    _ensure_worker()
    return {"ok": True, "job": (res or {}).get("id"), "count": len(files)}


def rename_pass() -> dict:
    """One pass over pending packages: rename what JD has collected, start
    what is complete. Returns counts; never raises."""
    with _pending_lock:
        rows = _load_pending()
    if not rows:
        return {"pending": 0}
    try:
        lg = linkgrabber()
    except Exception as e:
        return {"pending": len(rows), "error": f"{type(e).__name__}: {e}"}
    by_key = {}
    for l in lg["links"]:
        by_key.setdefault(url_key(l.get("url") or ""), []).append(l)
    renamed = started = 0
    keep = []
    for row in rows:
        names = row["names"]
        found = {k: by_key.get(k, []) for k in names}
        for k, links in found.items():
            for l in links:
                if l.get("name") != names[k]:
                    try:
                        _call(lambda d, i=l["uuid"], n=names[k]:
                              d.linkgrabber.rename_link(i, n))
                        renamed += 1
                    except Exception as e:
                        trace.event("jdapi", "rename failed", name=names[k][:80],
                                    error=f"{type(e).__name__}: {e}"[:160])
        complete = all(found.values())
        expired = time.time() - row.get("since", 0) > RENAME_TIMEOUT
        if complete or expired:
            if expired and not complete:
                missing = [names[k] for k, v in found.items() if not v]
                trace.event("jdapi", "rename gave up waiting for links",
                            package=row["package"], missing=len(missing),
                            first=missing[0][:80])
            if row.get("start"):
                ids = [l["uuid"] for v in found.values() for l in v]
                if ids:
                    try:
                        _call(lambda d, i=ids: d.linkgrabber.move_to_downloadlist(i, []))
                        started += 1
                    except Exception as e:
                        trace.event("jdapi", "start failed", package=row["package"],
                                    error=f"{type(e).__name__}: {e}"[:160])
                        keep.append(row)
                        continue
            trace.event("jdapi", "package named", package=row["package"],
                        files=sum(1 for v in found.values() if v),
                        started=bool(row.get("start")))
        else:
            keep.append(row)
    with _pending_lock:
        # Rows added while this pass ran are kept too.
        latest = _load_pending()
        done = [r for r in rows if r not in keep]
        _save_pending([r for r in latest if r not in done])
    return {"pending": len(keep), "renamed": renamed, "started": started}


def _worker_loop():
    global _worker
    idle = 0
    while idle < 60:                  # ~5 minutes with nothing pending
        try:
            res = rename_pass()
        except Exception as e:        # never let the worker die silently
            trace.event("jdapi", "rename pass failed", error=f"{type(e).__name__}: {e}")
            res = {"pending": 1}
        idle = idle + 1 if not res.get("pending") else 0
        time.sleep(5)
    with _pending_lock:
        _worker = None


def _ensure_worker():
    global _worker
    with _pending_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_worker_loop, name="jd-renamer", daemon=True)
        _worker.start()


def pending_names() -> dict:
    rows = _load_pending()
    return {"packages": len(rows), "files": sum(len(r["names"]) for r in rows),
            "worker": bool(_worker is not None and _worker.is_alive())}
