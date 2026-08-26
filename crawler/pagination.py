"""Find the next page of a forum listing or thread.

A thread worth crawling is rarely one page. The crawler was taking page 1 and
stopping, which on a 40-page release thread is most of the links left behind.

Two strategies, in order of trust:

  rel=next     the page telling us itself. Every forum family emits it.
  pager link   a numbered pager entry one higher than the current page.

Both are read from the page that was already fetched, so following a pager
costs one request per page and no guessing at URL shapes. Guessed ?page=N
patterns are not used: on several forum themes they return page 1 forever,
which turns a bounded crawl into a loop that looks like it is working.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup

# Query keys that carry a page number across the supported forum families.
PAGE_KEYS = ("page", "p", "pagenum", "start", "st", "offset")

_PATH_PAGE = re.compile(r"/(?:page[-/]?|p)(\d+)(?:/|$)", re.I)


def page_number(url: str) -> int | None:
    """The page a URL refers to, from its query string or its path."""
    p = urlparse(url or "")
    qs = parse_qs(p.query)
    for key in PAGE_KEYS:
        for raw in qs.get(key, []):
            if raw.isdigit():
                n = int(raw)
                # start/offset count posts, not pages; treat any non-zero as
                # "later than the first page" without inventing a page number.
                return n if key not in ("start", "offset") else (2 if n else 1)
    m = _PATH_PAGE.search(p.path)
    if m:
        return int(m.group(1))
    return None


def _rel_next(soup: BeautifulSoup, base: str) -> str | None:
    for tag in soup.find_all(("link", "a"), rel=True):
        rel = tag.get("rel") or []
        rel = [rel] if isinstance(rel, str) else rel
        if any(str(r).lower() == "next" for r in rel) and tag.get("href"):
            return urljoin(base, tag["href"])
    return None


def _pager_next(soup: BeautifulSoup, base: str, current: int | None) -> str | None:
    if current is None:
        current = 1
    best = None
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if urlparse(href).netloc.lower() != urlparse(base).netloc.lower():
            continue
        n = page_number(href)
        if n is None or n != current + 1:
            continue
        # Prefer a link whose text is the page number itself over a themed
        # arrow that may point at the last page rather than the next one.
        text = " ".join(a.get_text(" ", strip=True).split())
        if text == str(n):
            return href
        best = best or href
    return best


def next_page(html: str, base_url: str) -> str | None:
    """URL of the page after this one, or None when this is the last."""
    soup = BeautifulSoup(html or "", "lxml")
    nxt = _rel_next(soup, base_url)
    if nxt and nxt.rstrip("/") != base_url.rstrip("/"):
        return nxt
    nxt = _pager_next(soup, base_url, page_number(base_url))
    if nxt and nxt.rstrip("/") != base_url.rstrip("/"):
        return nxt
    return None


def walk(start_url: str, get, max_pages: int = 5):
    """Yield (url, html) for up to max_pages, following the pager.

    `get` is the fetch function, so this inherits robots.txt, the per-host
    Crawl-delay and any challenge fallback rather than opening its own client.
    """
    url = start_url
    seen = {start_url.rstrip("/").lower()}
    for _ in range(max(1, max_pages)):
        status, body, _headers = get(url)
        if status == 304 or not body:
            return
        yield url, body
        nxt = next_page(body, url)
        if not nxt:
            return
        key = nxt.rstrip("/").lower()
        if key in seen:
            return
        seen.add(key)
        url = nxt
