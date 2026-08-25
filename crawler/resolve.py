"""Resolve Drupal listing pages to real file-host URLs for JDownloader."""
import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urlparse, urljoin
from bs4 import BeautifulSoup

from . import fetch, links as linkmod

DOWNLOADABLE = {"build"}
FOLLOWABLE = ("/download/", "/node/", "/release", "/album", "/threads/", "/topic")
FURNITURE = (
    "/genre/", "/tags/", "/taxonomy/", "/user", "/comment", "/search",
    "/rss", "/feed", "/collections", "/requests", "/privacy", "/contact",
    "/admin", "/misc/", "/sites/", "/modules/", "/themes/", "/files/",
)
FURNITURE_EXACT = ("", "/", "/reuploads", "/collections", "/requests")
WRAPPER_TOKENS = (
    "flakattak", "flakattack", "redirect", "mirror", "download", "filehost",
    "rapidgator", "nitroflare", "ddownload", "katfile", "turbobit", "hitfile",
    "uploadgig", "fikper",
)
TARGET_KEYS = {
    "url", "u", "target", "dest", "destination", "redirect", "redirect_url",
    "link", "download", "file", "href",
}


def is_downloadable(link: dict) -> bool:
    return bool(link.get("downloadable")) or link.get("bucket") in DOWNLOADABLE


def candidates(items, base_url: str, limit: int = 40) -> list[dict]:
    host = urlparse(base_url).netloc.lower()
    here = urlparse(base_url).path.rstrip("/").lower()

    def on_site(l):
        return l.get("host", "").lower() == host.removeprefix("www.") or \
               l.get("host", "").lower().removeprefix("www.") == host.removeprefix("www.")

    def collect(test):
        out, seen = [], set()
        for it in items:
            name = getattr(it, "name", None) or it.get("name", "")
            for l in getattr(it, "links", None) or it.get("links", []):
                u = l["url"]
                if not on_site(l) or not test(urlparse(u).path.lower()):
                    continue
                key = u.rstrip("/").lower()
                if key in seen or (here and key.endswith(here)):
                    continue
                seen.add(key)
                out.append({"url": u, "title": name})
                if len(out) >= limit:
                    return out
        return out

    hits = collect(lambda path: any(p in path for p in FOLLOWABLE))
    if hits:
        return hits

    def plausible(path):
        if path.rstrip("/") in FURNITURE_EXACT:
            return False
        if any(p in path for p in FURNITURE):
            return False
        if re.search(r"\.(?:css|js|png|jpe?g|gif|svg|ico|woff2?|xml|zip|rar)$", path):
            return False
        return len(path.strip("/")) > 3

    return collect(plausible)


def _hrefs_and_text(body: str, base: str) -> tuple[list[str], list[str]]:
    soup = BeautifulSoup(body or "", "lxml")
    hrefs = []
    tokens = []
    for a in soup.find_all("a", href=True):
        hrefs.append(urljoin(base, a["href"]))
        tokens.append(a.get_text(" ", strip=True))
        for attr in ("data-href", "data-url", "data-link", "data-download-url", "onclick"):
            value = a.get(attr)
            if value:
                hrefs.append(urljoin(base, str(value)))
                tokens.append(str(value))
    for el in soup.find_all(True):
        for attr in ("data-href", "data-url", "data-link", "data-download-url", "value"):
            value = el.get(attr)
            if isinstance(value, str) and value:
                hrefs.append(urljoin(base, value))
    return hrefs, tokens


def _direct(body: str, base: str) -> list[dict]:
    hrefs, _ = _hrefs_and_text(body, base)
    found = linkmod.extract(body, hrefs)
    return _dedupe([l for l in found if is_downloadable(l)])


def _looks_like_wrapper(url: str, text: str = "") -> bool:
    p = urlparse(url)
    host = p.netloc.lower()
    hay = f"{host} {p.path} {p.query} {text}".lower()
    if any(k in hay for k in WRAPPER_TOKENS):
        return True
    try:
        qs = parse_qs(p.query)
        return any(k.lower() in TARGET_KEYS for k in qs)
    except Exception:
        return False


