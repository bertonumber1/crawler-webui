"""Polite HTTP layer. Every network request in the app goes through here.

Four things it enforces, so no provider has to remember them:

  robots.txt   consulted per host and cached; a disallowed path raises Blocked
               rather than being fetched.
  rate limit   a minimum gap between requests to the same host, so a crawl of
               40 forum pages does not arrive as 40 simultaneous connections.
  conditional  ETag / Last-Modified are stored per URL and replayed as
               If-None-Match / If-Modified-Since. A 304 costs one small
               round trip and no parsing.
  identity     an honest User-Agent naming the tool and the operator, so an
               admin who sees it in a log knows who to contact.

There is deliberately no mechanism here for defeating a bot challenge. If a
host answers 403 to an honest client, that is the host declining, and the
crawler reports it as such rather than working around it.
"""
import re, time, threading, os
import urllib.robotparser as robotparser
from urllib.parse import urlparse, urljoin
import httpx
from . import db


def _load_env(path=None):
    """Read .env beside the app, without adding a dependency.

    Real environment variables always win, so a systemd unit or a shell export
    can still override the file. Without this the shipped .env.example was
    decorative: every getenv below silently fell through to its default.
    """
    path = path or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".env")
    try:
        with open(path) as fh:
            lines = fh.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

UA = "crawler-webui/0.1 (personal article+release watcher; contact: local operator)"
MIN_GAP = 2.0          # seconds between requests to the same host
ROBOTS_TTL = 3600.0

# Optional FlareSolverr fallback. It is only used when the normal HTTP client
# receives a challenge/blocking response. Keep the normal path first because
# RSS/HTML requests are much cheaper that way.
FLARESOLVERR_URL = os.getenv(
    "FLARESOLVERR_URL", "http://192.168.0.182:8191/v1"
).rstrip("/")
FLARESOLVERR_ENABLED = os.getenv(
    "FLARESOLVERR_ENABLED", "1"
).lower() not in ("0", "false", "no")
FLARESOLVERR_TIMEOUT = float(os.getenv("FLARESOLVERR_TIMEOUT", "120"))
FLARESOLVERR_ON = {
    int(x.strip()) for x in os.getenv("FLARESOLVERR_ON", "403,429,503").split(",")
    if x.strip().isdigit()
}

_lock = threading.Lock()
_last_hit: dict[str, float] = {}
_robots: dict[str, tuple[float, robotparser.RobotFileParser]] = {}
db.init_cache()
db.init_settings()


def _build(proxy: str | None):
    """httpx 0.28 takes a single `proxy=` string; None means direct."""
    return httpx.Client(
        headers={"User-Agent": UA, "Accept": "*/*"},
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        proxy=proxy or None,
    )


_client = _build(db.setting("proxy"))


def current_proxy() -> str:
    return db.setting("proxy")


def set_proxy(url: str) -> str:
    """Swap the shared client over to a new proxy (empty string = direct).
    Robots and rate-limit state are cleared: a different egress is a different
    conversation with the host, and the cached robots.txt may not apply."""
    global _client, _robots, _last_hit
    url = (url or "").strip()
    if url and not re.match(r"^(https?|socks5h?)://", url):
        raise ValueError("proxy must start with http://, https://, socks5:// or socks5h://")
    new = _build(url)
    old, _client = _client, new
    try:
        old.close()
    except Exception:
        pass
    db.set_setting("proxy", url)
    with _lock:
        _robots.clear()
        _last_hit.clear()
    return url


def egress_ip(via_proxy: bool) -> tuple[bool, str]:
    """What IP does the far end actually see? The only honest way to confirm a
    proxy is doing anything is to compare this with and without it."""
    client = _client if via_proxy else _build("")
    try:
        r = client.get("https://api.ipify.org?format=json", timeout=15)
        r.raise_for_status()
        return True, r.json().get("ip", "?")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        if not via_proxy:
            try:
                client.close()
            except Exception:
                pass


class Blocked(Exception):
    """robots.txt disallows this path, or the host declined an honest request."""


class FetchError(Exception):
    pass


