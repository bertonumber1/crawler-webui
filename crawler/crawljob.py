"""Hand selected links to JDownloader via its Folder Watch extension."""
import json
import os
import re
from datetime import datetime

from . import trace

DEFAULT_WATCH = "/jdownloader/folderwatch"
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
    try:
        probe = os.path.join(d, ".crawler-write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok\n")
        os.unlink(probe)
    except OSError as e:
        return False, f"folderwatch dir is not writable: {d}: {e}"
    return True, d


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name or "").strip().rstrip(". ") or "release"


def write(urls: list[str], name: str, package: str = "", subfolder: str = "",
          auto_start: bool = False) -> str:
    """Write a JSON-format .crawljob and atomically place it in Folder Watch.

    JSON is used deliberately: it is JDownloader's multi-crawljob format and
    avoids ambiguity around embedded newlines in the ``text`` field.
    """
    ok, detail = available()
    if not ok:
        raise RuntimeError(detail)

    clean = []
    seen = set()
    for u in urls:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        clean.append(u)
    if not clean:
        raise ValueError("no links given")

    pkg = _safe(package or name or "crawler-webui")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    fn = re.sub(r"\W+", "_", name or "job")[:60].strip("_") or "job"
    path = os.path.join(watch_dir(), f"cw_{fn}_{stamp}.crawljob")

    job = {
        "enabled": "TRUE",
        "text": "\n".join(clean),
        "packageName": pkg,
        "autoConfirm": "TRUE",
        "autoStart": "TRUE" if auto_start else "FALSE",
        "forcedStart": "FALSE",
        "downloadFolder": subfolder or os.path.join(download_root(), pkg),
        "overwritePackagizerEnabled": False,
        "deepAnalyseEnabled": False,
    }

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump([job], f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    trace.event("handoff", "crawljob written", path=os.path.basename(path),
                package=pkg, links=len(clean), autostart=bool(auto_start))
    return path
