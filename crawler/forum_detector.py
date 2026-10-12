"""Detect common forum/CMS software from page HTML.

The detector is deliberately conservative: it reports evidence and a
confidence score rather than pretending every page can be classified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable


@dataclass(frozen=True)
class Detection:
    software: str
    confidence: float
    evidence: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Signature:
    software: str
    patterns: tuple[tuple[str, str], ...]


SIGNATURES: tuple[Signature, ...] = (
    Signature("vBulletin", (
        ("meta generator", r'<meta[^>]+name=["\']generator["\'][^>]+content=["\'][^"\']*vbulletin'),
        ("vBulletin asset", r'(?:/clientscript/|/forumdisplay\.php|/showthread\.php)'),
        ("vBulletin class", r'\b(?:vbmenu|vbphrase|postbit|tborder)\b'),
    )),
    Signature("XenForo", (
        ("meta generator", r'<meta[^>]+name=["\']generator["\'][^>]+content=["\'][^"\']*xenforo'),
        ("XenForo asset", r'(?:/js/xf/|/styles/[^/]+/xenforo/)'),
        ("XenForo class", r'\b(?:p-body|block--messages|message-cell|structItem)\b'),
    )),
    # MyBB. Verified against patched.to, which serves
    #   <meta name="generator" content="mybb" />
    # and the classic trow1/trow2 table furniture. Its URLs are the rewritten
    # form (/Thread-slug, /Forum-slug) rather than showthread.php, so BOTH
    # shapes are matched -- a board with pretty URLs off still hits the second.
    Signature("MyBB", (
        ("meta generator", r'<meta[^>]+name=["\']generator["\'][^>]+content=["\'][^"\']*mybb'),
        ("MyBB asset", r'(?:/jscripts/|/showthread\.php|/forumdisplay\.php|href="[^"]*/(?:Thread|Forum)-)'),
        ("MyBB class", r'\b(?:trow1|trow2|inline_row|forumdisplay_regular|post_body|postbit)\b'),
    )),
    # Forumotion (foroactivo, forumactif, ...): hosted boards whose default
    # template borrows phpBB's class names, so on class evidence alone they
    # read as phpBB. They are not: topics are /t123-slug, boards /f12-slug,
    # and pages are offsets in the path (/f12p20-slug). Verified against
    # dark-decade.foroactivo.com, which carries all three markers below.
    Signature("Forumotion", (
        ("Forumotion host", r'(?:illiweb\.com|forumotion\.|foroactivo\.|forumactif\.)'),
        ("Forumotion script", r'\b_userdata\s*\['),
        ("Forumotion URL", r'href=["\'](?:https?://[^/"\']+)?/[tf]\d+-'),
    )),
    # SMF (Simple Machines). Verified against makinamania.net (SMF 2.0.19):
    # "Powered by SMF", smf_scripturl in the page script, and topic/board
    # links as index.php?topic=N.S -- or index.php/topic,N.S.html on the
    # cached guest copy of the same page.
    Signature("SMF", (
        ("SMF footer", r'powered by smf|simple machines'),
        ("SMF script", r'smf_scripturl|smf_theme_url|smf_default_theme_url'),
        ("SMF URL", r'index\.php(?:\?|/)(?:topic|board)[=,]\d+\.\d+'),
    )),
    Signature("phpBB", (
        ("meta generator", r'<meta[^>]+name=["\']generator["\'][^>]+content=["\'][^"\']*phpbb'),
        ("phpBB asset", r'(?:/styles/[^/]+/theme/|/viewtopic\.php|/viewforum\.php)'),
        ("phpBB class", r'\b(?:phpbb|forumbg|postbody|topiclist)\b'),
    )),
    Signature("Invision Community", (
        ("meta generator", r'<meta[^>]+name=["\']generator["\'][^>]+content=["\'][^"\']*(?:invision|ips community)'),
        ("Invision asset", r'(?:/applications/core/interface/|/applications/forums/|/uploads/\w+/)'),
        ("Invision class", r'\b(?:ipsLayout|ipsType_|ipsButton|ipsComment)\b'),
    )),
    Signature("Drupal", (
        ("meta generator", r'<meta[^>]+name=["\']generator["\'][^>]+content=["\'][^"\']*drupal'),
        ("Drupal asset", r'(?:/core/misc/drupal|/sites/default/files/|drupalSettings)'),
        ("Drupal marker", r'\b(?:drupalSettings|js-drupal-|node--\w+)\b'),
    )),
)


def _normalise(html: str) -> str:
    return html.lower()


def detect(html: str, *, signatures: Iterable[Signature] = SIGNATURES) -> list[Detection]:
    """Return detections ordered by confidence, strongest first."""
    text = _normalise(html or "")
    results: list[Detection] = []
    for signature in signatures:
        evidence = tuple(name for name, pattern in signature.patterns if re.search(pattern, text, re.I))
        if not evidence:
            continue
        # One strong marker is useful; multiple independent markers increase confidence.
        confidence = min(0.99, 0.45 + 0.2 * len(evidence))
        results.append(Detection(signature.software, confidence, evidence))
    return sorted(results, key=lambda item: (-item.confidence, item.software))


def best_match(html: str) -> Detection | None:
    """Return the strongest supported forum/CMS detection, if any."""
    matches = detect(html)
    return matches[0] if matches else None
