"""When was a post written? And is it inside the crawl's date window?

A forum thread worth crawling is often years long, and on an old board most
of it links to files that died long ago: on makinamania.net every MediaFire,
SwissTransfer and Megaupload link older than a year or two is gone. Reading
those posts costs a request a page and a link check a link, for nothing.

So a crawl can carry a cut-off -- "only posts newer than N months" -- and
everything here exists to apply it on any site:

  post_date(node)   the date of one post, or None when it says nothing
  newest(node)      the latest date anywhere in a block (a board row, a page)
  cutoff(months)    the date a window of N months starts on; None = no limit

Dates are read from what the page itself prints, family by family:

  <time datetime="2025-04-11T...">   XenForo, Invision, Discourse, Drupal
  data-time / data-timestamp=epoch   XenForo, vBulletin
  11/04/25, 20:35:08 pm               SMF 2.0 (dd/mm/yy)
  Hoy / Ayer / Today / Yesterday      SMF, MyBB, phpBB "relative" dates
  14 Abr 2025, Apr 14 2025, ...       phpBB, Forumotion, MyBB (es/en/fr/it/pt)
  2025-04-11                          everything else

A date that cannot be read is None, never "today": an unreadable post is
kept, not silently dropped. The window only ever removes what it can prove
is old.
"""
from __future__ import annotations

import datetime as _dt
import os
import re

from bs4 import Tag

# The default window when a crawl does not say: 0 = no limit. Months, not
# days, because that is how the owner thinks of it ("the last 18 months").
DEFAULT_MONTHS = int(os.environ.get("CW_SINCE_MONTHS", "0") or 0)

_MONTHS = {
    # en
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    # es
    "ene": 1, "abr": 4, "ago": 8, "dic": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    # fr
    "janv": 1, "févr": 2, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
    "juil": 7, "août": 8, "aout": 8, "déc": 12,
    "janvier": 1, "février": 2, "fevrier": 2, "avril": 4, "juillet": 7,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    # it / pt
    "gen": 1, "mag": 5, "giu": 6, "lug": 7, "set": 9, "ott": 10,
    "fev": 2, "out": 10, "dez": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MON = "|".join(sorted((re.escape(m) for m in _MONTHS), key=len, reverse=True))

_TODAY = re.compile(r"\b(?:hoy|today|aujourd'hui|oggi|hoje)\b", re.I)
_YESTERDAY = re.compile(r"\b(?:ayer|yesterday|hier|ieri|ontem)\b", re.I)
# dd/mm/yy or dd/mm/yyyy, as SMF prints it ("11/04/25, 20:35:08 pm"). Read as
# day-first: every board this crawler has met is European.
_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b(?:,?\s+\d{1,2}:\d\d)?")
# No trailing \b: "2025-05-02T10:00" runs the day straight into the T.
_ISO = re.compile(r"\b(20\d\d|19\d\d)-(\d\d)-(\d\d)(?!\d)")
# "14 Abr 2025", "14 de abril de 2025", "Sáb 14 Sep 2024 - 21:34"
_D_MON_Y = re.compile(rf"\b(\d{{1,2}})(?:\s+de)?\s+({_MON})\.?(?:\s+de)?,?\s+(\d{{4}})\b", re.I)
# "Apr 14, 2025", "April 14 2025"
_MON_D_Y = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)


def _today() -> _dt.date:
    return _dt.date.today()


def _mk(y: int, m: int, d: int) -> _dt.date | None:
    if y < 100:
        y += 2000 if y < 70 else 1900
    try:
        return _dt.date(y, m, d)
    except ValueError:
        return None


def parse(text: str) -> _dt.date | None:
    """The first date written in this text, or None."""
    text = text or ""
    if _TODAY.search(text):
        return _today()
    if _YESTERDAY.search(text):
        return _today() - _dt.timedelta(days=1)
    m = _ISO.search(text)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    m = _D_MON_Y.search(text)
    if m:
        return _mk(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))
    m = _MON_D_Y.search(text)
    if m:
        return _mk(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))
    m = _DMY.search(text)
    if m:
        return _mk(int(m[3]), int(m[2]), int(m[1]))
    return None


def all_dates(text: str) -> list[_dt.date]:
    """Every date written in this text (dd/mm/yy and ISO forms)."""
    out = []
    for m in _ISO.finditer(text or ""):
        d = _mk(int(m[1]), int(m[2]), int(m[3]))
        if d:
            out.append(d)
    for m in _DMY.finditer(text or ""):
        d = _mk(int(m[3]), int(m[2]), int(m[1]))
        if d:
            out.append(d)
    if _TODAY.search(text or ""):
        out.append(_today())
    if _YESTERDAY.search(text or ""):
        out.append(_today() - _dt.timedelta(days=1))
    return out


