"""WordPress sites, read through their own REST API.

A WordPress archive (ravetapepacks.com) is one post per release, and the
release's files are embedded players (<audio src=...>) rather than links, so
the HTML listing pages carry nothing to harvest. The REST API gives every
post's full content, newest first, 50 at a time, and works on any WordPress
site that has not switched it off -- including ones without pretty permalinks,
through ?rest_route=.

Each post becomes one Item. Its links carry what JD needs to save them
properly: `filename` (from the caption the site shows above each player, see
naming.py) and `folder` (post title + year). The post's images come along,
the first saved as cover.jpg.
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .. import fetch, naming, trace
from ..models import Item
from .base import register, ProviderError

PER_PAGE = 50
MAX_PAGES = 200            # 10,000 posts; a runaway guard, not a real limit
MEDIA_EXT = ("mp3", "flac", "wav", "aiff", "aif", "ogg", "m4a", "opus", "zip",
             "rar", "7z")
IMAGE_EXT = ("jpg", "jpeg", "png", "gif", "webp")


def is_wordpress(body: str) -> bool:
    b = (body or "")[:200_000]
    return bool(re.search(r'<meta[^>]+generator[^>]+WordPress', b, re.I)
                or "/wp-json/" in b or "rest_route=" in b
                or "/wp-content/" in b and "/wp-includes/" in b)


def site_root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme or 'https'}://{p.netloc}/"


def api_url(root: str, page: int) -> str:
    # ?rest_route= works with and without pretty permalinks; /wp-json/ only
    # with them.
    return (f"{root}index.php?rest_route=/wp/v2/posts&per_page={PER_PAGE}"
            f"&page={page}&orderby=date&order=desc"
            f"&_fields=id,date,link,title,content,categories")


def _ext(url: str) -> str:
    path = urlparse(url).path
    return path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""


def _caption_for(node) -> str:
    """The text the site shows just above a player: the nearest preceding
    block with words in it, skipping spacers and ad scripts. Reaching the
    previous player first means this one has no caption."""
    anchor = node.find_parent("figure") or node
    for prev in anchor.find_all_previous(limit=40):
        if prev.name == "audio":
            return ""
        if prev.name not in ("p", "h1", "h2", "h3", "h4", "h5", "h6"):
            continue
        text = prev.get_text(" ", strip=True)
        if text and "adsbygoogle" not in text:
            return text
    return ""


def _year(text: str) -> str:
    m = re.search(r"\bYEAR\s*:\s*((?:19|20)\d\d)\b", text or "", re.I)
    return m.group(1) if m else ""


def media_url(root: str, page: int) -> str:
    return (f"{root}index.php?rest_route=/wp/v2/media&per_page=100&page={page}"
            f"&_fields=id,post,source_url,mime_type")


def cats_url(root: str, page: int) -> str:
    return (f"{root}index.php?rest_route=/wp/v2/categories&per_page=100&page={page}"
            f"&_fields=id,name,parent")


# The category group a site files its packs by event under. ravetapepacks
# calls it "PACKS BY RAVE" (its menu says "Packs by Event").
_EVENT_GROUP = re.compile(r"\b(?:rave|event)s?\b", re.I)


def event_of(post: dict, cats: dict[int, dict]) -> str:
    """The post's event: its category under the event group. Tagged with two
    (Quest & Fibre Optic), the one its title names first."""
    groups = {cid for cid, c in cats.items()
              if not c.get("parent") and _EVENT_GROUP.search(c.get("name", ""))}
    names = [naming.clean_text(cats[c]["name"]) for c in post.get("categories") or []
             if c in cats and cats[c].get("parent") in groups]
    if not names:
        return ""
    title = naming.clean_text((post.get("title") or {}).get("rendered", "")).lower()
    return min(names, key=lambda n: (title.find(n.lower()) if n.lower() in title else 10 ** 6, n))


def parse_post(post: dict, base: str, attached: list[str] | None = None,
               prefix: str = "") -> Item:
    """One post as one release. `attached` are images uploaded to this post
    in the media library that the post body does not show (alternate covers);
    they are part of the release too."""
    title = naming.clean_text((post.get("title") or {}).get("rendered", ""))
    content = (post.get("content") or {}).get("rendered", "")
    soup = BeautifulSoup(content, "lxml")
    year = _year(soup.get_text("\n", strip=True))
    folder = naming.folder_name(title, year)
    if prefix:
        # Nested folders: "Rave Tape Packs/Helter Skelter/<pack>". Each part
        # is made safe on its own; "/" only ever separates them.
        folder = "/".join([naming.safe_component(x) for x in prefix.split("/") if x] + [folder])

    media = []                       # (url, caption)
    seen = set()
    for node in soup.find_all(["audio", "source", "a"]):
        src = node.get("src") or node.get("href") or ""
        if node.name == "audio" and not src:
            continue                 # its <source> child carries the url
        url = urljoin(base, src.strip())
        if _ext(url) not in MEDIA_EXT or url in seen:
            continue
        seen.add(url)
        holder = node if node.name != "source" else (node.find_parent("audio") or node)
        media.append((url, _caption_for(holder) if node.name != "a"
                      else node.get_text(" ", strip=True)))

    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        # The full-size original, not a srcset thumbnail (cover-212x300.jpg).
        parent = img.find_parent("a")
        if parent is not None and _ext(parent.get("href", "")) in IMAGE_EXT:
            src = parent["href"]
        url = urljoin(base, src.strip())
        if _ext(url) in IMAGE_EXT and url not in seen:
            seen.add(url)
            images.append(url)

    for url in attached or []:
        if _ext(url) in IMAGE_EXT and url not in seen:
            seen.add(url)
            images.append(url)

    links = []
    for i, (url, caption) in enumerate(media, 1):
        links.append({"url": url, "host": urlparse(url).netloc.lower(),
                      "bucket": "build", "caption": caption,
                      "filename": naming.file_name(i, len(media), caption, url),
                      "folder": folder, "role": "track", "source": post.get("link", ""),
                      "check": naming.disagreement(caption, url)})
    used = set()
    for j, url in enumerate(images):
        # The first image is the one the post shows: the cover. Any others
        # (alternate covers in the media library) keep their upload name,
        # prefixed so none can collide with cover.jpg or each other.
        orig = urlparse(url).path.rsplit("/", 1)[-1]
        name = ("cover." + _ext(url).replace("jpeg", "jpg") if j == 0
                else naming.safe_component(f"artwork {j} - {orig}"))
        if name.lower() in used:
            name = naming.safe_component(f"artwork {j} - {orig}")
        used.add(name.lower())
        links.append({"url": url, "host": urlparse(url).netloc.lower(),
                      "bucket": "build", "caption": "", "filename": name,
                      "folder": folder, "role": "image", "check": "",
                      "source": post.get("link", "")})

    note = "" if media else soup.get_text(" ", strip=True)[:200]
    return Item(id=str(post.get("id", "")), name=folder.rsplit("/", 1)[-1],
                url=post.get("link", ""), published=post.get("date", ""),
                kind="release", summary=note or f"{len(media)} file(s)",
                links=links if media else [], store="wordpress")


def _music_dir(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[0].lower()


def assign_shared_files(items: list[Item]) -> list[Item]:
    """A file shown in two posts belongs to one release. ravetapepacks'
    WELCOME post is a random-selection player: one track out of some pack.
    The file stays with the post holding most of its directory's files; a
    post left with no files of its own is not a release (its images go too)."""
    owners: dict[str, list[Item]] = {}
    for it in items:
        for l in it.links:
            if l.get("role") == "track":
                owners.setdefault(l["url"], []).append(it)

    def weight(it, d):
        return sum(1 for l in it.links if l.get("role") == "track" and _music_dir(l["url"]) == d)

    drop = {}
    for url, its in owners.items():
        if len(its) < 2:
            continue
        keep = max(its, key=lambda it: weight(it, _music_dir(url)))
        for it in its:
            if it is not keep:
                drop.setdefault(id(it), set()).add(url)
    for it in items:
        gone = drop.get(id(it))
        if not gone:
            continue
        tracks = [l for l in it.links if l.get("role") == "track" and l["url"] not in gone]
        if not tracks:
            trace.event("wordpress", "post only repeats other posts' files: skipped",
                        post=it.url, title=it.name[:60])
            it.summary = "only repeats files from other posts"
            it.links = []
            continue
        # Renumber what is left so the folder has no gaps.
        for i, l in enumerate(tracks, 1):
            l["filename"] = naming.file_name(i, len(tracks), l["caption"], l["url"])
        it.links = tracks + [l for l in it.links if l.get("role") == "image"]
    return items


class WordPress:
    name = "wordpress"
    # The crawl loop hands the whole job to poll(): the API pages itself.
    reads_whole_site = True

    def resolve(self, url):
        return site_root(url)

    def posts(self, url, since=None, max_pages=MAX_PAGES):
        """Every post, newest first, stopping at the date window."""
        root = site_root(url)
        for page in range(1, max_pages + 1):
            status, body, _ = fetch.get(api_url(root, page), conditional=False)
            if status == 400 or not body:
                return               # past the last page: WP answers 400
            try:
                rows = json.loads(body)
            except ValueError:
                raise ProviderError(f"REST API did not answer JSON on page {page}")
            if not isinstance(rows, list):
                if page == 1:
                    raise ProviderError("REST API is switched off on this site")
                return
            if not rows:
                return
            for row in rows:
                if since is not None:
                    try:
                        d = date.fromisoformat(row.get("date", "")[:10])
                    except ValueError:
                        d = None             # undatable posts are kept
                    if d is not None and d < since:
                        trace.event("wordpress", "older than the date window: stopping",
                                    post=row.get("link", ""), date=row.get("date", ""))
                        return
                yield row
            if len(rows) < PER_PAGE:
                return

    def attachments(self, url) -> dict[int, list[str]]:
        """Images in the media library, grouped by the post they belong to.
        A site that hides its media listing just gives none."""
        root, out = site_root(url), {}
        for page in range(1, MAX_PAGES + 1):
            try:
                status, body, _ = fetch.get(media_url(root, page), conditional=False)
                rows = json.loads(body) if status == 200 and body else []
            except Exception:
                rows = []
            if not isinstance(rows, list) or not rows:
                break
            for r in rows:
                if r.get("post") and str(r.get("mime_type", "")).startswith("image/"):
                    out.setdefault(int(r["post"]), []).append(r.get("source_url", ""))
            if len(rows) < 100:
                break
        return out

    def categories(self, url) -> dict[int, dict]:
        root, out = site_root(url), {}
        for page in range(1, MAX_PAGES + 1):
            try:
                status, body, _ = fetch.get(cats_url(root, page), conditional=False)
                rows = json.loads(body) if status == 200 and body else []
            except Exception:
                rows = []
            if not isinstance(rows, list) or not rows:
                break
            out.update({int(r["id"]): r for r in rows if "id" in r})
            if len(rows) < 100:
                break
        return out

    def site_name(self, url) -> str:
        try:
            status, body, _ = fetch.get(site_root(url) + "index.php?rest_route=/",
                                        conditional=False)
            return naming.clean_text(json.loads(body).get("name", "")) if status == 200 else ""
        except Exception:
            return ""

    def poll(self, url, limit=10 ** 6, since=None):
        root = site_root(url)
        attached = self.attachments(url)
        cats = self.categories(url)
        site = self.site_name(url) or urlparse(root).netloc.removeprefix("www.")
        out = []
        for row in self.posts(url, since=since):
            event = event_of(row, cats) or ("Other" if cats else "")
            prefix = f"{site}/{event}" if event else site
            out.append(parse_post(row, root, attached.get(int(row.get("id") or 0)), prefix))
            if len(out) >= limit:
                break
        out = assign_shared_files(out)
        trace.event("wordpress", "posts read", site=root, posts=len(out),
                    files=sum(len(i.links) for i in out))
        return out

    def check(self, url):
        out = []
        try:
            status, body, _ = fetch.get(api_url(site_root(url), 1), conditional=False)
            out.append(("REST API", status == 200, f"HTTP {status}, {len(body)} bytes"))
            items = [parse_post(r, site_root(url)) for r in json.loads(body)[:5]]
            out.append(("posts parsed", bool(items), str(len(items))))
            out.append(("files found", any(i.links for i in items),
                        str(sum(len(i.links) for i in items))))
        except Exception as e:
            out.append(("wordpress", False, f"{type(e).__name__}: {e}"))
        return out


register(WordPress())
