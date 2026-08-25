"""Second hop: turn a listing page into links JDownloader can actually take.

A tag or genre page holds no download links at all. It holds links to release
pages, and the file hosts only appear one level deeper. Handing JD the release
page URL achieves nothing -- JD has no idea what a Drupal node is -- so the
crawler has to make that hop itself.

    /tags/nightmare-rotterdam        19 release links, 0 downloadable
      -> /download/va-nightmare-...  1 Rapidgator link          <- what JD wants

Everything goes through fetch.get(), so the hop inherits robots.txt, the
per-host Crawl-delay and the FlareSolverr fallback rather than reimplementing
them. A page already carrying downloadable links is returned as-is: the hop is
skipped, not performed twice.
"""
import re
from urllib.parse import urlparse

from . import fetch, links as linkmod

# Buckets a downloader can do something with. "source" (GitHub) and "forum"
# are real links but they are not files, so they never trigger a hop and never
# reach a crawljob.
DOWNLOADABLE = {"build"}

# Paths that look like a release/article node rather than site furniture.
FOLLOWABLE = ("/download/", "/node/", "/release", "/album", "/threads/", "/topic")

# Drupal site furniture. These are listings, taxonomy and account pages: real
# links, but never a release. Used only by the fallback below, where there is
# no positive signal to match on and the safer test is what to rule out.
FURNITURE = (
    "/genre/", "/tags/", "/taxonomy/", "/user", "/comment", "/search",
    "/rss", "/feed", "/collections", "/requests", "/privacy", "/contact",
    "/admin", "/misc/", "/sites/", "/modules/", "/themes/", "/files/",
)
FURNITURE_EXACT = ("", "/", "/reuploads", "/collections", "/requests")


def is_downloadable(link: dict) -> bool:
    return link.get("bucket") in DOWNLOADABLE


def candidates(items, base_url: str, limit: int = 40) -> list[dict]:
    """Release-page links worth a second request, in page order.

    Restricted to the site being crawled. Following off-site links would turn
    a listing page into an open redirect crawler.
    """
    host = urlparse(base_url).netloc.lower()
    here = urlparse(base_url).path.rstrip("/").lower()

    def on_site(l):
        return l.get("host", "").lower() == host

    def collect(test):
        out, seen = [], set()
        for it in items:
            name = getattr(it, "name", None) or it.get("name", "")
            for l in getattr(it, "links", None) or it.get("links", []):
                u = l["url"]
                if not on_site(l) or not test(urlparse(u).path.lower()):
                    continue
                key = u.rstrip("/").lower()
                if key in seen or key.endswith(here):
                    continue
                seen.add(key)
                out.append({"url": u, "title": name})
                if len(out) >= limit:
                    return out
        return out

    hits = collect(lambda path: any(p in path for p in FOLLOWABLE))
    if hits:
        return hits

    # No recognised node path. Rather than give up on a Drupal site that names
    # its nodes /va-some-album-1994, keep same-host links that are not site
    # furniture and not an asset. This is the looser of the two tests, so it
    # runs only when the specific one found nothing.
    def plausible(path):
        if path.rstrip("/") in FURNITURE_EXACT:
            return False
        if any(p in path for p in FURNITURE):
            return False
        if re.search(r"\.(?:css|js|png|jpe?g|gif|svg|ico|woff2?|xml|zip|rar)$", path):
            return False
        return len(path.strip("/")) > 3

    return collect(plausible)


def page_links(url: str) -> list[dict]:
    """Downloadable links on one release page."""
    status, body, headers = fetch.get(url, conditional=False)
    if status == 304 or not body:
        return []
    return [l for l in linkmod.extract(body) if is_downloadable(l)]


def resolve(items, base_url: str, limit: int = 40, on_progress=None) -> dict:
    """Follow release pages and collect what JD can take.

    Returns one entry per release, including the ones that resolved to nothing
    -- a release page with no hosts left on it is a fact worth showing, not a
    row to hide.
    """
    already = []
    for it in items:
        for l in getattr(it, "links", None) or it.get("links", []):
            if is_downloadable(l):
                already.append(l)
    if already:
        return {"hopped": False, "releases": [], "links": _dedupe(already),
                "note": "page already carried downloadable links; no second hop needed"}

    todo = candidates(items, base_url, limit)
    releases, flat, errors = [], [], 0
    for i, c in enumerate(todo, 1):
        try:
            found = page_links(c["url"])
            releases.append({"url": c["url"], "title": c["title"],
                             "links": found, "count": len(found)})
            flat.extend(found)
        except Exception as e:
            errors += 1
            releases.append({"url": c["url"], "title": c["title"], "links": [],
                             "count": 0, "error": f"{type(e).__name__}: {e}"})
        if on_progress:
            on_progress(i, len(todo), c["title"] or c["url"])

    return {"hopped": True, "followed": len(todo), "errors": errors,
            "releases": releases, "links": _dedupe(flat)}


def _dedupe(found: list[dict]) -> list[dict]:
    seen, out = set(), []
    for l in found:
        key = l["url"].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(l)
    return out
