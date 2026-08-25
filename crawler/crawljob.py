"""Hand selected links to JDownloader via its folderwatch extension.

folderwatch is a documented JD feature: it polls a directory and picks up
.crawljob files, which are plain key=value text. Nothing clever here — it is
just the tidiest way to get a link from another process into JD.

Only links you explicitly select reach this module; nothing auto-submits.
"""
import os, re, json
from datetime import datetime

DEFAULT_WATCH = "/home/media/docker/torrentvpn-app/jdownloader/config/folderwatch"
# JD runs in a container, so downloadFolder must be a path JD sees, not a host
# path. /output is the container's side of the download bind mount.
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
    return True, d


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name or "").strip().rstrip(". ") or "release"


def write(urls: list[str], name: str, package: str = "", subfolder: str = "",
          auto_start: bool = False) -> str:
    """Drop one .crawljob into the folderwatch directory.

    Written as a JSON array. folderwatch accepts both that and key=value, but
    key=value has no way to express a list of jobs and quietly mangles a value
    containing a newline -- which is exactly what a multi-link "text" field is.

    autoStart defaults off: the job lands in JD and waits, so a bad selection
    is a line to delete rather than a download already running.
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
        "overwritePackagizerEnabled": "FALSE",
    }

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump([job], f, indent=1)
    # Rename into place so folderwatch never reads a half-written file; its
    # poll is on a 10s timer and does not care that the file appeared whole.
    os.replace(tmp, path)
    return path
