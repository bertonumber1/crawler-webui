"""Drupal-first web crawler provider.

The target sites are Drupal release/catalogue sites. The provider deliberately
treats HTML as the source of truth: feeds are only a fallback because Drupal
feeds often omit the actual download links present on a release node.
"""
from urllib.parse import urljoin, urlparse
from lxml import etree
from bs4 import BeautifulSoup, Tag

from .. import fetch, links as linkmod
from ..models import Item
from .base import register, ProviderError

FEED_TYPES = (
    "application/rss+xml", "application/atom+xml",
    "application/xml", "text/xml"
)

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "c": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _text(node, *paths):
    for p in paths:
        try:
            found = node.find(p, NS) if ":" in p else node.find(p)
        except SyntaxError:
            continue
        if found is None:
            continue
        if (found.text or "").strip():
            return found.text.strip()
        if found.get("href"):
            return found.get("href").strip()
    return ""


def _clean_title(text: str) -> str:
    return " ".join((text or "").split())


class Web:
    name = "web"

    def _is_feed(self, body, headers) -> bool:
        ct = (headers.get("content-type") or "").lower()
        if any(t in ct for t in ("rss", "atom", "xml")):
            return True
        head = body.lstrip()[:500].lower()
        return head.startswith("<?xml") or "<rss" in head or "<feed" in head

    def discover_feed(self, url, html) -> str | None:
        soup = BeautifulSoup(html, "lxml")
        for ln in soup.find_all("link", rel=lambda v: v and "alternate" in v):
            typ = (ln.get("type") or "").lower()
            if typ in FEED_TYPES and ln.get("href"):
                return urljoin(url, ln["href"])
        return None

    def _parse_feed(self, body, base) -> list[Item]:
        try:
            root = etree.fromstring(
                body.encode("utf-8", "replace"),
                etree.XMLParser(recover=True, resolve_entities=False),
            )
        except Exception as e:
            raise ProviderError(f"feed did not parse: {e}")
        if root is None:
            raise ProviderError("feed was empty")

        out = []
        nodes = root.findall(".//item") or root.findall(".//a:entry", NS)
        for n in nodes:
            link = _text(n, "link", "a:link") or ""
            if not link:
                le = n.find("a:link", NS)
                link = le.get("href", "") if le is not None else ""
            body_html = _text(
                n, "c:encoded", "description", "a:content", "a:summary"
            ) or ""
            soup = BeautifulSoup(body_html, "lxml")
            hrefs = [urljoin(base, a["href"]) for a in soup.find_all("a", href=True)]
            links = linkmod.extract(body_html, hrefs)
            out.append(Item(
                id=_text(n, "guid", "a:id") or link,
                name=_text(n, "title", "a:title"),
                url=urljoin(base, link),
                author=_text(n, "dc:creator", "author", "a:author/a:name"),
                published=_text(n, "pubDate", "a:published", "a:updated"),
                kind="release",
                summary=soup.get_text(" ", strip=True)[:400],
                links=links,
                store=self.name,
            ))
        return out

    # ----------------------------------------------------------- Drupal HTML

    @staticmethod
    def _node_candidates(soup: BeautifulSoup) -> list[Tag]:
        """Find likely Drupal release/listing units, from specific to broad."""
        selectors = [
            "article.node",
            "article[class*='node--']",
            ".node--view-mode-full",
            ".node--type-release",
            ".node--type-album",
            ".node--type-record",
            ".node--type-post",
            ".views-row",
            ".view-content > .node",
            ".view-content > article",
            ".node__content",
            ".field--name-body",
            ".field--name-field-body",
            ".field--name-field-description",
        ]
        found = []
        seen = set()
        for selector in selectors:
            for node in soup.select(selector):
                if id(node) not in seen:
                    found.append(node)
                    seen.add(id(node))

        # Prefer an outer article/node over its nested fields.
        kept = []
        for node in found:
            if any(node is not other and node in other.parents for other in found):
                continue
            kept.append(node)
        return kept

    @staticmethod
    def _links_for(node, base):
        hrefs = []
        for a in node.find_all("a", href=True):
            hrefs.append(urljoin(base, a["href"]))

        # Drupal themes/modules sometimes put the real URL in data-* attrs.
        for el in node.find_all(True):
            for attr in ("data-href", "data-url", "data-link", "data-download-url"):
                value = el.get(attr)
                if value and isinstance(value, str):
                    hrefs.append(urljoin(base, value))

        return linkmod.extract(str(node), hrefs)

    @staticmethod
    def _title_for(node, page_title, base):
        h = node.find(["h1", "h2", "h3"])
        if h:
            title = _clean_title(h.get_text(" ", strip=True))
            if title:
                return title

        # Common Drupal title field.
        for selector in (".field--name-title", ".node__title", ".page-title"):
            x = node.select_one(selector)
            if x:
                title = _clean_title(x.get_text(" ", strip=True))
                if title:
                    return title

        return page_title or base

    def _parse_html(self, html, base) -> list[Item]:
        soup = BeautifulSoup(html, "lxml")

        # Never let menus/footers dominate candidate scoring.
        for tag in soup.select(
            "script,style,noscript,template,svg,nav,footer,aside,"
            ".breadcrumb,.pager,.pagination,.sharethis-wrapper,.social-share"
        ):
            tag.decompose()

        page_title = _clean_title(
            (soup.find("h1") or soup.title).get_text(" ", strip=True)
            if (soup.find("h1") or soup.title) else base
        )

        candidates = self._node_candidates(soup)

        # If a page has no recognisable Drupal node wrapper, main is the
        # correct broad fallback. This is deliberately not a regex-only path.
        if not candidates:
            main = soup.select_one("main, [role='main'], .region-content") or soup.body or soup
            candidates = [main]

        # Score candidates by useful link density and meaningful text. Discard
        # tiny wrappers which are merely title/date fields.
        scored = []
        for node in candidates:
            text = _clean_title(node.get_text(" ", strip=True))
            links = self._links_for(node, base)
            if not text and not links:
                continue

            rapid = sum(
                1 for x in links
                if "rapidgator.net" in (x.get("host") or "")
            )
            score = min(len(text), 10000) + len(links) * 1000 + rapid * 5000
            scored.append((score, node, links))

        # If the candidate set contains only nested/small fields and the page
        # itself clearly contains release links, use the main content once.
        total_links = self._links_for(
            soup.select_one("main, [role='main'], .region-content") or soup,
            base,
        )
        if total_links and not any(
            any(x["url"] == y["url"] for x in links) for _, _, links in scored
            for y in total_links
        ):
            main = soup.select_one("main, [role='main'], .region-content") or soup.body or soup
            scored.append((min(len(main.get_text(" ", strip=True)), 10000) +
                           len(total_links) * 1000, main, total_links))

        # Remove duplicate candidates by their link set. This prevents nested
        # Drupal fields from creating the same release several times.
        unique = []
        seen_keys = set()
        for score, node, links in sorted(scored, key=lambda x: x[0], reverse=True):
            key = tuple(sorted(x["url"] for x in links))
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            unique.append((node, links))

        # A listing page should produce multiple nodes; a release page normally
        # produces one. Keep all distinct candidates, capped by poll(limit).
        out = []
        for node, links in unique:
            title = self._title_for(node, page_title, base)
            text = _clean_title(node.get_text(" ", strip=True))
            if not links and len(text) < 30:
                continue

            node_id = (
                node.get("data-history-node-id")
                or node.get("data-node-id")
                or node.get("id")
                or ""
            )
            if not node_id:
                # Stable enough for the crawler's per-page result.
                node_id = base + "#" + title

            ts = node.select_one("time")
            published = ts.get("datetime", "") if ts else ""

            out.append(Item(
                id=node_id,
                name=title,
                url=base,
                kind="release",
                published=published,
                summary=text[:400],
                links=links,
                store=self.name,
            ))

        if not out:
            # Last-resort extraction: still structured around the page's main
            # content, never around a regex over the entire document alone.
            body = soup.select_one("main, [role='main'], .region-content") or soup.body or soup
            links = self._links_for(body, base)
            out.append(Item(
                id=base,
                name=page_title or base,
                url=base,
                kind="release",
                summary=_clean_title(body.get_text(" ", strip=True))[:400],
                links=links,
                store=self.name,
            ))

        return out

    # -------------------------------------------------------------- public

    def resolve(self, url) -> str:
        status, body, headers = fetch.get(url, conditional=False)
        if self._is_feed(body, headers):
            items = self._parse_feed(body, url)
            return items[0].name if items else url
        soup = BeautifulSoup(body, "lxml")
        h1 = soup.find("h1")
        return _clean_title(
            h1.get_text(" ", strip=True) if h1 else
            soup.title.get_text(" ", strip=True) if soup.title else url
        )

    def poll(self, url, limit=50) -> list[Item]:
        status, body, headers = fetch.get(url)
        if status == 304:
            return []

        # For Drupal, parse the actual HTML first. A feed is a fallback because
        # it frequently omits download links which exist on the node page.
        if not self._is_feed(body, headers):
            html_items = self._parse_html(body, url)
            useful = sum(len(i.links) for i in html_items)
            if useful:
                return html_items[:limit]

            feed = self.discover_feed(url, body)
            if feed:
                try:
                    s2, b2, h2 = fetch.get(feed)
                    if s2 != 304:
                        feed_items = self._parse_feed(b2, feed)
                        if feed_items:
                            return feed_items[:limit]
                except Exception:
                    pass
            return html_items[:limit]

        return self._parse_feed(body, url)[:limit]

    def check(self, url) -> list:
        out = []
        try:
            allowed, note = fetch.robots_ok(url)
            out.append(("robots.txt allows", allowed, note or "no crawl-delay set"))
            if not allowed:
                return out
        except Exception as e:
            out.append(("robots.txt", False, str(e)))
            return out

        try:
            status, body, headers = fetch.get(url, conditional=False)
            out.append(("fetch", True, f"HTTP {status}, {len(body)} bytes"))
            isfeed = self._is_feed(body, headers)
            out.append(("is a feed", isfeed, headers.get("content-type", "")))

            items = self.poll(url)
            out.append(("Drupal/content items parsed", bool(items), f"{len(items)} found"))
            nl = sum(len(i.links) for i in items)
            rg = sum(
                1 for i in items for l in i.links
                if "rapidgator.net" in (l.get("host") or "")
            )
            out.append(("links extracted", nl > 0, f"{nl} total"))
            out.append(("Rapidgator links", True, f"{rg} found"))
        except fetch.Blocked as e:
            out.append(("fetch", False, str(e)))
        except Exception as e:
            out.append(("fetch", False, f"{type(e).__name__}: {e}"))
        return out


register(Web())
