"""Read what JDownloader actually has, rather than trusting that we sent it.

Writing a .crawljob proves nothing. JD consumes the file, moves it to added/,
logs an accepted CrawlerJob, and can still end up with no package -- a dupe it
declines silently, a link its filters drop, a malformed field that fails
deserialisation. All of those look identical from the folderwatch side, and
every one of them has happened here.

JD persists its two lists as zip archives of JSON fragments:

    cfg/linkcollector<N>.zip   the LinkGrabber
    cfg/downloadList<N>.zip    the Downloads tab

Reading the newest of each is enough to answer the only question that matters:
is the file we queued actually in JD? Nothing is written back -- JD owns those
files and is running.
"""
from __future__ import annotations

import json
import os
import re
import zipfile

from . import links as linkmod

DEFAULT_CFG = "/home/media/docker/torrentvpn-app/jdownloader/config/cfg"

_LIST_RE = re.compile(r"^(linkcollector|downloadList)(\d+)\.zip$")


def cfg_dir() -> str:
    return os.environ.get("CW_JD_CFG", DEFAULT_CFG)


def available() -> tuple[bool, str]:
    d = cfg_dir()
    if not os.path.isdir(d):
        return False, f"JD cfg dir not found: {d}"
    if not os.access(d, os.R_OK):
        return False, f"JD cfg dir not readable: {d}"
    return True, d


def _newest(kind: str) -> str | None:
    """Newest list file of a kind. JD numbers them upwards, not by mtime."""
    d = cfg_dir()
    best, best_n = None, -1
    try:
        names = os.listdir(d)
    except OSError:
        return None
    for name in names:
        m = _LIST_RE.match(name)
        if not m or m.group(1) != kind:
            continue
        n = int(m.group(2))
        if n > best_n:
            best, best_n = os.path.join(d, name), n
    return best


def _urls_in(path: str) -> list[str]:
    """Every URL in a JD list archive.

    Entries are JSON fragments named 00, 00_00, 01 ... plus an extraInfo
    member. A truncated or half-written archive yields what it can rather than
    raising -- JD may be rewriting it as we read.
    """
    out = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name == "extraInfo":
                    continue
                try:
                    blob = json.loads(z.read(name).decode("utf-8", "replace"))
                except Exception:
                    continue
                out.extend(_walk_urls(blob))
    except Exception:
        return out
    return out


def _walk_urls(node) -> list[str]:
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "url" and isinstance(v, str) and v.startswith("http"):
                found.append(v)
            else:
                found.extend(_walk_urls(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(_walk_urls(v))
    return found


def _packages_in(path: str) -> list[dict]:
    """Package rows (name + folder) from a list archive."""
    out = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in sorted(z.namelist()):
                # Package fragments are the un-suffixed members: 00, 01, ...
                if name == "extraInfo" or "_" in name:
                    continue
                try:
                    blob = json.loads(z.read(name).decode("utf-8", "replace"))
                except Exception:
                    continue
                if isinstance(blob, dict) and blob.get("name"):
                    out.append({"name": blob.get("name", ""),
                                "folder": blob.get("downloadFolder", "")})
    except Exception:
        return out
    return out


def snapshot() -> dict:
    """What JD holds right now, as file identities we can compare against."""
    ok, detail = available()
    if not ok:
        return {"ok": False, "detail": detail, "linkgrabber": [], "downloads": [],
                "keys": set(), "packages": []}

    lg, dl = _newest("linkcollector"), _newest("downloadList")
    lg_urls = _urls_in(lg) if lg else []
    dl_urls = _urls_in(dl) if dl else []

    def keys(urls):
        return {linkmod.canonical_download_key(u) for u in urls
                if linkmod.is_known_file_host(u)}

    lg_keys, dl_keys = keys(lg_urls), keys(dl_urls)
    return {
        "ok": True,
        "detail": detail,
        "linkgrabber_file": os.path.basename(lg) if lg else "",
        "downloads_file": os.path.basename(dl) if dl else "",
        "linkgrabber": sorted(lg_keys),
        "downloads": sorted(dl_keys),
        "keys": lg_keys | dl_keys,
        "packages": (_packages_in(lg) if lg else []) + (_packages_in(dl) if dl else []),
    }


def reconcile() -> dict:
    """Update our record of what is in JD from JD's own lists.

    Anything we marked sent and can now see in JD becomes confirmed. Anything
    we marked confirmed that JD no longer holds has left -- finished and
    cleared, or deleted by hand -- and becomes gone. It stays in the table so
    it is still recognised as already-had on a future crawl.
    """
    from . import history

    snap = snapshot()
    if not snap["ok"]:
        return {"ok": False, "detail": snap["detail"]}

    present = snap["keys"]
    confirmed, gone = [], []
    for row in history.recent(limit=5000):
        key, state = row["file_key"], row["state"]
        if key in present and state != history.CONFIRMED:
            confirmed.append(key)
        elif key not in present and state == history.CONFIRMED:
            gone.append(key)

    history.mark(confirmed, history.CONFIRMED, "seen in JD")
    history.mark(gone, history.GONE, "no longer in JD")
    return {
        "ok": True,
        "in_jd": len(present),
        "confirmed": len(confirmed),
        "gone": len(gone),
        "linkgrabber": len(snap["linkgrabber"]),
        "downloads": len(snap["downloads"]),
        "packages": snap["packages"][:50],
    }
