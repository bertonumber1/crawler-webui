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
