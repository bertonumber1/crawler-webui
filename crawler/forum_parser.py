"""Software-aware forum extraction.

The detector tells us which forum family we are looking at; this module turns
that signal into a conservative extraction strategy. It never assumes a
single theme: each strategy has multiple selectors and then falls back to
link evidence.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag

from . import links as linkmod, postdates
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
    # MyBB. `.post_body` is where a release post's links live. trow1/trow2 are
    # deliberately ABSENT: they are generic table furniture used by the header,
    # the stats block and the footer alike, so including them made a thread
    # parse as one item holding 79 links, every one of them site navigation.
    "MyBB": (
        ".post_body", ".post_content", ".postbit-box",
        ".inline_row", ".forumdisplay_regular",
    ),
    # Forumotion's phpBB3-style template: a post is `.post`, a row of a board
    # listing is `ul.topiclist li.row`. Members-only links sit in the post as
    # <dl class="codebox hidecode">, which is still inside `.post`.
    "Forumotion": (
        ".post", ".postbody", "ul.topiclist li.row",
    ),
    "phpBB": (
        ".topiclist", ".topiclist_topics", ".post", ".postbody", ".forabg",
        ".forumbg",
    ),
    # SMF 2.0: a post is div.post (inside .windowbg/.windowbg2), a search
    # result div.list_posts, a board row a <tr> of #messageindex's table.
    "SMF": (
        "div.post", "div.list_posts",
    ),
    # An SMF board (topic list) has no posts: one item per topic row. Default
    # theme: #messageindex / table.table_grid; makinamania's classic theme:
    # table.bordercolor. Kept apart from "SMF" because a topic page may wrap
    # its posts in the same tables, and the outer row would swallow them.
    "SMF-board": (
        "#messageindex tr", "table.table_grid tr", "table.bordercolor tr",
    ),
    "Invision Community": (
        ".ipsDataItem", ".ipsComment", ".ipsType_richText", ".ipsLayout_container",
        ".cTopic", ".ipsAreaBackground_light",
    ),
}

# A Forumotion topic: /t123-slug, or a later page of it, /t123p15-slug. Only
# the bare URL counts. The same path with a query string is an ACTION --
# ?eval=plus&p_vote=N casts a vote as the logged-in member, ?watch=topic
# subscribes -- and following it would do that on the member's account.
# /t123n- and /t123p- (next/previous topic) are deliberately not matched.
# ?highlight=<words> is the one harmless query: search results add it to every
# topic link, and it only colours the matched words.
_FORUMOTION_TOPIC = re.compile(r"^/t\d+(?:p\d+)?-")
# Profiles (/u123) and boards (/f12-slug): pages, but never a release.
FORUMOTION_FURNITURE = re.compile(r"^/(?:u\d+|f\d+(?:p\d+)?-)")


def forumotion_topic_url(url: str) -> str | None:
    """The bare topic URL, or None when this is not a plain topic link."""
    p = urlparse(url or "")
    if not _FORUMOTION_TOPIC.match(p.path):
        return None
    keys = {k.split("=", 1)[0].lower() for k in p.query.split("&") if k}
    if keys - {"highlight"}:
        return None
    return p._replace(query="", fragment="").geturl()


def is_forumotion_topic(url: str) -> bool:
    return forumotion_topic_url(url) is not None


# SMF (Simple Machines). A topic or board is an id plus an OFFSET -- posts
# (topics) or topics (boards) skipped -- in one of two spellings, and the
# same board serves both: makinamania.net hands a logged-in member
#   index.php?topic=189337.16290        index.php?board=52.40
# but intermittently the cached guest copy of the very same page, with
#   index.php/topic,189337.16290.html   index.php/board,52.40.html
# Accept both everywhere, or pagers and board rows "vanish" mid-crawl.
# topic=N.msgM (a jump to one message) and ;prev_next= (the NEXT TOPIC, not
# the next page) are not page links.
SMF_ID = re.compile(r"(?:[?&;]|/index\.php/)(topic|board)[=,](\d+)\.(\d+)(?:\.html)?(?=$|[;&#?\"'])")
# Query keys that make an SMF GET do something: SMF puts all of these under
# index.php?action=..., so any action at all is refused.
_SMF_ACTION = re.compile(r"[?&;]action=|[;&]prev_next=|[;&]sesc=|[;&]\w+=[0-9a-f]{32}", re.I)


def smf_id(url: str) -> tuple[str, int, int] | None:
    """('topic'|'board', id, offset) for an SMF page link, else None."""
    if not url or _SMF_ACTION.search(url):
        return None
    m = SMF_ID.search(url.split("#", 1)[0])
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3))


def smf_url(base: str, kind: str, ident: int, offset: int = 0) -> str:
    """One canonical spelling: index.php?topic=N.S on the base's host."""
    p = urlparse(base)
    return f"{p.scheme or 'https'}://{p.netloc}/index.php?{kind}={ident}.{offset}"


