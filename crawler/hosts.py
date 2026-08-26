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


_RAPIDGATOR_FILE = re.compile(
    r"^https?://(?:www\.)?rapidgator\.net/file/([A-Za-z0-9]+)(?:/([^?#]*))?", re.I)


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
        Host("mega", "mega.nz", "MEGA", False, _passthrough("mega.nz")),
        Host("mediafire", "mediafire.com", "MediaFire", False, _passthrough("mediafire.com")),
        Host("pixeldrain", "pixeldrain.com", "Pixeldrain", False, _passthrough("pixeldrain.com")),
        Host("gofile", "gofile.io", "GoFile", False, _passthrough("gofile.io")),
    )
}

DEFAULT_ENABLED = ("rapidgator",)


def _configured() -> tuple[str, ...]:
    raw = (os.getenv("CRAWLER_HOSTS") or "").strip()
    if not raw:
        return DEFAULT_ENABLED
    if raw.lower() == "all":
        return tuple(KNOWN)
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
    return {h.domain for h in enabled()}


def by_domain(url_or_host: str) -> Host | None:
    host = url_or_host if "/" not in url_or_host else _host_only(url_or_host)
    host = host.lower().removeprefix("www.")
    for h in enabled():
        if host == h.domain:
            return h
    return None


def is_enabled(url: str) -> bool:
    return by_domain(url) is not None


def reduce(url: str) -> tuple[str | None, str | None]:
    """(identity, canonical url) for an enabled host; (None, None) otherwise."""
    h = by_domain(url)
    if h is None:
        return None, None
    if h.reduce is None:
        clean = (url or "").split("#", 1)[0].rstrip("/")
        return f"{h.domain}:{clean.lower()}", clean
    return h.reduce(url)


def label(url: str) -> str:
    h = by_domain(url)
    return h.label if h else _host_only(url)


# Every domain the classifier should recognise, enabled or not. The resolver
# needs to know a Nitroflare link IS a file host even while Nitroflare is
# switched off, so it stops treating that page as a wrapper worth following.
ALL_DOMAINS: set[str] = {h.domain for h in KNOWN.values()}
ALL_LABELS: dict[str, str] = {h.domain: h.label for h in KNOWN.values()}
