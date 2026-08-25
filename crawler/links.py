"""Outbound-link extraction and classification for release pages."""
import html as html_lib
import re
from urllib.parse import urlparse, unquote

BUCKETS = [
    ("source", r"(github\.com|gitlab\.com|bitbucket\.org|git\.sr\.ht|codeberg\.org)"),
    ("build",  r"(androidfilehost\.com|sourceforge\.net|mega\.nz|mega\.co\.nz|"
               r"mediafire\.com|drive\.google\.com|onedrive\.live\.com|dropbox\.com|"
               r"pixeldrain\.com|gofile\.io|1fichier\.com|rapidgator\.net|"
               r"nitroflare\.com|ddownload\.com|katfile\.com|turbobit\.net)"),
    ("video",  r"(youtube\.com|youtu\.be|vimeo\.com|odysee\.com|streamable\.com)"),
    ("forum",  r"(xda-developers\.com|forum\.xda-developers\.com|reddit\.com|t\.me)"),
]
_COMPILED = [(b, re.compile(p, re.I)) for b, p in BUCKETS]

PREMIUM = re.compile(
    r"(rapidgator\.net|nitroflare\.com|ddownload\.com|katfile\.com|turbobit\.net|"
    r"hitfile\.net|uploadgig\.com|fikper\.com)", re.I)

# Accept URLs in ordinary text, HTML attributes and encoded HTML.
URL_RE = re.compile(
    r"""(?i)(?:https?://|//)[^\s"'<>`\]\)}]+"""
)


def _normalise(url: str) -> str | None:
    if not url:
        return None

    url = html_lib.unescape(str(url)).strip()
    url = url.replace("\\/", "/")
    url = url.strip(" \t\r\n'\"<>[](){}.,;")

    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        return None

    # Remove common trailing HTML punctuation without damaging query strings.
    url = url.rstrip(".,;")
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
    except Exception:
        return None
    return url


def classify(url: str) -> dict:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    bucket = "other"
    for name, rx in _COMPILED:
        if rx.search(host):
            bucket = name
            break
    return {"url": url, "host": host, "bucket": bucket,
            "premium": bool(PREMIUM.search(host))}


def extract(html_or_text: str, hrefs=None) -> list[dict]:
    """Collect and normalise links from hrefs plus visible/embedded URLs.

    The caller can provide absolute hrefs extracted by BeautifulSoup. We also
    scan the serialised node because Drupal modules occasionally expose links
    through data attributes or encoded markup.
    """
    seen, out = set(), []

    candidates = list(hrefs or [])
    raw = html_lib.unescape(html_or_text or "")
    candidates.extend(URL_RE.findall(raw))

    # Decode HTML entities and one level of percent encoding for cases where a
    # module has wrapped a target URL inside a redirect parameter.
    for raw_url in list(candidates):
        decoded = unquote(raw_url)
        if decoded != raw_url:
            candidates.append(decoded)

    for candidate in candidates:
        u = _normalise(candidate)
        if not u:
            continue
        # Deduplicate case-insensitively on scheme/host while preserving the
        # original query/path, since different query strings can be meaningful.
        key = u.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(classify(u))
    return out


def summarise(links: list[dict]) -> dict:
    counts = {}
    for l in links:
        counts[l["bucket"]] = counts.get(l["bucket"], 0) + 1
    return counts