def smf_topic_url(url: str) -> str | None:
    """The topic's first page, canonical. topic=N.msgM is a post, not a topic page."""
    got = smf_id(url)
    if not got or got[0] != "topic":
        return None
    return smf_url(url, "topic", got[1], 0)


def is_smf_topic(url: str) -> bool:
    return smf_topic_url(url) is not None


# Signatures: the same links under every post a member writes. On makinamania
# one member's MEGA links appeared in every topic they replied to, so without
# this a crawl "found" the same two files on every page. Covers SMF, phpBB,
# MyBB, vBulletin, XenForo and Invision.
SIGNATURES = (".signature", ".signaturecontainer", ".postbit_signature",
              ".sig", ".signature-container", ".message-signature",
              ".cAuthorPane_signature", ".ipsComment_signature", "div[id^='msg_'][id$='_signature']")


def strip_signatures(soup) -> None:
    for sel in SIGNATURES:
        for node in soup.select(sel):
            node.decompose()


# Tags removed before extraction because they never hold a release. `form` is
# deliberately NOT here: Invision wraps every post in
# <form data-role="moderationTools">, so decomposing forms wholesale deletes
# the post -- and the download link inside it -- before it can be read. A real
# search or login form is dropped by _drop_forms() below on what it contains,
# not on being a form at all.
FURNITURE = {
    "nav", "header", "footer", "aside", "script", "style", "noscript",
    "template", "svg",
}

# Forms worth removing: a password field, or a search role/name. A post-wrapper
# form has neither.
def _drop_forms(soup):
    for form in soup.find_all("form"):
        if (form.find("input", {"type": "password"})
                or (form.get("role") == "search")
                or "search" in " ".join(form.get("class") or []).lower()
                or "search" in (form.get("action") or "").lower()):
            form.decompose()


_QUOTE_HEAD = re.compile(r"(?:Cita de|Quote from|Citation de|Zitat von):? .+? (?:en|on|le|am) \d\d?/\d\d?/\d\d(?:\d\d)?,? \d\d?:\d\d(?::\d\d)?(?: ?[ap]m)?", re.I)
_DISCOGS_SLUG = re.compile(r"discogs\.com/(?:\w\w/)?(?:release|master)/\d+-([^\s/?#\"'<>]+)")


def post_title(node: Tag) -> str | None:
    """A release name for one forum post: the Discogs title it links, else the
    poster's own first words (quotes and URLs removed). In a sharing thread a
    post IS a release, and "Re: RECOPILATORIOS 2000's" names none of them."""
    own = node.__copy__() if hasattr(node, "__copy__") else node
    for q in own.find_all("blockquote"):
        q.decompose()
    for q in own.select(".quoteheader, .quotefooter, .topslice_quote, .botslice_quote"):
        q.decompose()
    html = str(own)
    m = _DISCOGS_SLUG.search(html)
    if m:
        from urllib.parse import unquote
        return " ".join(unquote(m.group(1)).replace("-", " ").split())[:100]
    for a in own.find_all("a"):         # a link's label is not the release name
        a.decompose()
    text = _QUOTE_HEAD.sub(" ", own.get_text(" ", strip=True))
    text = re.split(r"https?://", text)[0]
    text = " ".join(text.split()).strip(" .:-")
    return text[:100] or None


