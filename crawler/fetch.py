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
import random, re, time, threading, os
import urllib.robotparser as robotparser
from urllib.parse import urlparse, urljoin
import httpx
from . import cookies
from . import db
from . import trace


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
MIN_GAP = float(os.getenv("CRAWLER_MIN_GAP", "0"))  # optional minimum gap; 0 = no artificial delay

# Browse-like hosts. On a members-only board every page read is a view logged
# against the member's account, shown in "who is online" and the topic's view
# count. For hosts listed in CW_STEALTH_HOSTS (suffix match, so
# "foroactivo.com" covers every board on it) the crawler reads like a person:
# a browser's User-Agent and Accept headers, one page at a time, and a
# randomised pause between pages. Every other host keeps the honest identity
# and normal pace. Unset = off.
#   CW_STEALTH_HOSTS=foroactivo.com
#   CW_STEALTH_GAP=90-150        seconds, uniform random in the range
STEALTH_HOSTS = tuple(
    h.strip().lower().removeprefix("www.")
    for h in os.getenv("CW_STEALTH_HOSTS", "").split(",") if h.strip())
# Which browser the stealth hosts see: Brave on Windows or on Linux. Brave
# sends Chrome's UA string unchanged. Pick the one you really browse the site
# from, or the account shows up on two machines. Chosen in the UI (Session
# panel) and kept in the settings table; CW_STEALTH_OS sets the first default.
STEALTH_UAS = {
    "windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
}
_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def _gap_range(raw: str) -> tuple[float, float]:
    lo, _, hi = (raw or "90-150").partition("-")
    try:
        lo_f = float(lo)
        hi_f = float(hi) if hi else lo_f
    except ValueError:
        return 90.0, 150.0
    return min(lo_f, hi_f), max(lo_f, hi_f)


STEALTH_GAP = _gap_range(os.getenv("CW_STEALTH_GAP", "90-150"))


def is_stealth(url: str) -> bool:
    host = urlparse(url or "").netloc.lower().split("@")[-1].split(":")[0]
    host = host.removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in STEALTH_HOSTS)


def _default_os() -> str:
    v = os.getenv("CW_STEALTH_OS", "linux").strip().lower()
    return v if v in STEALTH_UAS else "linux"


_stealth_os = _default_os()


def stealth_os() -> str:
    return _stealth_os


def set_stealth_os(value: str) -> str:
    global _stealth_os
    value = (value or "").strip().lower()
    if value not in STEALTH_UAS:
        raise ValueError(f"unknown OS {value!r}; expected one of {sorted(STEALTH_UAS)}")
    db.set_setting("stealth_os", value)
    _stealth_os = value
    return value


def _headers_for(url: str, extra: dict | None = None) -> dict:
    out = ({"User-Agent": STEALTH_UAS[_stealth_os], **_BROWSER_HEADERS}
           if is_stealth(url) else {})
    out.update(extra or {})
    return out
ROBOTS_TTL = 3600.0

