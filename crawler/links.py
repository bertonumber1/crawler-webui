"""Outbound-link extraction and classification for release pages."""
import html as html_lib
import re
from urllib.parse import urlparse, unquote

BUCKETS = [
    ("source", r"(github\.com|gitlab\.com|bitbucket\.org|git\.sr\.ht|codeberg\.org)"),
    ("build",  r"(androidfilehost\.com|sourceforge\.net|mega\.nz|mega\.co\.nz|"
               r"mediafire\.com|drive\.google\.com|onedrive\.live\.com|dropbox\.com|"
               r"pixeldrain\.com|gofile\.io|1fichier\.com|rapidgator\.net|"
               r"nitroflare\.com|ddownload\.com|katfile\.com|turbobit\.net|"
               r"hitfile\.net|uploadgig\.com|fikper\.com|wdfiles\.ru|"
               r"cloud\.mail\.ru|disk\.yandex\.(?:ru|com)|files\.fm)"),
    ("video",  r"(youtube\.com|youtu\.be|vimeo\.com|odysee\.com|streamable\.com)"),
    ("forum",  r"(reddit\.com|t\.me)"),
]
_COMPILED = [(b, re.compile(p, re.I)) for b, p in BUCKETS]

PREMIUM = re.compile(
    r"(rapidgator\.net|nitroflare\.com|ddownload\.com|katfile\.com|turbobit\.net|"
    r"hitfile\.net|uploadgig\.com|fikper\.com)", re.I
)

DOWNLOAD_LABELS = {
    "rapidgator.net": "Rapidgator",
    "nitroflare.com": "Nitroflare",
    "ddownload.com": "DDownload",
    "katfile.com": "Katfile",
    "turbobit.net": "Turbobit",
    "hitfile.net": "HitFile",
    "uploadgig.com": "UploadGig",
    "fikper.com": "Fikper",
    "1fichier.com": "1fichier",
    "mega.nz": "MEGA",
    "mediafire.com": "MediaFire",
    "pixeldrain.com": "Pixeldrain",
    "gofile.io": "GoFile",
}

# Hosts that represent an actual downloadable service rather than a wrapper.
DOWNLOAD_HOSTS = set(DOWNLOAD_LABELS)

# Accept URLs in ordinary text, HTML attributes and encoded HTML.
URL_RE = re.compile(r"""(?i)(?:https?://|//)[^\s\"'<>`\]}]+""")


def _balance_brackets(url: str) -> str:
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1]
    return url


def _normalise(url: str) -> str | None:
    if not url:
        return None

    url = html_lib.unescape(str(url)).strip()
    url = url.replace("\\/", "/")
    url = url.strip(" \t\r\n'\"<>[]{}.,;")

    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        return None

    url = _balance_brackets(url.rstrip(".,;"))
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
    except Exception:
        return None
    return url


def host_only(url: str) -> str:
    return urlparse(url).netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")


def is_download_host(url: str) -> bool:
    return host_only(url) in DOWNLOAD_HOSTS


def classify(url: str) -> dict:
    host = host_only(url)
    bucket = "other"
    for name, rx in _COMPILED:
        if rx.search(host):
            bucket = name
            break
    label = DOWNLOAD_LABELS.get(host)
    return {
        "url": url,
        "host": host,
        "bucket": bucket,
        "label": label or bucket,
        "premium": bool(PREMIUM.search(host)),
        "downloadable": host in DOWNLOAD_HOSTS,
    }


def extract(html_or_text: str, hrefs=None) -> list[dict]:
    """Collect and normalise links from hrefs plus visible/embedded URLs.

    The caller can provide absolute hrefs extracted by BeautifulSoup. We also
    scan the serialised node because Drupal modules occasionally expose links
    through data attributes or encoded markup.

    Visible anchor text can contain deliberately shortened display URLs such
    as ``https://rapidgator.net/file/abc...part1.rar`` while the href contains
    the real URL. Those shortened display values are not valid download URLs
    and must never be emitted alongside the real href.
    """
    seen, out = set(), []
    candidates = list(hrefs or [])
    raw = html_lib.unescape(html_or_text or "")
    candidates.extend(URL_RE.findall(raw))

    for raw_url in list(candidates):
        decoded = unquote(raw_url)
        if decoded != raw_url:
            candidates.append(decoded)

    for candidate in candidates:
        u = _normalise(candidate)
        if not u:
            continue
        # Reject truncated/ellipsised display URLs. The actual href, when
        # present, is retained and will already be in candidates.
        if "..." in u:
            continue
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
