"""Hand selected links to JDownloader via its folderwatch extension.

folderwatch is a documented JD feature: it polls a directory and picks up
.crawljob files, which are plain key=value text or JSON. Nothing clever here
— it is just the tidiest way to get a link from another process into JD.

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

    JDownloader treats ``autoConfirm`` and ``autoStart`` as separate controls.
    autoConfirm moves successfully crawled links from LinkGrabber into the
    Download List; autoStart controls whether those confirmed downloads begin.
    We therefore always auto-confirm crawler jobs and leave auto-start under
    the caller's control. This makes the default behavior "visible in JD and
    waiting", rather than "processed into LinkGrabber but seemingly missing".

    JSON is used because one crawljob can contain multiple URLs and because JD
    officially supports JSON crawljobs for this purpose.
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
        # Always confirm into JD's Download List. autoStart independently
        # decides whether JD should begin downloading immediately.
        "autoConfirm": "TRUE",
        "autoStart": "TRUE" if auto_start else "FALSE",
        "overwritePackagizerEnabled": false,
    }

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([job], f, indent=1)
        f.write("\n")
    # Rename into place so folderwatch never reads a half-written file; its
    # poll is on a timer and does not care that the file appeared whole.
    os.replace(tmp, path)
    return path
