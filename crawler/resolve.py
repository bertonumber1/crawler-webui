def _mark_key(url: str, ident: int) -> str:
    return f"smf_mark:{urlparse(url).netloc.lower().removeprefix('www.')}:{ident}"


def smf_mark(url: str) -> int | None:
    """The newest page offset of this SMF thread already read in full, if any."""
    got = smf_id(url)
    if not got:
        return None
    from . import db
    try:
        raw = db.setting(_mark_key(url, got[1]), "")
    except Exception:
        return None
    return int(raw) if str(raw).isdigit() else None


def set_smf_mark(url: str, offset: int) -> None:
    got = smf_id(url)
    if not got:
        return
    from . import db
    try:
        db.set_setting(_mark_key(url, got[1]), int(offset))
    except Exception:
        pass


def smf_window_pages(url: str, since, resume: bool = True):
    """Yield (page_url, html) for an SMF topic, newest page first, down to the
    first page that holds a post older than `since` (that page included: its
    newer posts count). The pager's offsets come in either SMF spelling.

    resume: a thread is not a finished release -- RECOPILATORIOS gains pages
    every day. The newest offset read is remembered once a walk completes, and
    the next walk stops at that page (re-reading it: new replies land there
    first). A weekly re-crawl of a 1,268-page thread then costs a page or two.
    """
    kind, ident, _ = smf_id(url)
    first = smf_url(url, "topic", ident, 0)
    status, body, _ = fetch.get(first, conditional=False)
    if status == 304 or not body:
        return
    soup = BeautifulSoup(body, "lxml")
    offs = [0]
    for a in soup.find_all("a", href=True):
        got = smf_id(urljoin(url, a["href"]))
        if got and got[0] == "topic" and got[1] == ident:
            offs.append(got[2])
    per_page = min((o for o in offs if o > 0), default=15)
    newest = off = (max(offs) // per_page) * per_page
    floor = smf_mark(url) if resume else None
    if floor is not None and floor > newest:
        floor = None                     # the thread was pruned: read it again
    pages = 0
    while pages < pagination.MAX_PAGES:
        page_url = smf_url(url, "topic", ident, off)
        if off == 0:
            page = body
        else:
            st, page, _ = fetch.get(page_url, conditional=False)
            if st == 304 or not page:
                return
        pages += 1
        yield page_url, page
        _, dates = _posts_in_window(page, since)
        if (off == 0 or any(postdates.too_old(d, since) for d in dates)
                or (floor is not None and off <= floor)):
            break
        off -= per_page
    # Only a walk that ran to its end moves the mark; a cancelled one (the
    # consumer stops iterating) leaves it, so the unread pages are read next time.
    if resume:
        set_smf_mark(url, newest)
        trace.event("resolve", "smf thread read up to", url=first, offset=newest,
                    pages=pages, resumed_from=floor)


def _smf_window_links(url: str, since) -> list[dict]:
    found = []
    for page_url, page in smf_window_pages(url, since):
        kept, dates = _posts_in_window(page, since)
        for html in kept:
            found.extend(_direct(html, url))
            found.extend(_short_links(html, url))
        trace.event("resolve", "smf window page", url=page_url,
                    posts=len(dates), in_window=len(kept))
    return _dedupe(found)


"""Resolve Drupal listing pages to real file-host URLs for JDownloader."""
import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urlparse, urljoin, urldefrag
from bs4 import BeautifulSoup

from . import fetch, links as linkmod, trace
from . import postdates, pagination
from .forum_parser import (FORUMOTION_FURNITURE, forumotion_topic_url,
                           is_forumotion_topic, is_smf_topic, smf_id, smf_topic_url,
                           smf_url, strip_signatures)

DOWNLOADABLE = {"build"}
FOLLOWABLE = ("/download/", "/node/", "/release", "/album", "/threads/", "/topic")
FURNITURE = (
    "/genre/", "/tags/", "/taxonomy/", "/user", "/comment", "/search",
    "/rss", "/feed", "/collections", "/requests", "/privacy", "/contact",
    "/admin", "/misc/", "/sites/", "/modules/", "/themes/", "/files/",
)
FURNITURE_EXACT = ("", "/", "/reuploads", "/collections", "/requests")
# Words that suggest a page exists only to point somewhere else. File-host
# names are deliberately NOT here: a Nitroflare link on a Rapidgator-only day
# is a dead end, not a wrapper worth fetching, and listing the hosts made the
# resolver follow every switched-off host it met.
WRAPPER_TOKENS = (
    "redirect", "goto", "out.php", "away.php", "link.php", "dl.php",
    "mirror", "filehost", "download.php", "/go/", "/link/", "/dl/",
)
# Query keys that make a GET do something as the logged-in member: vote on a
# post, subscribe, mark read, log out. A crawler with a member cookie must
# never follow these, whatever the path looks like. (Forumotion puts a vote
# link, ?eval=plus&p_vote=N, under every post.)
ACTION_KEYS = {
    "eval", "p_vote", "vote", "watch", "unwatch", "mark", "logout", "delete",
}
TARGET_KEYS = {
    "url", "u", "target", "dest", "destination", "redirect", "redirect_url",
    "link", "download", "file", "href",
}


def is_downloadable(link: dict) -> bool:
    # The crawler currently harvests Rapidgator only.
    # Do not let the generic "build" bucket accidentally admit MEGA,
    # K2S, MediaFire, Nitroflare, etc.
    return bool(link.get("downloadable"))


def _is_action(url: str) -> bool:
    return any(k.lower() in ACTION_KEYS for k in parse_qs(urlparse(url).query, keep_blank_values=True))


# Release titles say their format: [FLAC], [WAV], [LOSSLESS] against [MP3],
# [LOSSY]. Forumotion puts the title in the topic URL too
# (/t12293-lossless-pont-aeri-...), so the choice is made BEFORE a topic is
# opened -- a lossy topic is never visited, which on a members-only board is
# one fewer view on the account.
_LOSSLESS = re.compile(r"(?<![a-z0-9])(?:flac|wav|wave|lossless|aiff?|alac|ape|wv|dsd)(?![a-z0-9])", re.I)
_LOSSY = re.compile(r"(?<![a-z0-9])(?:lossy|mp3|aac|m4a|ogg|opus|\d{3}\s*kbps)(?![a-z0-9])", re.I)
# Request threads ("peticiones", "se buscan en lossless") name a format they
# are asking for, not one they carry.
_REQUEST = re.compile(r"(?<![a-z0-9])(?:peticion(?:es)?|se[\s-]+buscan?|busco|request(?:s|ed)?)(?![a-z0-9])", re.I)


def looks_lossless(title: str, url: str = "") -> bool:
    text = f"{title} {urlparse(url).path}"
    return (bool(_LOSSLESS.search(text)) and not _LOSSY.search(text)
            and not _REQUEST.search(text))


def looks_lossy(title: str, url: str = "") -> bool:
    """The title says MP3/lossy, or it is a request thread: never worth opening."""
    text = f"{title} {urlparse(url).path}"
    if _REQUEST.search(text):
        return True
    return bool(_LOSSY.search(text)) and not _LOSSLESS.search(text)


def candidates(items, base_url: str, limit: int = 40,
               lossless_only: bool = False) -> list[dict]:
    """Topics to open. With lossless_only the choice adapts to the site:

    - titles that SAY the format (dark-decade: "lossless-pont-aeri-...",
      "[MP3] ..."): only the lossless ones are opened, as before;
    - titles that never say it (makinamania: "RECOPILATORIOS 2000's",
      "[DISCOTECA] Coliseum"): every topic is opened except ones titled
      MP3/lossy or requests, and each link is then proven lossless or not by
      its host (crawler/linkcheck.py) before it is sent.
    """
    if lossless_only:
        strict = _candidates(items, base_url, limit, mode="strict")
        if strict:
            return strict
        return _candidates(items, base_url, limit, mode="not-lossy")
    return _candidates(items, base_url, limit, mode="")


def _candidates(items, base_url: str, limit: int, mode: str) -> list[dict]:
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
                # A #fragment names a post on a page, not a different page:
                # a board row links a topic and its #latest-post separately.
                u = urldefrag(l["url"])[0]
                if not on_site(l) or _is_action(u) or not test(u):
                    continue
                # Search results link /t123-slug?highlight=words; the same
                # topic reached twice must be fetched once.
                u = forumotion_topic_url(u) or smf_topic_url(u) or u
                # The link's own text is the topic title; the item name is
                # the page's, which on a listing says nothing about format.
                if mode == "strict" and not looks_lossless(l.get("text", ""), u):
                    continue
                if mode == "not-lossy" and looks_lossy(l.get("text", ""), u):
                    continue
                key = u.rstrip("/").lower()
                if key in seen or (here and key.endswith(here)):
                    continue
                seen.add(key)
                out.append({"url": u, "title": name})
                if len(out) >= limit:
                    return out
        return out

    hits = collect(lambda u: any(p in urlparse(u).path.lower() for p in FOLLOWABLE)
                   or is_forumotion_topic(u) or is_smf_topic(u))
    if hits:
        return hits

    def plausible(u):
        path = urlparse(u).path.lower()
        if FORUMOTION_FURNITURE.match(path):
            return False
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
    # A recognised file host is the destination, never a step towards it.
    # Following one costs a request and a captcha for nothing.
    if linkmod.is_known_file_host(url):
        return False
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


def page_links(url: str, since=None) -> list[dict]:
    """Every downloadable link on a release/topic page.

    since: a date. Posts provably older are ignored, and an SMF topic is read
    from its LAST page backwards until a page holds a post older than that --
    a 1,268-page thread whose last 18 months are 183 pages costs 183 fetches,
    not 1,268, and never touches the years of dead links before them.
    """
    if since is not None and is_smf_topic(url):
        return _smf_window_links(url, since)
    status, body, _ = fetch.get(url, conditional=False)
    if status == 304 or not body:
        return []
    body = _without_signatures(body)

    direct = _dedupe(_direct(body, url) + _short_links(body, url))
    if direct:
        return direct

    # If the release page only exposes wrappers, follow those wrappers. This
    # is deliberately bounded to two hops and only starts from links that look
    # like download/redirect/file-host wrappers.
    return _resolve_wrapper_links(body, url)


# Link shorteners. makinamania's WAV rips are posted as tinyurl links that
# redirect to OpenDrive (od.lk); without one hop they are invisible. One
# redirect hop only, through fetch's own robots/throttle -- never a page read.
SHORTENERS = re.compile(
    r"https?://(?:www\.)?(?:tinyurl\.com|bit\.ly|cutt\.ly|shorturl\.at|is\.gd|"
    r"ow\.ly|t\.ly|acortar\.link|n9\.cl|rebrand\.ly|tiny\.cc)/[^\s\"'<>]+", re.I)


def _short_links(html: str, base: str) -> list[dict]:
    out = []
    for short in dict.fromkeys(m.rstrip('.,);') for m in SHORTENERS.findall(html or "")):
        try:
            final = fetch.resolve_final_url(short)
        except Exception as e:
            trace.event("resolve", "short link failed", url=short, error=f"{type(e).__name__}")
            continue
        got = linkmod.classify(final)
        if is_downloadable(got):
            got["via"] = short
            out.append(got)
    return out


def _without_signatures(body: str) -> str:
    soup = BeautifulSoup(body or "", "lxml")
    strip_signatures(soup)
    return str(soup)


def _posts_in_window(body: str, since) -> tuple[list[str], list]:
    """(html of each post inside the window, the date of every post)."""
    soup = BeautifulSoup(body or "", "lxml")
    strip_signatures(soup)
    kept, dates = [], []
    for post in soup.select("div.post"):
        d = postdates.post_date(post)
        dates.append(d)
        if not postdates.too_old(d, since):
            kept.append(str(post))
    return kept, dates


def _smf_window_links(url: str, since) -> list[dict]:
    kind, ident, _ = smf_id(url)
    status, body, _ = fetch.get(smf_url(url, "topic", ident, 0), conditional=False)
    if status == 304 or not body:
        return []
    # The pager names the last page; its offsets are in either SMF spelling.
    soup = BeautifulSoup(body, "lxml")
    offs = [0]
    for a in soup.find_all("a", href=True):
        got = smf_id(urljoin(url, a["href"]))
        if got and got[0] == "topic" and got[1] == ident:
            offs.append(got[2])
    per_page = min((o for o in offs if o > 0), default=15)
    off = (max(offs) // per_page) * per_page
    found, pages = [], 0
    while pages < pagination.MAX_PAGES:
        if off == 0:
            page = body
        else:
            st, page, _ = fetch.get(smf_url(url, "topic", ident, off), conditional=False)
            if st == 304 or not page:
                break
        pages += 1
        kept, dates = _posts_in_window(page, since)
        for html in kept:
            found.extend(_direct(html, url))
            found.extend(_short_links(html, url))
        trace.event("resolve", "smf window page", url=url, offset=off,
                    posts=len(dates), in_window=len(kept))
        if off == 0 or any(postdates.too_old(d, since) for d in dates):
            break
        off -= per_page
    return _dedupe(found)


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


def resolve(items, base_url: str, limit: int = 40, on_progress=None,
            on_release=None, lossless_only: bool = False,
            skip_done: bool = False, cancel=None, since=None) -> dict:
    """skip_done: leave out release pages whose every file was already sent.
    cancel: a threading.Event; once set, unread release pages are abandoned."""
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

    todo = candidates(items, base_url, limit, lossless_only=lossless_only)
    skipped = 0
    if skip_done and todo:
        from . import history
        done = history.done_releases(c["url"] for c in todo)
        skipped = sum(1 for c in todo if c["url"].rstrip("/").lower() in done)
        todo = [c for c in todo if c["url"].rstrip("/").lower() not in done]
        if skipped:
            trace.event("resolve", "known releases skipped", skipped=skipped, left=len(todo))
    releases = [None] * len(todo)
    flat, errors = [], 0
    workers = max(1, min(len(todo) or 1, int(os.getenv("CRAWLER_RESOLVE_CONCURRENCY", "6"))))

    def one(c):
        if cancel is not None and cancel.is_set():
            return {"url": c["url"], "title": c["title"], "links": [],
                    "count": 0, "cancelled": True}
        try:
            found = (page_links(c["url"]) if since is None
                     else page_links(c["url"], since=since))
            trace.event("resolve", "release page followed",
                        url=c["url"], found=len(found), title=(c["title"] or "")[:60])
            return {"url": c["url"], "title": c["title"],
                    "links": found, "count": len(found)}
        except Exception as e:
            trace.event("resolve", "release page failed",
                        url=c["url"], error=f"{type(e).__name__}: {e}")
            return {"url": c["url"], "title": c["title"], "links": [],
                    "count": 0, "error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="resolve") as pool:
        futures = {pool.submit(one, c): i for i, c in enumerate(todo)}
        done = 0
        for future in as_completed(futures):
            idx = futures[future]
            rel = future.result()
            if rel.get("cancelled"):
                continue
            releases[idx] = rel
            if rel["links"]:
                from . import history
                history.remember_release(rel["url"], rel["title"], rel["links"])
            flat.extend(rel["links"])
            errors += bool(rel.get("error"))
            done += 1
            # Hand each release on the moment it is read, so the caller can
            # queue it in JD then rather than after the whole crawl.
            if on_release and rel["links"]:
                on_release(rel)
            if on_progress:
                on_progress(done, len(todo), rel["title"] or rel["url"])

    releases = [r for r in releases if r is not None]

    return {
        "hopped": True,
        "followed": len(todo),
        "skipped_known": skipped,
        "cancelled": bool(cancel is not None and cancel.is_set()),
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
