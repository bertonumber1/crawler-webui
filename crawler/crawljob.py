"""Hand selected links to JDownloader via its folderwatch extension.

Folder Watch works especially well with a Dockerised JD instance when the
crawler and JD share the same host directory as a volume.  The crawler writes
a complete .crawljob into the *host-side* folderwatch directory; JD sees the
same file through its container mount and moves it to ``added`` after import.

Only links explicitly selected by the WebUI reach this module.
"""
import os, re, json
from datetime import datetime

# This is the host-side path.  Set CW_FOLDERWATCH when the crawler is itself
# containerised or when the JD compose volume lives somewhere else.
DEFAULT_WATCH = "/home/media/docker/torrentvpn-app/jdownloader/config/folderwatch"
# This is deliberately the JD-container-visible download root.  It is only
# used as the value written into downloadFolder; set CW_DOWNLOAD_ROOT to the
# path that JD itself sees inside its container.
DEFAULT_ROOT = "/output/_CRAWLER"


def watch_dir() -> str:
    return os.environ.get("CW_FOLDERWATCH", DEFAULT_WATCH)


def download_root() -> str:
    return os.environ.get("CW_DOWNLOAD_ROOT", DEFAULT_ROOT)


def available() -> tuple[bool, str]:
    d = watch_dir()
    if not os.path.isdir(d):
        return False, f"folderwatch dir not found: {d}"
    if not os.access(d, os.W_OK):
        return False, f"folderwatch dir not writable: {d}"
    # JD creates this after processing crawljobs.  Its absence is not an
    # error: a newly-created folderwatch directory can legitimately have no
    # added directory yet.
    return True, d


def status() -> dict:
    """Return enough information to diagnose host/container mount problems."""
    d = watch_dir()
    ok, detail = available()
    added = os.path.join(d, "added")
    return {
        "ok": ok,
        "folderwatch": d,
        "writable": bool(ok),
        "added_dir": added,
        "added_exists": os.path.isdir(added),
        "download_root_for_jd": download_root(),
        "detail": detail,
    }


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name or "").strip().rstrip(". ") or "release"


def write(urls: list[str], name: str, package: str = "", subfolder: str = "",
          auto_start: bool = False) -> str:
    """Drop one JSON-format .crawljob into the shared Folder Watch directory.

    JDownloader officially supports JSON crawljobs and allows multiple jobs in
    one file.  A single JSON object is used here so every selected batch is one
    atomic import.  The file is first written beside the watch directory and
    then atomically renamed into it, preventing JD's polling thread from ever
    seeing a partial file.
    """
    ok, detail = available()
    if not ok:
        raise RuntimeError(detail)
    urls = [u.strip() for u in urls if u and u.strip()]
    if not urls:
        raise ValueError("no links given")

    pkg = _safe(package or name or "crawler-webui")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    fn = re.sub(r"\W+", "_", name or "job")[:60].strip("_") or "job"
    path = os.path.join(watch_dir(), f"cw_{fn}_{stamp}.crawljob")

    job = {
        "text": "\n".join(urls),
        "packageName": pkg,
        "downloadFolder": subfolder or os.path.join(download_root(), pkg),
        "enabled": "TRUE",
        "autoStart": "TRUE" if auto_start else "FALSE",
        "autoConfirm": "TRUE" if auto_start else "FALSE",
        "overwritePackagizerEnabled": False,
    }

    # Put the temporary file in the same filesystem so os.replace() is truly
    # atomic.  JD's Folder Watch can poll as often as 1 second, so this matters.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump([job], f, indent=1, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path
