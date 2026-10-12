"""Which file hosts the crawler is allowed to harvest, in one place.

Rapidgator-only used to be asserted in three separate places: the
DOWNLOAD_HOSTS set in links.py, is_downloadable() in resolve.py, and the
dedupe boundary in server.py. Turning a host on meant finding all three and
agreeing with yourself. Now they all read this.

A host is described by its domain, a display label, and how to reduce one of
its URLs to a stable identity -- the same file offered as /file/<id> and
/file/<id>/<name>.html must collapse to one key or it enters the queue twice.

Set CRAWLER_HOSTS to a comma-separated list of keys to change what is
enabled without touching code:

    CRAWLER_HOSTS=rapidgator            # the default
    CRAWLER_HOSTS=rapidgator,nitroflare
    CRAWLER_HOSTS=all
    CRAWLER_HOSTS=cloud                 # rapidgator + every free cloud drive
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Callable
from urllib.parse import urlparse


def _host_only(url: str) -> str:
    return urlparse(url or "").netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")


@dataclass(frozen=True)
class Host:
    key: str
    domain: str
    label: str
    premium: bool = True
    # Reduce a URL to (identity, canonical_url). Returning None for the
    # identity rejects the URL -- a truncated or display-only form that would
    # fail in the downloader anyway.
    reduce: Callable[[str], tuple[str | None, str | None]] | None = None
    # Other domains serving the same host. Cloud drives rarely live on one
    # name: TeraBox links arrive as 1024terabox.com, teraboxapp.com and more.
    aliases: tuple[str, ...] = ()
    # A site serving its own files: fetched politely by crawler/direct.py,
    # one at a time, not handed to JD's 20 parallel downloads.
    direct: bool = False

    @property
    def domains(self) -> tuple[str, ...]:
        return (self.domain,) + self.aliases


_RAPIDGATOR_FILE = re.compile(
    r"^https?://(?:www\.)?rapidgator\.net/file/([A-Za-z0-9]+)(?:/([^?#]*))?", re.I)


_MEGA = re.compile(
    r"^https?://(?:www\.)?mega(?:\.co)?\.nz/(folder|file)/([A-Za-z0-9_-]+)(#[A-Za-z0-9_-]+)?", re.I)


_MEGA_OLD = re.compile(
    r"^https?://(?:www\.)?mega(?:\.co)?\.nz/#(F?)!([A-Za-z0-9_-]+)!([A-Za-z0-9_-]+)", re.I)


def _mega(url: str) -> tuple[str | None, str | None]:
    # The part after # is the decryption key, not a page anchor. Strip it and
    # the link is a locked folder nobody can open, so it is kept and it is part
    # of the identity -- the same folder id with two keys is two different
    # grabs. A link with no key is rejected: it cannot be downloaded.
    # Old posts use mega.co.nz/#!id!key (file) and #F!id!key (folder): the same
    # thing, rewritten to today's form so one file has one identity.
    old = _MEGA_OLD.match(url or "")
    if old:
        kind = "folder" if old.group(1) else "file"
        url = f"https://mega.nz/{kind}/{old.group(2)}#{old.group(3)}"
    m = _MEGA.match(url or "")
    if not m:
        return None, None
    kind, ident, key = m.group(1), m.group(2), m.group(3) or ""
    if not key:
        return None, None
    canonical = f"https://mega.nz/{kind}/{ident}{key}"
    return f"mega:{ident}{key}", canonical


_GDRIVE_ID = re.compile(
    r"(?:/(?:file/d|drive/(?:u/\d+/)?folders|folders)/|[?&]id=)([A-Za-z0-9_-]{10,})")


def _gdrive(url: str) -> tuple[str | None, str | None]:
    # The same file is shared as /file/d/<id>/view, /open?id=<id> and
    # /uc?id=<id>&export=download. Collapse them to the id. A folder keeps its
    # own canonical form so JD opens it as a folder.
    m = _GDRIVE_ID.search(url or "")
    if not m:
        return None, None
    ident = m.group(1)
    if "/folders/" in url:
        return f"gdrive:{ident}", f"https://drive.google.com/drive/folders/{ident}"
    return f"gdrive:{ident}", f"https://drive.google.com/file/d/{ident}"


def _rapidgator(url: str) -> tuple[str | None, str | None]:
    m = _RAPIDGATOR_FILE.match(url or "")
    if not m:
        return None, None
    file_id, filename = m.groups()
    canonical = (f"https://rapidgator.net/file/{file_id}/{filename}"
                 if filename else f"https://rapidgator.net/file/{file_id}")
    return f"rapidgator:{file_id}", canonical


def _passthrough(domain: str):
    def reduce(url: str) -> tuple[str | None, str | None]:
        clean = url.split("#", 1)[0].rstrip("/")
        return f"{domain}:{clean.lower()}", clean
    return reduce


_SITE_FILE_EXT = ("mp3", "flac", "wav", "aiff", "aif", "ogg", "m4a", "opus",
                  "zip", "rar", "7z", "jpg", "jpeg", "png", "gif", "webp")


def _site_files(domain: str, *dirs: str):
    """A site that serves its own files (no file host in between). Only paths
    under `dirs` ending in a media/image extension are files; every other
    page on the site is a page, never a download."""
    def reduce(url: str) -> tuple[str | None, str | None]:
        p = urlparse((url or "").split("#", 1)[0])
        path = p.path
        if not any(path.lower().startswith(d) for d in dirs):
            return None, None
        if path.rsplit(".", 1)[-1].lower() not in _SITE_FILE_EXT:
            return None, None
        clean = f"https://www.{domain}{path}"
        return f"{domain}:{path.lower()}", clean
    return reduce


KNOWN: dict[str, Host] = {
    h.key: h for h in (
        Host("rapidgator", "rapidgator.net", "Rapidgator", True, _rapidgator),
        Host("nitroflare", "nitroflare.com", "Nitroflare", True, _passthrough("nitroflare.com")),
        Host("ddownload", "ddownload.com", "DDownload", True, _passthrough("ddownload.com")),
        Host("katfile", "katfile.com", "Katfile", True, _passthrough("katfile.com")),
        Host("turbobit", "turbobit.net", "Turbobit", True, _passthrough("turbobit.net")),
        Host("hitfile", "hitfile.net", "HitFile", True, _passthrough("hitfile.net")),
        Host("uploadgig", "uploadgig.com", "UploadGig", True, _passthrough("uploadgig.com")),
        Host("fikper", "fikper.com", "Fikper", True, _passthrough("fikper.com")),
        Host("onefichier", "1fichier.com", "1fichier", True, _passthrough("1fichier.com")),
        Host("mega", "mega.nz", "MEGA", False, _mega, ("mega.co.nz",)),
        Host("mediafire", "mediafire.com", "MediaFire", False, _passthrough("mediafire.com")),
        Host("pixeldrain", "pixeldrain.com", "Pixeldrain", False, _passthrough("pixeldrain.com")),
        Host("gofile", "gofile.io", "GoFile", False, _passthrough("gofile.io")),
        # Cloud drives. Free to download (no debrid), JDownloader has a plugin
        # for each. Seen on dark-decade.foroactivo.com: MEGA and TeraBox.
        Host("gdrive", "drive.google.com", "Google Drive", False, _gdrive,
             ("docs.google.com", "drive.usercontent.google.com")),
        Host("terabox", "terabox.com", "TeraBox", False, _passthrough("terabox"),
             ("1024terabox.com", "teraboxapp.com", "terabox.app", "1024tera.com",
              "freeterabox.com", "4funbox.com", "mirrobox.com", "nephobox.com",
              "momerybox.com", "tibibox.com", "teraboxlink.com", "terasharelink.com")),
        Host("dropbox", "dropbox.com", "Dropbox", False, _passthrough("dropbox.com"),
             ("db.tt",)),
        Host("onedrive", "onedrive.live.com", "OneDrive", False, _passthrough("onedrive"),
             ("1drv.ms",)),
        Host("pcloud", "u.pcloud.link", "pCloud", False, _passthrough("pcloud"),
             ("e.pcloud.link", "pcloud.com")),
        Host("yandex", "disk.yandex.com", "Yandex Disk", False, _passthrough("yandex"),
             ("disk.yandex.ru", "yadi.sk", "disk.360.yandex.ru")),
        Host("workupload", "workupload.com", "Workupload", False, _passthrough("workupload.com")),
        Host("krakenfiles", "krakenfiles.com", "KrakenFiles", False, _passthrough("krakenfiles.com")),
        Host("sendcm", "send.cm", "Send.cm", False, _passthrough("send.cm")),
        Host("wetransfer", "wetransfer.com", "WeTransfer", False, _passthrough("wetransfer"),
             ("we.tl",)),
        # Transfer services: what makinamania.net's sharing threads actually
        # post in 2025-26. All expire (SwissTransfer and transfernow within
        # days to a month), so they are only worth harvesting from recent posts
        # -- see the date window, crawler/postdates.py.
        Host("swisstransfer", "swisstransfer.com", "SwissTransfer", False, _passthrough("swisstransfer.com")),
        Host("transferit", "transfer.it", "Transfer.it", False, _passthrough("transfer.it")),
        Host("transfernow", "transfernow.net", "TransferNow", False, _passthrough("transfernow.net")),
        Host("opendrive", "od.lk", "OpenDrive", False, _passthrough("od.lk"),
             ("opendrive.com",)),
        # Sites that host their own tape packs. Read by the WordPress provider;
        # JD's DirectHTTP sends the Referer the site's hotlink guard wants.
        Host("ravetapepacks", "ravetapepacks.com", "Rave Tape Packs", False,
             _site_files("ravetapepacks.com", "/music/", "/wp-content/uploads/"),
             direct=True),
    )
}

DEFAULT_ENABLED = ("rapidgator",)
# Hosts that need no premium account: what a forum posting MEGA or TeraBox
# folders actually offers. Rapidgator stays in, it is the debrid path.
CLOUD = ("rapidgator",) + tuple(k for k, h in KNOWN.items() if not h.premium)


def _configured() -> tuple[str, ...]:
    raw = (os.getenv("CRAWLER_HOSTS") or "").strip()
    if not raw:
        return DEFAULT_ENABLED
    if raw.lower() == "all":
        return tuple(KNOWN)
    if raw.lower() == "cloud":
        return CLOUD
    keys = tuple(k.strip().lower() for k in raw.split(",") if k.strip())
    return tuple(k for k in keys if k in KNOWN) or DEFAULT_ENABLED


_enabled: tuple[str, ...] = _configured()


def enabled() -> tuple[Host, ...]:
    return tuple(KNOWN[k] for k in _enabled)


def set_enabled(keys) -> tuple[Host, ...]:
    """Change the harvest set at runtime. Unknown keys are ignored."""
    global _enabled
    wanted = tuple(str(k).strip().lower() for k in keys)
    valid = tuple(k for k in wanted if k in KNOWN)
    if valid:
        _enabled = valid
    return enabled()


def domains() -> set[str]:
    return {d for h in enabled() for d in h.domains}


def by_domain(url_or_host: str) -> Host | None:
    host = url_or_host if "/" not in url_or_host else _host_only(url_or_host)
    host = host.lower().removeprefix("www.")
    for h in enabled():
        if host in h.domains:
            return h
    return None


def is_enabled(url: str) -> bool:
    return by_domain(url) is not None


# A shortened display URL such as /file/abc...part1.rar must never be reduced.
# It does not fail -- it succeeds, yielding file id "abc": a plausible identity
# for a file that does not exist. links.extract() drops these, but the reducer
# is the identity boundary and anything reaching it directly must be refused
# here too, or a forged identity enters the sent-history and permanently
# suppresses the real file.
TRUNCATED = "..."


# A file host's own pages are not files. makinamania: a "De nada" post linked
# https://www.swisstransfer.com/es (the Spanish home page) and it reached JD as
# a "release". A link counts only when it names something: a path beyond a
# language code or site page, or a query/fragment (mediafire.com/?key,
# mega.nz/#!id!key carry the file in those, not in the path).
_SITE_PAGES = {"about", "faq", "help", "terms", "tos", "privacy", "login",
               "signin", "register", "signup", "pricing", "pro", "premium",
               "blog", "contact", "legal", "home", "index.html", "upload", "app"}
_LOCALE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2})?$", re.I)


def names_a_file(url: str) -> bool:
    p = urlparse(url or "")
    segs = [x for x in p.path.split("/") if x]
    if p.query or (p.fragment and len(p.fragment) > 3):
        return True
    if not segs:
        return False
    if len(segs) == 1 and (_LOCALE.match(segs[0]) or segs[0].lower() in _SITE_PAGES):
        return False
    return True


def reduce(url: str) -> tuple[str | None, str | None]:
    """(identity, canonical url) for an enabled host; (None, None) otherwise."""
    if TRUNCATED in (url or ""):
        return None, None
    if not names_a_file(url):
        return None, None
    h = by_domain(url)
    if h is None:
        return None, None
    if h.reduce is None:
        clean = (url or "").split("#", 1)[0].rstrip("/")
        return f"{h.domain}:{clean.lower()}", clean
    return h.reduce(url)


def is_direct(url: str) -> bool:
    h = by_domain(url)
    return bool(h and h.direct)


def label(url: str) -> str:
    h = by_domain(url)
    return h.label if h else _host_only(url)


# Every domain the classifier should recognise, enabled or not. The resolver
# needs to know a Nitroflare link IS a file host even while Nitroflare is
# switched off, so it stops treating that page as a wrapper worth following.
ALL_DOMAINS: set[str] = {d for h in KNOWN.values() for d in h.domains}
ALL_LABELS: dict[str, str] = {d: h.label for h in KNOWN.values() for d in h.domains}
