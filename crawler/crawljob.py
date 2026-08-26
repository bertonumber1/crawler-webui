"""Hand selected links to JDownloader via its Folder Watch extension."""
import json
import os
import re
from datetime import datetime

from . import links as linkmod, trace

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

    # Preserve order, drop duplicate representations of the same file, and
    # refuse anything that is not a usable download URL on a harvested host.
    #
    # This is the last point before a link leaves the process, and JD reports
    # nothing either way: it consumes the file, moves it to added/, and stays
    # silent whether the contents made sense or not. Rubbish accepted here is
    # rubbish that looks queued -- and once the sent-history records it, it
    # sits there permanently suppressing nothing.
    clean, rejected = [], []
    seen = set()
    for raw in urls:
        raw = (raw or "").strip()
        if not raw:
            continue
        canonical = linkmod.canonical_download_url(raw)
        if not canonical:
            rejected.append(raw)
            continue
        key = linkmod.canonical_download_key(canonical)
        if key in seen:
            continue
        seen.add(key)
        clean.append(canonical)

    if rejected:
        trace.event("handoff", "links rejected before handoff",
                    count=len(rejected), first=rejected[0][:120])
    if not clean:
        raise ValueError(
            "no usable links: " + (
                f"{len(rejected)} rejected (not a harvested host, or malformed)"
                if rejected else "none given"))

    pkg = _safe(package or name or "crawler-webui")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    fn = re.sub(r"\W+", "_", name or "job")[:60].strip("_") or "job"
    path = os.path.join(watch_dir(), f"cw_{fn}_{stamp}.crawljob")

    # Six fields, and no more. This is the set with a long record of being
    # accepted -- flacattack_grab.py queued 91 jobs with exactly these -- and
    # every field beyond it is a chance to get a type wrong.
    #
    # That is not hypothetical. overwritePackagizerEnabled is a primitive
    # boolean in JD; sent as the string "FALSE" it fails to deserialise and
    # the whole job is discarded in silence: the file is consumed, moved to
    # added/, logged as an accepted CrawlerJob, and no package ever appears.
    # Absent and correct-boolean forms both work, so the field earns nothing
    # and can cost everything. Omitted.
    job = {
        "enabled": "TRUE",
        "text": "\n".join(clean),
        "packageName": pkg,
        "downloadFolder": subfolder or os.path.join(download_root(), pkg),
        "autoStart": "TRUE" if auto_start else "FALSE",
        "autoConfirm": "TRUE",
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
