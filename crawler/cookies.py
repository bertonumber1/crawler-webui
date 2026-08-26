"""Crawl as a logged-in member, using cookies exported from a browser.

Most forums show a guest the thread and hide the links. seriousaboutsound.net
is the case in point: a topic page renders fine without an account and carries
no file hosts at all, only "Sign in to" and "Unlock". Crawling it anonymously
returns zero downloadable links and looks exactly like a site with nothing on
it, which is the worst kind of wrong answer.

Rather than script a login per forum -- different software, captchas, 2FA,
and a password to store for each -- the crawler reuses a session that already
exists in a browser. Cookies are read from a Netscape cookies.txt, which is
what every browser extension exports and what curl and yt-dlp already speak.

Nothing here logs in, and no password is ever handled. If the exported session
expires the crawler goes back to seeing what a guest sees, and says so.
"""
from __future__ import annotations

import http.cookiejar
import os
import threading
from urllib.parse import urlparse

from . import trace

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "cookies.txt")

_lock = threading.Lock()
_jar: http.cookiejar.MozillaCookieJar | None = None
_loaded_from = ""
_error = ""


def path() -> str:
    return os.environ.get("CW_COOKIES_FILE", DEFAULT_PATH)


def load(where: str | None = None) -> dict:
    """(Re)load the cookie file. Never raises -- a bad file means no cookies."""
    global _jar, _loaded_from, _error
    target = where or path()
    jar = http.cookiejar.MozillaCookieJar(target)
    try:
        # Keep session cookies and expired-looking ones: an exported session
        # frequently has no expiry set, and discarding those throws away the
        # very cookie that proves you are logged in.
        jar.load(ignore_discard=True, ignore_expires=True)
    except FileNotFoundError:
        with _lock:
            _jar, _loaded_from, _error = None, "", ""
        return status()
    except Exception as e:
        with _lock:
            _jar, _loaded_from = None, ""
            _error = f"{type(e).__name__}: {e}"
        trace.event("cookies", "load failed", path=target, error=_error[:120])
        return status()

    with _lock:
        _jar, _loaded_from, _error = jar, target, ""
    trace.event("cookies", "loaded", path=target, cookies=len(jar),
                domains=len(domains()))
    return status()


def jar():
    return _jar


def domains() -> list[str]:
    if _jar is None:
        return []
    return sorted({c.domain.lstrip(".") for c in _jar})


def have_for(url: str) -> bool:
    """Whether we hold a cookie that would be sent to this URL's host."""
    if _jar is None:
        return False
    host = (urlparse(url or "").netloc.lower().split("@")[-1].split(":")[0])
    if not host:
        return False
    for d in domains():
        if host == d or host.endswith("." + d):
            return True
    return False


def status() -> dict:
    return {
        "path": path(),
        "loaded": _jar is not None,
        "count": len(_jar) if _jar is not None else 0,
        "domains": domains(),
        "error": _error,
    }


def save_text(text: str, where: str | None = None) -> dict:
    """Write a pasted cookies.txt and load it.

    Accepts the file as text so it can be pasted straight from a browser
    extension without needing a file upload path into the container.
    """
    target = where or path()
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    body = (text or "").strip()
    # MozillaCookieJar refuses a file without this exact first line.
    header = "# Netscape HTTP Cookie File"
    if not body.startswith("#"):
        body = header + "\n" + body
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body.rstrip("\n") + "\n")
    os.replace(tmp, target)
    return load(target)


def clear() -> dict:
    """Forget the session, and remove the file so it is not silently reused."""
    global _jar, _loaded_from, _error
    try:
        os.remove(path())
    except OSError:
        pass
    with _lock:
        _jar, _loaded_from, _error = None, "", ""
    trace.event("cookies", "cleared")
    return status()


load()