def _host(url) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def robots_ok(url) -> tuple[bool, str]:
    base = _host(url)
    now_t = time.monotonic()
    with _lock:
        hit = _robots.get(base)
    if not hit or now_t - hit[0] > ROBOTS_TTL:
        rp = robotparser.RobotFileParser()
        rp.set_url(urljoin(base, "/robots.txt"))
        try:
            r = _client.get(urljoin(base, "/robots.txt"))
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                rp.parse([])          # no robots.txt = nothing disallowed
        except Exception:
            rp.parse([])
        with _lock:
            _robots[base] = (now_t, rp)
        hit = (now_t, rp)
    allowed = hit[1].can_fetch(UA, url)
    delay = hit[1].crawl_delay(UA)
    return allowed, (f"crawl-delay {delay}s" if delay else "")


def _throttle(url):
    """Wait out whichever is longer: our floor, or the host's Crawl-delay.

    robots_ok() already parses Crawl-delay. It used to be returned as a note
    and thrown away, so a site asking for 10s got requests every 2s.
    """
    base = _host(url)
    gap = MIN_GAP
    with _lock:
        hit = _robots.get(base)
    if hit:
        try:
            declared = hit[1].crawl_delay(UA)
        except Exception:
            declared = None
        if declared:
            gap = max(gap, float(declared))
    with _lock:
        last = _last_hit.get(base, 0.0)
        wait = gap - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    with _lock:
        _last_hit[base] = time.monotonic()


def _looks_like_challenge(body: str, headers: dict) -> bool:
    """Detect common browser-challenge pages that incorrectly return HTTP 200."""
    if not body:
        return False
    ct = (headers.get("content-type") or "").lower()
    if "html" not in ct and ct:
        return False
    sample = body[:250000].lower()
    markers = (
        "cf-chl-", "cf-turnstile", "challenge-platform",
        "just a moment...", "checking your browser",
        "ddos-guard", "enable javascript and cookies to continue",
        "/cdn-cgi/challenge-platform/",
    )
    return any(m in sample for m in markers)


def _flaresolverr_get(url: str) -> tuple[int, str, dict]:
    """Fetch a page through a local FlareSolverr instance.

    FlareSolverr returns the solved browser page as `solution.response`.
    This is deliberately a fallback, not the default transport.
    """
    if not FLARESOLVERR_ENABLED:
        raise FetchError("FlareSolverr fallback is disabled")

    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": int(FLARESOLVERR_TIMEOUT * 1000),
    }
    try:
        r = httpx.post(
            FLARESOLVERR_URL,
            json=payload,
            timeout=httpx.Timeout(FLARESOLVERR_TIMEOUT + 10, connect=10),
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise FetchError(f"FlareSolverr request failed: {type(e).__name__}: {e}") from e

    if data.get("status") != "ok":
        msg = data.get("message") or data.get("error") or "unknown FlareSolverr error"
        raise FetchError(f"FlareSolverr returned {data.get('status')}: {msg}")

    solution = data.get("solution") or {}
    status = int(solution.get("status", 200))
    body = solution.get("response") or ""
    headers = solution.get("headers") or {}
    if not body:
        raise FetchError("FlareSolverr returned an empty response")

    return status, body, headers


def get(url, conditional=True) -> tuple[int, str, dict]:
    """Returns (status, text, headers). status 304 means unchanged, text ''."""
    allowed, note = robots_ok(url)
    if not allowed:
        raise Blocked(f"robots.txt disallows {url}")

    headers = {}
    if conditional:
        c = db.cache_get(url)
        if c.get("etag"):
            headers["If-None-Match"] = c["etag"]
        if c.get("last_modified"):
            headers["If-Modified-Since"] = c["last_modified"]

    _throttle(url)
    try:
        r = _client.get(url, headers=headers)
    except httpx.HTTPError as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e

    db.cache_put(url, r.headers.get("etag", ""),
                 r.headers.get("last-modified", ""), r.status_code)

    if r.status_code == 304:
        return 304, "", dict(r.headers)

    # Some challenge pages return HTTP 200. Detect those as well as explicit
    # 403/429/503 responses, otherwise the parser would receive only the
    # challenge shell and report "no links".
    challenge = _looks_like_challenge(r.text, dict(r.headers))
    if (r.status_code in FLARESOLVERR_ON or challenge) and FLARESOLVERR_ENABLED:
        try:
            return _flaresolverr_get(url)
        except FetchError as e:
            raise Blocked(
                f"host returned {r.status_code}; FlareSolverr fallback failed: {e}"
            ) from e

    if r.status_code in (401, 403):
        raise Blocked(f"host returned {r.status_code} to the HTTP client")
    if r.status_code == 429:
        raise Blocked("host returned 429 (rate limited) — back off and retry later")
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code}")
    return r.status_code, r.text, dict(r.headers)
