"""Automatic multi-forum provider.

Fetches a page once, identifies the forum software, and selects the matching
forum extraction hopper. It is intentionally separate from the existing
Drupal-first provider so the current production path remains intact while
forum support is exercised independently.
"""
from .. import fetch
from ..forum_detector import best_match
from ..forum_parser import parse
from .base import register, ProviderError


class Forum:
    name = "forum"

    def resolve(self, url):
        status, body, _ = fetch.get(url, conditional=False)
        if status == 304 or not body:
            return url
        soup_title = None
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(body, "lxml")
            soup_title = soup.title.get_text(" ", strip=True) if soup.title else None
        except Exception:
            pass
        return soup_title or url

    def poll(self, url, limit=50):
        status, body, _ = fetch.get(url, conditional=False)
        if status == 304 or not body:
            return []
        items, detection = parse(body, url, limit=limit)
        if not detection:
            raise ProviderError("forum software could not be detected")
        for item in items:
            item.summary = f"[{detection.software}] {item.summary}"[:400]
        return items

    def check(self, url):
        out = []
        try:
            allowed, note = fetch.robots_ok(url)
            out.append(("robots.txt allows", allowed, note or "no crawl-delay set"))
            if not allowed:
                return out
            status, body, headers = fetch.get(url, conditional=False)
            out.append(("fetch", True, f"HTTP {status}, {len(body)} bytes"))
            detection = best_match(body)
            if not detection:
                out.append(("forum software", False, "no supported signature detected"))
                return out
            out.append(("forum software", True,
                        f"{detection.software} ({detection.confidence:.2f})"))
            items = self.poll(url)
            total = sum(len(i.links) for i in items)
            rg = sum(1 for i in items for l in i.links
                     if l.get("host") == "rapidgator.net")
            out.append(("threads/items parsed", bool(items), str(len(items))))
            out.append(("links harvested", total > 0, str(total)))
            out.append(("Rapidgator links", True, str(rg)))
        except fetch.Blocked as e:
            out.append(("fetch", False, str(e)))
        except Exception as e:
            out.append(("forum", False, f"{type(e).__name__}: {e}"))
        return out


register(Forum())
