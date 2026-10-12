"""Find the next page of a forum listing or thread.

A thread worth crawling is rarely one page. The crawler was taking page 1 and
stopping, which on a 40-page release thread is most of the links left behind.

Three strategies, in order of trust:

  rel=next     the page telling us itself. Every forum family emits it.
  next link    a pager entry labelled "next" (Drupal's "next ›", class
               pager-next / pager__item--next, title "Go to next page").
  pager link   a numbered pager entry one higher than the current page.

The labelled link comes before the numbered guess because some sites count
pages from 0: on flacattack.net the button showing "2" is ?page=1, so looking
for "page one higher than this" from the first page jumped straight to the
third and left the second page unread.

Both are read from the page that was already fetched, so following a pager
costs one request per page and no guessing at URL shapes. Guessed ?page=N
patterns are not used: on several forum themes they return page 1 forever,
which turns a bounded crawl into a loop that looks like it is working.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup
import os

from .forum_parser import smf_id, smf_url

# Ceiling for "all pages". flacattack's happy-hardcore genre alone is 131.
MAX_PAGES = int(os.environ.get("CW_MAX_PAGES", "500"))

# Query keys that carry a page number across the supported forum families.
PAGE_KEYS = ("page", "p", "pagenum", "start", "st", "offset")

_PATH_PAGE = re.compile(r"/(?:page[-/]?|p)(\d+)(?:/|$)", re.I)

# Forumotion: /f31-slug is page 1 of board 31, /f31p20-slug the next page.
# The number is an OFFSET (topics or posts skipped), not a page number, and
# the board emits no rel=next, so neither strategy above finds it.
_FORUMOTION_PAGE = re.compile(r"^/([ft]\d+)(?:p(\d+))?-")


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


_NEXT_TEXT = re.compile(r"^(?:next(?:\s*page)?|siguiente|suivant|weiter|volgende|próxima|›|>|→)"
                        r"\s*[›»>→]?$", re.I)
_NEXT_CLASS = re.compile(r"(?:^|[-_ ])next(?:$|[-_ ])", re.I)


def _labelled_next(soup: BeautifulSoup, base: str) -> str | None:
    """A same-site link the page itself labels as "next" (not "last »")."""
    host = urlparse(base).netloc.lower()
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if urlparse(href).netloc.lower() != host:
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        classes = " ".join(a.get("class") or []) + " " + " ".join(
            (a.parent.get("class") or []) if a.parent is not None else [])
        title = (a.get("title") or a.get("aria-label") or "").lower()
        if (_NEXT_TEXT.match(text) or "next page" in title
                or (_NEXT_CLASS.search(classes) and not re.search(r"last|prev", text, re.I))):
            return href.split("#")[0]
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


def _forumotion_next(soup: BeautifulSoup, base: str) -> str | None:
    """The smallest offset past this one, among links to the same board/topic."""
    here = urlparse(base)
    m = _FORUMOTION_PAGE.match(here.path)
    if not m:
        return None
    ident, current = m.group(1), int(m.group(2) or 0)
    best = None
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        p = urlparse(href)
        if p.netloc.lower() != here.netloc.lower() or p.query:
            continue
        n = _FORUMOTION_PAGE.match(p.path)
        if not n or n.group(1) != ident or not n.group(2):
            continue
        offset = int(n.group(2))
        if offset > current and (best is None or offset < best[0]):
            best = (offset, p._replace(fragment="").geturl())
    return best[1] if best else None


def _smf_next(soup: BeautifulSoup, base: str) -> str | None:
    """The smallest offset past this one among links to the same SMF topic/board.

    SMF's <link rel="next"> on a topic is ;prev_next=next -- the NEXT TOPIC --
    so for SMF this answer is authoritative and rel=next is never consulted.
    Both spellings (?topic=N.S and /topic,N.S.html) count: the cached guest
    copy of a page uses the second.
    """
    here = smf_id(base)
    if not here:
        return None
    kind, ident, current = here
    best = None
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if urlparse(href).netloc.lower() != urlparse(base).netloc.lower():
            continue
        got = smf_id(href)
        if not got or got[0] != kind or got[1] != ident or got[2] <= current:
            continue
        if best is None or got[2] < best:
            best = got[2]
    return smf_url(base, kind, ident, best) if best is not None else None


def next_page(html: str, base_url: str) -> str | None:
    """URL of the page after this one, or None when this is the last."""
    soup = BeautifulSoup(html or "", "lxml")
    if smf_id(base_url):
        return _smf_next(soup, base_url)
    nxt = _rel_next(soup, base_url)
    if nxt and nxt.rstrip("/") != base_url.rstrip("/"):
        return nxt
    nxt = _forumotion_next(soup, base_url)
    if nxt:
        return nxt
    if _FORUMOTION_PAGE.match(urlparse(base_url).path):
        return None     # Forumotion's offsets are authoritative; its "Next" image can point backwards
    nxt = _labelled_next(soup, base_url)
    here, there = page_number(base_url), page_number(nxt) if nxt else None
    if (nxt and nxt.rstrip("/") != base_url.rstrip("/")
            and not (here is not None and there is not None and there <= here)):
        return nxt
    nxt = _pager_next(soup, base_url, page_number(base_url))
    if nxt and nxt.rstrip("/") != base_url.rstrip("/"):
        return nxt
    return None


def walk(start_url: str, get, max_pages: int = 5):
    """Yield (url, html) for up to max_pages, following the pager.

    max_pages <= 0 means "every page", bounded by MAX_PAGES so a pager that
    never ends still stops.

    `get` is the fetch function, so this inherits robots.txt, the per-host
    Crawl-delay and any challenge fallback rather than opening its own client.
    """
    url = start_url
    seen = {start_url.rstrip("/").lower()}
    for _ in range(MAX_PAGES if max_pages <= 0 else max_pages):
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
