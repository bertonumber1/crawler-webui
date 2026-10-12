from crawler.forum_parser import parse


CASES = {
    "vBulletin": '''<html><title>Music Releases</title><body><div class="tborder"><a href="/showthread.php?t=123">Album Release</a><p><a href="https://rapidgator.net/file/abc123">Rapidgator</a></p></div></body></html>''',
    "XenForo": '''<html><title>Releases</title><body><div class="structItem"><div class="structItem-title"><a href="/threads/album.123/">Album Release</a></div><a href="https://rapidgator.net/file/xyz789">Download</a></div></body></html>''',
    "phpBB": '''<html><title>Releases</title><body><div class="post"><a class="topictitle" href="/viewtopic.php?t=42">Album Release</a><a href="https://rapidgator.net/file/phpbb42">RG</a></div></body></html>''',
    "Invision Community": '''<html><title>Releases</title><body><div class="ipsDataItem"><a href="/topic/77-album/">Album Release</a><div class="ipsComment"><a href="https://rapidgator.net/file/ips77">RG</a></div></div></body></html>''',
}


def test_forum_strategies_harvest_rapidgator():
    for software, html in CASES.items():
        items, detection = parse(html, "https://forum.example/section")
        assert detection is not None
        assert detection.software == software
        assert items
        urls = [l["url"] for item in items for l in item.links]
        assert any("rapidgator.net/file/" in u for u in urls)


def test_forum_parser_dedupes_nested_candidates():
    html = '''<html><body><div class="structItem"><a href="/threads/a.1/">A</a><a href="https://rapidgator.net/file/a">RG</a><div class="message-cell--main"><a href="https://rapidgator.net/file/a">RG</a></div></div></body></html>'''
    items, detection = parse(html, "https://forum.example/")
    assert detection.software == "XenForo"
    urls = [l["url"] for item in items for l in item.links]
    assert urls.count("https://rapidgator.net/file/a") == 1


def test_unknown_theme_falls_back_to_main_content():
    html = '''<html><body><main><a href="https://rapidgator.net/file/fallback">RG</a></main></body></html>'''
    items, detection = parse(html, "https://forum.example/")
    assert detection is None
    assert len(items) == 0


# Forumotion (dark-decade.foroactivo.com). Its template reuses phpBB class
# names, so detection must prefer the Forumotion markers.
FORUMOTION_BOARD = '''<html><head><title>Sellos</title>
<script src="https://illiweb.com/rs3/lang/es.js"></script>
<script>_userdata["username"] = "x";</script></head><body>
<ul class="topiclist"><li class="row"><a class="topictitle" href="/t13055-various-makina-2006">Various - Makina 2006</a>
<a href="/t13055-various-makina-2006#52206">latest</a><a href="/u1049">poster</a></li></ul>
<a href="/f127p20-sellos">2</a></body></html>'''

FORUMOTION_TOPIC = '''<html><head><title>Makina 2006</title>
<script>_userdata["username"] = "x";</script></head><body>
<div class="post"><div class="postbody"><h2>Various - Makina 2006</h2>
<dl class="codebox hidecode"><dd><a href="https://mega.nz/folder/TuxFEb4I#d6Xpmi3Qng6SZd4qUIwQOA">MEGA</a></dd></dl>
<a href="/t13055-various-makina-2006?cvf%5B0%5D=a&eval=plus&p_vote=52206">+</a>
<a href="/t13055-various-makina-2006?watch=topic">watch</a></div></div></body></html>'''


def test_forumotion_detected_over_phpbb():
    from crawler.forum_detector import best_match
    d = best_match(FORUMOTION_BOARD)
    assert d.software == "Forumotion"


def test_forumotion_topic_keeps_hidecode_mega_link():
    items, d = parse(FORUMOTION_TOPIC, "https://x.foroactivo.com/t13055-various-makina-2006")
    urls = [l["url"] for i in items for l in i.links]
    assert "https://mega.nz/folder/TuxFEb4I#d6Xpmi3Qng6SZd4qUIwQOA" in urls


def test_forumotion_board_follows_topics_once_and_never_actions():
    from crawler import resolve
    base = "https://x.foroactivo.com/f127-sellos"
    items, _ = parse(FORUMOTION_BOARD, base)
    urls = [c["url"] for c in resolve.candidates(items, base)]
    assert urls == ["https://x.foroactivo.com/t13055-various-makina-2006"]

    # From a topic page, vote and watch links must never be followed.
    topic = "https://x.foroactivo.com/t13055-various-makina-2006"
    items, _ = parse(FORUMOTION_TOPIC.replace("mega.nz", "example.org"), topic)
    followed = [c["url"] for c in resolve.candidates(items, topic)]
    assert not any("eval=" in u or "watch=" in u for u in followed)


def test_forumotion_topic_url_shapes():
    from crawler.forum_parser import is_forumotion_topic
    assert is_forumotion_topic("https://x.foroactivo.com/t1-a")
    assert is_forumotion_topic("https://x.foroactivo.com/t1p15-a")
    assert not is_forumotion_topic("https://x.foroactivo.com/t1n-a")      # next topic
    assert not is_forumotion_topic("https://x.foroactivo.com/t1-a?watch=topic")


def test_forumotion_search_results_follow_topics_only():
    # Search hides the links (the hidecode box is stripped), so the topics
    # must be opened -- once each, without ?highlight, and never a profile.
    from crawler import resolve
    html = '''<html><head><script>_userdata["u"]=1;</script></head><body>
    <div class="post"><h2><a href="/t9-pont-aeri-trilogy?highlight=pont+aeri#5">Pont Aeri Trilogy</a></h2>
    <div class="postbody">DESCARGA <a href="/u126">poster</a> <a href="/f10-digital">board</a></div></div>
    <div class="post"><h2><a href="/t9-pont-aeri-trilogy?highlight=pont+aeri#7">again</a></h2>
    <div class="postbody"><a href="/tags/Pont">#Pont</a></div></div></body></html>'''
    base = "https://x.foroactivo.com/search?search_keywords=pont+aeri&show_results=posts"
    items, _ = parse(html, base)
    assert [c["url"] for c in resolve.candidates(items, base)] == [
        "https://x.foroactivo.com/t9-pont-aeri-trilogy"]
