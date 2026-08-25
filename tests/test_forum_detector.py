from crawler.forum_detector import best_match, detect


def test_detect_vbulletin():
    html = '<html><head><meta name="generator" content="vBulletin 4.2"></head><body><div class="tborder">x</div></body></html>'
    match = best_match(html)
    assert match and match.software == "vBulletin"
    assert match.confidence >= 0.65


def test_detect_xenforo():
    html = '<html><head><meta name="generator" content="XenForo"></head><body><div class="p-body">x</div></body></html>'
    match = best_match(html)
    assert match and match.software == "XenForo"


def test_detect_phpbb():
    html = '<html><head><meta name="generator" content="phpBB"></head><body><div class="forumbg">x</div></body></html>'
    match = best_match(html)
    assert match and match.software == "phpBB"


def test_detect_invision():
    html = '<html><body><div class="ipsLayout"><button class="ipsButton">x</button></div></body></html>'
    match = best_match(html)
    assert match and match.software == "Invision Community"


def test_detect_drupal():
    html = '<html><head><meta name="Generator" content="Drupal 10"></head><body><script>var drupalSettings = {};</script></body></html>'
    match = best_match(html)
    assert match and match.software == "Drupal"


def test_unknown_returns_no_match():
    assert detect('<html><body><h1>Plain page</h1></body></html>') == []


def test_multiple_evidence_is_ranked_higher():
    html = '<div class="tborder"><a href="/showthread.php?t=1">thread</a></div>'
    match = best_match(html)
    assert match and match.software == "vBulletin"
    assert set(match.evidence) == {"vBulletin asset", "vBulletin class"}