def _wrapper_candidates(body: str, base: str) -> list[str]:
    hrefs, texts = _hrefs_and_text(body, base)
    raw = linkmod.extract(body, hrefs)
    out, seen = [], set()
    for idx, l in enumerate(raw):
        if is_downloadable(l):
            continue
        text = texts[idx] if idx < len(texts) else ""
        if not _looks_like_wrapper(l["url"], text):
            continue
        key = l["url"].rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            out.append(l["url"])
    return out


def _resolve_wrapper(start_url: str, max_hops: int = 2) -> list[dict]:
    """Resolve a wrapper/redirect page to direct hosts, with a hard hop limit."""
    queue = [(start_url, 0)]
    seen = {start_url.rstrip("/").lower()}
    found = []
    while queue:
        current, depth = queue.pop(0)
        try:
            final = fetch.resolve_final_url(current)
            if final and final.rstrip("/").lower() != current.rstrip("/").lower():
                classified = linkmod.classify(final)
                if is_downloadable(classified):
                    found.append(classified)
                    continue
        except Exception:
            pass

        if depth >= max_hops:
            continue
        try:
            status, body, _ = fetch.get(current, conditional=False)
        except Exception:
            continue
        if status == 304 or not body:
            continue

        direct = _direct(body, current)
        if direct:
            found.extend(direct)
            continue

        for nxt in _wrapper_candidates(body, current):
            key = nxt.rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                queue.append((nxt, depth + 1))
    return _dedupe(found)


def page_links(url: str) -> list[dict]:
    status, body, _ = fetch.get(url, conditional=False)
    if status == 304 or not body:
        return []

    direct = _direct(body, url)
    if direct:
        return direct

    # If the release page only exposes wrappers, follow those wrappers. This
    # is deliberately bounded to two hops and only starts from links that look
    # like download/redirect/file-host wrappers.
    return _resolve_wrapper_links(body, url)


def _resolve_wrapper_links(body: str, base: str) -> list[dict]:
    hrefs, texts = _hrefs_and_text(body, base)
    raw = linkmod.extract(body, hrefs)
    candidates = []
    for idx, l in enumerate(raw):
        if is_downloadable(l):
            continue
        text = texts[idx] if idx < len(texts) else ""
        if _looks_like_wrapper(l["url"], text):
            candidates.append(l["url"])

    out = []
    for candidate in candidates:
        out.extend(_resolve_wrapper(candidate, max_hops=2))
    return _dedupe(out)


def resolve(items, base_url: str, limit: int = 40, on_progress=None) -> dict:
    already = []
    for it in items:
        for l in getattr(it, "links", None) or it.get("links", []):
            if is_downloadable(l):
                already.append(l)
    if already:
        return {
            "hopped": False,
            "releases": [],
            "links": _dedupe(already),
            "note": "page already carried downloadable links; no second hop needed",
        }

    todo = candidates(items, base_url, limit)
    releases = [None] * len(todo)
    flat, errors = [], 0
    workers = max(1, min(len(todo) or 1, int(os.getenv("CRAWLER_RESOLVE_CONCURRENCY", "6"))))

    def one(c):
        try:
            found = page_links(c["url"])
            return {"url": c["url"], "title": c["title"],
                    "links": found, "count": len(found)}
        except Exception as e:
            return {"url": c["url"], "title": c["title"], "links": [],
                    "count": 0, "error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="resolve") as pool:
        futures = {pool.submit(one, c): i for i, c in enumerate(todo)}
        done = 0
        for future in as_completed(futures):
            idx = futures[future]
            rel = future.result()
            releases[idx] = rel
            flat.extend(rel["links"])
            errors += bool(rel.get("error"))
            done += 1
            if on_progress:
                on_progress(done, len(todo), rel["title"] or rel["url"])

    releases = [r for r in releases if r is not None]

    return {
        "hopped": True,
        "followed": len(todo),
        "errors": errors,
        "releases": releases,
        "links": _dedupe(flat),
    }


def _dedupe(found: list[dict]) -> list[dict]:
    seen, out = set(), []
    for l in found:
        key = l["url"].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(l)
    return out