def _machine_date(node: Tag) -> _dt.date | None:
    for el in [node] + node.find_all(["time", "abbr", "span", "a", "div"], limit=200):
        if not isinstance(el, Tag):
            continue
        raw = el.get("datetime")
        if raw:
            d = parse(str(raw))
            if d:
                return d
        for attr in ("data-time", "data-timestamp", "data-date"):
            raw = el.get(attr)
            if raw and str(raw).isdigit():
                try:
                    return _dt.datetime.utcfromtimestamp(int(raw)).date()
                except (OverflowError, ValueError, OSError):
                    pass
    return None


# Where a post's own date lives, by family: SMF "keyinfo" ("« Respuesta #3 en:
# 11/04/25 »"), phpBB/Forumotion "author"/"postdetails", MyBB "post_date",
# vBulletin "postdate", generic "date".
_DATE_HOLDERS = (".keyinfo", ".post_date", ".postdate", "p.author", ".author",
                 ".postdetails", ".message-attribution", ".date", "time")


def post_date(post: Tag) -> _dt.date | None:
    """The date one post was written.

    Quotes are skipped on purpose: "Cita de: X en 03/07/18" inside a 2025 post
    is the quoted post's date, and reading it would make a new post look old.
    """
    if post is None:
        return None
    d = _machine_date(post)
    if d:
        return d
    # The header usually sits just before the body (SMF: keyinfo, then post).
    holders = []
    for sel in _DATE_HOLDERS:
        holders.extend(post.select(sel))
    prev = post.find_previous(class_=re.compile(r"keyinfo|postdetails|post_date|postdate|author"))
    if prev is not None:
        holders.append(prev)
    for h in holders:
        if h.find_parent("blockquote") is not None:
            continue
        d = parse(h.get_text(" ", strip=True))
        if d:
            return d
    return _header_date(post)


# A post header always has a TIME: "25/08/26, 18:47:15 pm", "Hoy a las 20:35",
# "2025-04-11T10:00". A member's join date in the profile box beside the post
# ("Registro: 22-08-18") has none, so requiring one keeps it out.
_DATE_WITH_TIME = re.compile(
    r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2}),?\s+\d{1,2}:\d\d"
    r"|\b(?:hoy|ayer|today|yesterday)\b[^0-9]{0,12}\d{1,2}:\d\d"
    r"|\b(?:19|20)\d\d-\d\d-\d\dT\d\d:\d\d", re.I)


def _header_date(post: Tag) -> _dt.date | None:
    """Themes that print the date in a plain block before the post (makinamania's
    custom SMF theme: a div.smalltext in the same cell). Take the nearest
    preceding date-with-time that sits outside every post and every quote,
    with no other post between it and this one."""
    for text in post.find_all_previous(string=_DATE_WITH_TIME, limit=12):
        el = text.parent
        if el is None or el.find_parent("blockquote") is not None:
            continue
        if el.find_parent("div", class_="post") is not None:
            continue
        nxt = el.find_next("div", class_="post")
        if nxt is not None and nxt is not post:
            return None          # the date belongs to an earlier post
        return parse(str(text))
    return None


def newest(node_or_text) -> _dt.date | None:
    """The latest date anywhere in a block -- a board row's "last post", a page."""
    if isinstance(node_or_text, Tag):
        d = _machine_date(node_or_text)
        text = node_or_text.get_text(" ", strip=True)
        ds = all_dates(text) + ([d] if d else [])
    else:
        ds = all_dates(str(node_or_text or ""))
    return max(ds) if ds else None


def cutoff(months: int | None = None, today: _dt.date | None = None) -> _dt.date | None:
    """First day inside a window of `months` months; None means no limit."""
    months = DEFAULT_MONTHS if months is None else int(months)
    if months <= 0:
        return None
    t = today or _today()
    y, m = divmod((t.year * 12 + t.month - 1) - months, 12)
    m += 1
    # Clamp the day for short months (31 Mar - 1 month = 28/29 Feb).
    for day in (t.day, 30, 29, 28):
        try:
            return _dt.date(y, m, day)
        except ValueError:
            continue
    return _dt.date(y, m, 1)


def too_old(date: _dt.date | None, since: _dt.date | None) -> bool:
    """True only when the date is known AND before the window."""
    return bool(since and date and date < since)