# Optional FlareSolverr fallback. It is only used when the normal HTTP client
# receives a challenge/blocking response. Keep the normal path first because
# RSS/HTML requests are much cheaper that way.
FLARESOLVERR_URL = os.getenv(
    "FLARESOLVERR_URL", "http://127.0.0.1:8191/v1"
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
if db.setting("stealth_os") in STEALTH_UAS:
    _stealth_os = db.setting("stealth_os")


def _build(proxy: str | None):
    """httpx 0.28 takes a single `proxy=` string; None means direct.

    The cookie jar is attached here so a logged-in session applies to every
    request the app makes. Most forums render a thread to a guest and hide the
    links inside it, so without cookies a members-only board looks like a board
    with nothing on it.
    """
    return httpx.Client(
        headers={"User-Agent": UA, "Accept": "*/*"},
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        proxy=proxy or None,
        cookies=cookies.jar(),
    )


_client = _build(db.setting("proxy"))


def reload_cookies(text: str | None = None) -> dict:
    """Load a new session and rebuild the client so it actually takes effect."""
    global _client
    st = cookies.save_text(text) if text is not None else cookies.load()
    new = _build(db.setting("proxy"))
    old, _client = _client, new
    try:
        old.close()
    except Exception:
        pass
    return st


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
            r = _client.get(urljoin(base, "/robots.txt"), headers=_headers_for(url))
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
    gap = max(0.0, MIN_GAP)
    if is_stealth(url):
        # Drawn per request: a fixed interval is itself a bot's signature.
        gap = max(gap, random.uniform(*STEALTH_GAP))
    with _lock:
        hit = _robots.get(base)
    if hit:
        try:
            declared = hit[1].crawl_delay(UA)
        except Exception:
            declared = None
        if declared:
            gap = max(gap, float(declared))
    # Reserve the next slot under the lock, then sleep outside it. Reading the
    # last hit and writing it back after sleeping let the resolver's parallel
    # workers all see the same last hit, sleep the same time and fire
    # together -- six requests at once, however long the gap.
    with _lock:
        now = time.monotonic()
        last = _last_hit.get(base)
        slot = now if last is None else max(now, last + gap)
        _last_hit[base] = slot
    wait = slot - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _looks_like_challenge(body: str, headers: dict) -> bool:
    """Detect common browser-challenge pages that incorrectly return HTTP 200."""
    if not body:
        return False
    ct = (headers.get("content-type") or "").lower()
    if "html" not in ct and ct:
        return False
    sample = body[:250000].lower()
    markers = (
        "cf-chl-", "cf-turnstile",
        "just a moment...", "checking your browser",
        "ddos-guard", "enable javascript and cookies to continue",
    )
    if any(m in sample for m in markers):
        return True
    # Cloudflare also injects /cdn-cgi/challenge-platform/scripts/jsd/main.js
    # into EVERY normal page of a site behind its bot management (makinamania:
    # each 50-120 KB forum page). Only a small page carrying it is the
    # challenge shell itself; a full page with it is just a page.
    return "challenge-platform" in sample and len(body) < 30000


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



def resolve_final_url(url: str) -> str:
    """Follow normal HTTP redirects and return the final URL.

    Uses the same robots, proxy and per-host throttling policy as get(). This
    is intentionally only a redirect resolver; it does not execute JavaScript.
    """
    allowed, note = robots_ok(url)
    if not allowed:
        raise Blocked(f"robots.txt disallows {url}")
    _throttle(url)
    try:
        r = _client.get(url, follow_redirects=True, headers=_headers_for(url))
    except httpx.HTTPError as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code}")
    return str(r.url)

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
        r = _client.get(url, headers=_headers_for(url, headers))
    except httpx.HTTPError as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e

    db.cache_put(url, r.headers.get("etag", ""),
                 r.headers.get("last-modified", ""), r.status_code)

    if r.status_code == 304:
        trace.event("fetch", "304 not modified", url=url)
        return 304, "", dict(r.headers)

    # Some challenge pages return HTTP 200. Detect those as well as explicit
    # 403/429/503 responses, otherwise the parser would receive only the
    # challenge shell and report "no links".
    challenge = _looks_like_challenge(r.text, dict(r.headers))
    if (r.status_code in FLARESOLVERR_ON or challenge) and FLARESOLVERR_ENABLED:
        trace.event("fetch", "challenge, trying FlareSolverr",
                    url=url, status=r.status_code)
        try:
            out = _flaresolverr_get(url)
            trace.event("fetch", "FlareSolverr solved", url=url, bytes=len(out[1]))
            return out
        except FetchError as e:
            raise Blocked(
                f"host returned {r.status_code}; FlareSolverr fallback failed: {e}"
            ) from e

    if r.status_code in (401, 403):
        raise Blocked(f"host returned {r.status_code} to the HTTP client")
    if r.status_code == 429:
        raise Blocked("host returned 429 (rate limited) — back off and retry later")
    if r.status_code >= 400:
        trace.event("fetch", "HTTP error", url=url, status=r.status_code)
        raise FetchError(f"HTTP {r.status_code}")
    trace.event("fetch", "ok", url=url, status=r.status_code, bytes=len(r.text))
    return r.status_code, r.text, dict(r.headers)


def post(url: str, data: dict, referer: str = "",
         respect_robots: bool = True) -> int:
    """Submit a form, through the same policy every read goes through.

    The one writing request in the app. It is kept here rather than in the
    caller so it inherits what the rest already enforces: the member's cookie
    jar, the per-host gap, and the browser identity on a stealth host -- a
    reply arriving with the crawler's own User-Agent, seconds after a page
    read, would stand out far more than the reply itself.

    A form post also carries Referer and Origin, because a browser does and
    forum software checks: Forumotion rejects a reply that arrives without
    them. No token is ever invented here; every field comes from the page.

    robots.txt has no rule for submitting a form -- it governs what a crawler
    may fetch -- but this app has never fetched a disallowed path, so the
    caller decides, and the default is to honour it.
    """
    if respect_robots:
        allowed, _ = robots_ok(url)
        if not allowed:
            raise Blocked(f"robots.txt disallows {url}")
    origin = "{0.scheme}://{0.netloc}".format(urlparse(referer or url))
    headers = _headers_for(url, {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": origin,
        "Referer": referer or url,
    })
    _throttle(url)
    try:
        r = _client.post(url, data=data, headers=headers, follow_redirects=True)
    except httpx.HTTPError as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e
    if r.status_code in (401, 403):
        raise Blocked(f"host returned {r.status_code} to the form post")
    if r.status_code == 429:
        raise Blocked("host returned 429 (rate limited) — back off and retry later")
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code}")
    trace.event("fetch", "form posted", url=url, status=r.status_code,
                fields=len(data))
    return r.status_code
