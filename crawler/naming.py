"""Folder and file names for releases whose site names each file.

A tape-pack site (ravetapepacks.com) shows a caption above every player --
"TAPE 1 (SIDE A) - SWAN-E" -- and serves the file under an upload name that
is often worse: Tape3SwanEandVibesandDougal.mp3, Tape1(A)-Ned_Ryder.mp3.
The caption is what the site says the file is, so it is the title. The upload
name is still useful as a spelling oracle: captions are frequently ALL CAPS,
and the upload name usually has the DJ's own casing (Swan-E, R-Type, DJ EZ).

Nothing here touches the network; it is pure string work so every name can be
previewed and tested before a single link reaches JDownloader.
"""
from __future__ import annotations

import html
import re
from urllib.parse import unquote, urlparse

# Characters Windows refuses in a path component (the library lives on D:).
_FORBIDDEN = re.compile(r'[<>"/\\|?*\x00-\x1f]')
_DASHES = re.compile(r"\s*[–—‒]\s*")
_SPACES = re.compile(r"\s+")

# Tokens that stay upper case when an ALL-CAPS caption is re-cased and the
# upload name offers no spelling of its own.
_KEEP_UPPER = {
    "DJ", "DJS", "MC", "MCS", "B2B", "B3B", "UK", "UKG", "NYE", "EZ", "SS",
    "LTJ", "NRG", "DMS", "GQ", "IC3", "CD", "DVD", "MP3", "II", "III", "IV",
    "VI", "VII", "VIII", "IX", "XI", "XII", "USA", "BBC", "KISS", "FM", "PA",
    "TV", "VIP", "AWOL", "D&B", "DNB", "RSA", "XTC", "KGB", "XCON",
}
_SMALL = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the",
          "to", "vs", "with", "&"}


def clean_text(text: str) -> str:
    """Entities decoded, dashes made plain, whitespace collapsed."""
    t = html.unescape(text or "").replace(" ", " ")
    t = t.replace("’", "'").replace("‘", "'")
    t = t.replace("“", '"').replace("”", '"')
    t = _DASHES.sub(" - ", t)
    # "ECHO / KROME" is two DJs; "1/2" is a fraction-like label.
    t = re.sub(r"\s+/\s+", " & ", t)
    t = re.sub(r"(?<=\S)/(?=\S)", "-", t)
    return _SPACES.sub(" ", t).strip()


def safe_component(name: str) -> str:
    """One path component that Windows and Linux both accept."""
    n = clean_text(name)
    n = n.replace(":", " -").replace('"', "'")
    n = _FORBIDDEN.sub("", n)
    n = _SPACES.sub(" ", n).strip().rstrip(". ")
    return n or "untitled"


def url_words(url: str) -> list[str]:
    """The upload name split into words: Tape_1_(Side_A)_-_Swan-E.mp3 ->
    [Tape, 1, Side, A, Swan-E]."""
    base = unquote(urlparse(url or "").path.rsplit("/", 1)[-1])
    base = html.unescape(base).rsplit(".", 1)[0]
    return [w for w in re.split(r"[\s_()\[\]]+|(?<!\w)-(?!\w)", base) if w]


def _case_word(word: str, spellings: dict[str, str], first: bool) -> str:
    key = word.lower()
    if key in spellings:
        return spellings[key]
    # Hyphenated names: R-TYPE -> R-Type, SWAN-E -> Swan-E.
    if "-" in word and not word.startswith("-"):
        return "-".join(_case_word(p, spellings, True) for p in word.split("-"))
    if word.upper() in _KEEP_UPPER or (len(word) == 1 and word.isalpha()):
        return word.upper()
    # Vowel-less short tokens are initials (RSA, RSR, LTJ, NRG): capitals.
    if (2 <= len(word) <= 4 and word.isalpha() and not re.search(r"[aeiouy]", key)
            and key not in {"mr", "dr", "st", "mrs", "vs", "nd", "rd", "th"}):
        return word.upper()
    if not first and key in _SMALL:
        return key
    if len(word) == 1:
        return word.upper()
    # Leading punctuation: (SIDE -> (Side
    m = re.match(r"^(\W*)(\w)(.*)$", word, re.S)
    if not m:
        return word
    return m.group(1) + m.group(2).upper() + m.group(3).lower()


