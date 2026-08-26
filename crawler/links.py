"""Outbound-link extraction and classification for release pages."""
import html as html_lib
import re
from urllib.parse import urlparse, unquote

from . import hosts

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

# Labels for every host the classifier knows, enabled or not -- the resolver
# must still recognise a switched-off host as a file host rather than treating
# its page as a wrapper worth following.
DOWNLOAD_LABELS = dict(hosts.ALL_LABELS)

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
    """True when the URL is on a host we are currently harvesting."""
    return hosts.is_enabled(url)


def is_known_file_host(url: str) -> bool:
    """True for any recognised file host, including ones switched off.

    The resolver needs this: a Nitroflare link is a dead end, not a wrapper to
    follow, even on a day when Nitroflare is not being harvested.
    """
    return host_only(url) in hosts.ALL_DOMAINS


_RAPIDGATOR_FILE_RE = re.compile(
    r"^https?://(?:www\.)?rapidgator\.net/file/([A-Za-z0-9]+)(?:/([^?#]*))?",
    re.I,
)


def rapidgator_file_id(url: str) -> str | None:
    """Return the Rapidgator file ID, or None for malformed/truncated URLs."""
    m = _RAPIDGATOR_FILE_RE.match(url or "")
    return m.group(1) if m else None


def canonical_download_key(url: str) -> str:
    """Return a stable identity for a downloadable file.

    Two URLs for the same file -- /file/<id> and /file/<id>/<name>.html --
    must reduce to one key, or the same download is queued twice.
    """
    identity, _ = hosts.reduce(url)
    return identity or url.rstrip("/").lower()


def canonical_download_url(url: str) -> str | None:
    """Return a usable canonical download URL, or None to reject it.

    A truncated or display-only form is rejected here rather than passed on to
    fail later in the downloader.
    """
    _, canonical = hosts.reduce(url)
    return canonical


def dedupe_download_links(links: list[dict]) -> list[dict]:
    """Keep only valid target downloads and remove duplicate representations."""
    out = []
    seen = set()

    for link in links:
        url = link.get("url", "")
        if not is_download_host(url):
            continue

        canonical = canonical_download_url(url)
        if not canonical:
            continue

        key = canonical_download_key(canonical)
        if key in seen:
            continue

        seen.add(key)

        item = dict(link)
        item["url"] = canonical
        item["host"] = host_only(canonical)
        item["label"] = hosts.label(canonical)
        item["premium"] = bool(PREMIUM.search(item["host"]))
        item["downloadable"] = True
        out.append(item)

    return out


def classify(url: str) -> dict:
    host = host_only(url)
    bucket = "other"
    for name, rx in _COMPILED:
        if rx.search(host):
            bucket = name
            break
    return {
        "url": url,
        "host": host,
        "bucket": bucket,
        "label": DOWNLOAD_LABELS.get(host) or bucket,
        "premium": bool(PREMIUM.search(host)),
        "downloadable": hosts.is_enabled(url),
        # Recognised file host, whether or not it is switched on. Lets the
        # resolver stop rather than follow it as a wrapper.
        "file_host": host in hosts.ALL_DOMAINS,
    }


def extract(html_or_text: str, hrefs=None) -> list[dict]:
    """Collect and normalise links.

    The caller can provide absolute hrefs extracted by BeautifulSoup. We also
    scan the serialised node because Drupal modules occasionally expose links
    through data attributes or encoded markup.

    Explicit hrefs are authoritative. Two guards keep shortened display URLs
    out, because forums print things like
    ``https://rapidgator.net/file/abc...part1.rar`` as the visible text of a
    link whose href is the real URL:

      * anchor display text is never scanned -- the href is already collected
        and is the truthful version;
      * any URL still carrying ``...`` is dropped wherever it came from.

    The second guard is not redundant. A truncated Rapidgator URL does not
    fail loudly: ``/file/abc...part1.rar`` parses as file id ``abc``, which is
    a perfectly plausible identity for a file that does not exist, and it
    would be written into the sent-history and silently suppress the real
    file for good.
    """
    seen, out = set(), []
    candidates = list(hrefs or [])

    raw = html_lib.unescape(html_or_text or "")

    if "<" in raw and ">" in raw:
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(raw, "lxml")

            # Scan textual URLs, but never anchor display text. The actual
            # anchor hrefs are already present in `hrefs` and are authoritative.
            for node in soup.find_all(string=True):
                parent = getattr(node, "parent", None)
                if parent is not None and parent.name == "a":
                    continue
                candidates.extend(URL_RE.findall(str(node)))

            # Keep URLs embedded in JSON-LD and other script/template data.
            for tag in soup.find_all(["script", "style", "template"]):
                candidates.extend(
                    URL_RE.findall(
                        tag.string or tag.get_text("", strip=False)
                    )
                )

            # Keep URLs stored in data-* and other attributes, except href.
            for tag in soup.find_all(True):
                for name, value in tag.attrs.items():
                    if name.lower() == "href":
                        continue

                    values = value if isinstance(value, list) else [value]

                    for item in values:
                        if isinstance(item, str):
                            candidates.extend(URL_RE.findall(item))

        except Exception:
            # Do not let malformed HTML break extraction. Explicit hrefs
            # collected by the caller are still processed below.
            pass
    else:
        # Plain text input.
        candidates.extend(URL_RE.findall(raw))

    for raw_url in list(candidates):
        decoded = unquote(raw_url)
        if decoded != raw_url:
            candidates.append(decoded)

    for candidate in candidates:
        u = _normalise(candidate)
        if not u:
            continue
        # Reject truncated/ellipsised display URLs. The real href, when
        # present, is already in candidates and survives.
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
