"""Software-aware forum extraction.

The detector tells us which forum family we are looking at; this module turns
that signal into a conservative extraction strategy. It never assumes a
single theme: each strategy has multiple selectors and then falls back to
link evidence.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag

from . import links as linkmod
from .forum_detector import Detection, best_match
from .models import Item


STRATEGIES = {
    "vBulletin": (
        "[id^='post_message_']",
        ".post_message",
        ".postbody",
        ".postcontent",
        ".postbit",
        ".postcontainer",
        "li[id^='thread_']",
        ".threadbit",
        ".discussion",
        ".tborder",
        "#posts",
    ),
    "XenForo": (
        ".structItem", ".structItemContainer", ".message--post", ".block--messages",
        ".message-cell--main", ".p-body-content",
    ),
    "phpBB": (
        ".topiclist", ".topiclist_topics", ".post", ".postbody", ".forabg",
        ".forumbg",
    ),
    "Invision Community": (
        ".ipsDataItem", ".ipsComment", ".ipsType_richText", ".ipsLayout_container",
        ".cTopic", ".ipsAreaBackground_light",
    ),
}

FURNITURE = {
    "nav", "header", "footer", "aside", "script", "style", "noscript",
    "template", "svg", "form",
}


def _title(node: Tag, fallback: str) -> str:
    for selector in (
        "h1", "h2", "h3", ".structItem-title", ".p-title-value",
        ".ipsType_pageTitle", ".topic-title", ".topictitle", ".threadtitle",
    ):
        x = node.select_one(selector)
        if x:
            value = " ".join(x.get_text(" ", strip=True).split())
            if len(value) >= 3:
                return value
    for a in node.find_all("a", href=True):
        value = " ".join(a.get_text(" ", strip=True).split())
        if len(value) >= 5 and not value.lower() in {"reply", "quote", "edit", "login"}:
            return value
    return fallback


def _links(node: Tag, base: str) -> list[dict]:
    hrefs = []
    for a in node.find_all("a", href=True):
        hrefs.append(urljoin(base, a["href"]))
        for attr in ("data-href", "data-url", "data-link", "data-download-url"):
            value = a.get(attr)
            if value:
                hrefs.append(urljoin(base, str(value)))
    for el in node.find_all(True):
        for attr in ("data-href", "data-url", "data-link", "data-download-url"):
            value = el.get(attr)
            if isinstance(value, str) and value:
                hrefs.append(urljoin(base, value))
    return linkmod.extract(str(node), hrefs)


def _same_host(url: str, base: str) -> bool:
    a = urlparse(url).netloc.lower().removeprefix("www.")
    b = urlparse(base).netloc.lower().removeprefix("www.")
    return bool(a and b and a == b)


def _candidate_nodes(soup: BeautifulSoup, software: str) -> list[Tag]:
    nodes = []
    seen = set()
    for selector in STRATEGIES.get(software, ()):
        for node in soup.select(selector):
            if id(node) not in seen:
                seen.add(id(node))
                nodes.append(node)
    # Outer nodes are preferable to tiny nested wrappers.
    kept = []
    for node in nodes:
        if any(node is not other and node in other.parents for other in nodes):
            continue
        kept.append(node)
    return kept


def parse(html: str, base_url: str, *, detection: Detection | None = None,
          limit: int = 50) -> tuple[list[Item], Detection | None]:
    """Parse a forum page into release/thread candidates and return detection."""
    soup = BeautifulSoup(html or "", "lxml")
    detection = detection or best_match(html)
    if not detection or detection.software not in STRATEGIES:
        return [], detection

    for tag in soup.find_all(FURNITURE):
        tag.decompose()

    page_title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else base_url).split())
    nodes = _candidate_nodes(soup, detection.software)
    if not nodes:
        main = soup.select_one("main, [role='main'], #content") or soup.body or soup
        nodes = [main]

    out, seen = [], set()
    for node in nodes:
        links = _links(node, base_url)
        # A forum candidate is useful when it has a download host, a wrapper,
        # or a meaningful thread URL. We deliberately don't emit every UI box.
        useful = [l for l in links if l.get("downloadable")]
        thread_links = [
            l for l in links
            if _same_host(l["url"], base_url)
            and any(x in urlparse(l["url"]).path.lower() for x in ("/thread", "/threads/", "/showthread", "/viewtopic", "/topic/") )
        ]
        if not useful and not thread_links:
            continue

        title = _title(node, page_title)
        key = (title.lower(), tuple(sorted(x["url"].rstrip("/").lower() for x in links)))
        if key in seen:
            continue
        seen.add(key)
        out.append(Item(
            id=base_url + "#" + title,
            name=title,
            url=base_url,
            kind="release",
            summary=" ".join(node.get_text(" ", strip=True).split())[:400],
            links=links,
            store="forum",
        ))
        if len(out) >= limit:
            break

    # If the forum page has download links but the theme defeated all known
    # selectors, retain a single structured main-content result rather than
    # silently returning zero.
    if not out:
        main = soup.select_one("main, [role='main'], #content") or soup.body or soup
        links = _links(main, base_url)
        if links:
            out.append(Item(
                id=base_url,
                name=page_title,
                url=base_url,
                kind="release",
                summary=" ".join(main.get_text(" ", strip=True).split())[:400],
                links=links,
                store="forum",
            ))

    return out, detection
