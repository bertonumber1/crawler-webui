"""Hand selected links to JDownloader via its folderwatch extension.

folderwatch is a documented JD feature: it polls a directory and picks up
.crawljob files, which are plain key=value text. Nothing clever here — it is
just the tidiest way to get a link from another process into JD.

Only links you explicitly select reach this module; nothing auto-submits.
"""
import os, re, json
from datetime import datetime

DEFAULT_WATCH = "/home/media/docker/torrentvpn-app/jdownloader/config/folderwatch"


def watch_dir() -> str:
    return os.environ.get("CW_FOLDERWATCH", DEFAULT_WATCH)


def available() -> tuple[bool, str]:
    d = watch_dir()
    if not os.path.isdir(d):
        return False, f"folderwatch dir not found: {d}"
    if not os.access(d, os.W_OK):
        return False, f"folderwatch dir not writable: {d}"
    return True, d


def write(urls: list[str], name: str, package: str = "", subfolder: str = "") -> str:
    ok, detail = available()
    if not ok:
        raise RuntimeError(detail)
    urls = [u for u in urls if u.strip()]
    if not urls:
        raise ValueError("no links given")

    safe = re.sub(r"\W+", "_", name or "job")[:60].strip("_") or "job"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(watch_dir(), f"cw_{safe}_{stamp}.crawljob")

    job = {
        "text": "\n".join(urls),
        "packageName": package or name or "crawler-webui",
        "enabled": "TRUE",
        "autoStart": "FALSE",       # lands in JD, waits for you to press go
        "autoConfirm": "FALSE",
        "overwritePackagizerEnabled": "FALSE",
    }
    if subfolder:
        job["downloadFolder"] = subfolder

    with open(path, "w") as f:
        for k, v in job.items():
            f.write(f"{k}={v}\n")
    return path