def _is_shouting(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and not any(c.islower() for c in letters)


def recase(text: str, oracle_url: str = "") -> str:
    """Re-case an ALL-CAPS caption. Mixed-case captions are left as written,
    apart from a stray double capital (PIlgrim -> Pilgrim)."""
    spellings = {}
    words_up = url_words(oracle_url)
    upload_has_case = any(c.islower() for w in words_up for c in w)
    for w in words_up:
        # Only trust upload words written with deliberate casing: mixed case,
        # or capitals in an upload name that otherwise uses lower case (FX).
        if w.isdigit():
            continue
        if upload_has_case or w.upper() in _KEEP_UPPER:
            for part in [w] + w.split("-"):
                spellings.setdefault(part.lower(), part)
    words = text.split(" ")
    if _is_shouting(text):
        out = []
        for i, w in enumerate(words):
            core = w.strip("()[]")
            pre, post = w[:len(w) - len(w.lstrip("()["))], w[len(w.rstrip(")]")):]
            cased = _case_word(core, spellings, first=(i == 0 or out[-1:] == ["-"]))
            out.append(pre + cased + post)
        return " ".join(out)
    fixed = []
    for w in words:
        if re.fullmatch(r"[A-Z]{2}[a-z]{2,}", w):
            w = w[0] + w[1:].lower()
        fixed.append(w)
    return " ".join(fixed)


_TAPE = re.compile(r"\btape\s*(\d+)", re.I)
_SIDE = re.compile(r"\bside\s*([A-Z0-9]+)\b", re.I)


def tape_of(text: str) -> str:
    m = _TAPE.search(text or "")
    return m.group(1) if m else ""


# Caption typos confirmed against the upload name and the DJ's own name.
# Applied after re-casing; keep this list short and certain.
CORRECTIONS = {
    "Billy Hunter": "Billy Bunter",
}


def _correct(title: str) -> str:
    for wrong, right in CORRECTIONS.items():
        title = re.sub(rf"\b{re.escape(wrong)}\b", right, title)
    return title


def track_title(caption: str, url: str) -> str:
    """The caption, re-cased, plus anything the upload name knows that the
    caption leaves out (its tape number)."""
    cap = clean_text(caption)
    up = " ".join(url_words(url))
    if not cap:
        return safe_component(up)
    title = _correct(recase(cap, url))
    tape = tape_of(up)
    if tape and not tape_of(title):
        title = f"Tape {tape} - {title}"
    return safe_component(title)


def disagreement(caption: str, url: str) -> str:
    """Why the caption and the upload name seem to describe different files,
    or '' when they agree. Used for the review list, never to rename."""
    cap, up = clean_text(caption).lower(), " ".join(url_words(url)).lower()
    reasons = []
    ct, ut = tape_of(cap), tape_of(up)
    if ct and ut and ct != ut:
        reasons.append(f"tape {ct} vs upload tape {ut}")
    cs, us = _SIDE.search(cap), _SIDE.search(up)
    side = lambda v: {"a": "1", "b": "2", "c": "3", "d": "4"}.get(v.lower(), v.lower())
    if cs and us and side(cs.group(1)) != side(us.group(1)):
        reasons.append(f"side {cs.group(1)} vs upload side {us.group(1)}")
    # Name words: the DJ names in the caption should appear in the upload.
    stop = {"tape", "side", "dj", "and", "the", "b2b", "vs", "with", "mc",
            "part", "a", "b", "1", "2", "3", "4", "5", "6", "7", "8", "9"}
    squash = re.sub(r"[^a-z0-9]", "", up)
    missing = [w for w in re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)", "", cap))
               if w not in stop and len(w) > 2 and w not in squash]
    if missing:
        reasons.append("caption words not in upload name: " + ", ".join(missing))
    return "; ".join(reasons)


def file_name(index: int, total: int, caption: str, url: str) -> str:
    ext = urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else "mp3"
    width = max(2, len(str(total)))
    return f"{index:0{width}d} - {track_title(caption, url)}.{ext}"


def folder_name(title: str, year: str = "") -> str:
    t = clean_text(title)
    if _is_shouting(t):
        t = recase(t)
    t = safe_component(t)
    if year and year not in t:
        t = f"{t} ({year})"
    return safe_component(t)