# Spam bots flood a board with zero-reply topics from one account: on
# makinamania's MAKINA: GENERAL one member started 30 of 40 topics on a page
# (software cracks, game-gold ads), none ever answered. Opening each costs a
# paced page fetch. A real uploader's releases get replies, or arrive a few
# at a time, so the bar is high: 6+ zero-reply topics by one starter on one
# listing page.
BOT_FLOOD = 6
_PROFILE = re.compile(r"action=profile;u=(\d+)|/profile/|member\.php\?u=|/members/", re.I)


def _row_starter_and_replies(row: Tag) -> tuple[str | None, int | None]:
    cells = row.find_all("td", recursive=False) or row.find_all("td")
    starter, replies = None, None
    for i, td in enumerate(cells):
        a = td.find("a", href=_PROFILE)
        if a is not None and not td.find("a", href=re.compile(r"topic[=,]\d+")):
            starter = a.get_text(" ", strip=True) or a["href"]
            for nxt in cells[i + 1:]:
                txt = nxt.get_text(" ", strip=True).replace(".", "").replace(",", "")
                if txt.isdigit():
                    replies = int(txt)
                    break
            break
    return starter, replies


def drop_bot_floods(rows: list[Tag]) -> list[Tag]:
    info = [_row_starter_and_replies(r) for r in rows]
    zero = {}
    for starter, replies in info:
        if starter and replies == 0:
            zero[starter] = zero.get(starter, 0) + 1
    flood = {s for s, n in zero.items() if n >= BOT_FLOOD}
    return [r for r, (st, rp) in zip(rows, info) if not (st in flood and rp == 0)]


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
          limit: int = 50, since=None) -> tuple[list[Item], Detection | None]:
    """Parse a forum page into release/thread candidates and return detection.

    since: a date; posts and board rows provably older are left out. A block
    whose date cannot be read is kept -- the window only drops what it can
    prove is old."""
    soup = BeautifulSoup(html or "", "lxml")
    detection = detection or best_match(html)
    if not detection or detection.software not in STRATEGIES:
        return [], detection

    for tag in soup.find_all(FURNITURE):
        tag.decompose()
    _drop_forms(soup)
    strip_signatures(soup)

    page_title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else base_url).split())
    strategy = detection.software
    if strategy == "SMF" and not soup.select("div.post, div.list_posts"):
        strategy = "SMF-board"
    nodes = _candidate_nodes(soup, strategy)
    if strategy == "SMF-board":
        # Only rows that link a topic: header/stats rows are not topics.
        nodes = [n for n in nodes if n.find("a", href=re.compile(r"topic[=,]\d+\.\d+"))]
        nodes = drop_bot_floods(nodes)
    if not nodes:
        main = soup.select_one("main, [role='main'], #content") or soup.body or soup
        nodes = [main]

    out, seen = [], set()
    for node in nodes:
        if since is not None:
            # A board row is as new as its last post; a post as new as its own
            # header (never a quote inside it).
            when = (postdates.newest(node) if node.name == "tr"
                    else postdates.post_date(node))
            if postdates.too_old(when, since):
                continue
        links = _links(node, base_url)
        # A forum candidate is useful when it has a download host, a wrapper,
        # or a meaningful thread URL. We deliberately don't emit every UI box.
        useful = [l for l in links if l.get("downloadable")]
        thread_links = [
            l for l in links
            if _same_host(l["url"], base_url)
            and (any(x in urlparse(l["url"]).path.lower() for x in ("/thread", "/threads/", "/showthread", "/viewtopic", "/topic/") )
                 or is_forumotion_topic(l["url"]) or is_smf_topic(l["url"]))
        ]
        if not useful and not thread_links:
            continue

        title = ((post_title(node) if detection.software == "SMF" and node.name == "div" else None)
                 or _title(node, page_title))
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
