"""SMF (Simple Machines) boards, as makinamania.net serves them.

Synthetic pages with the real structure: no session ids or member names.
"""
import datetime as dt

import pytest

from crawler import forum_detector, forum_parser, hosts, pagination, postdates, resolve

BASE = "https://www.mm.test"


@pytest.fixture(autouse=True)
def cloud_hosts():
    """The live crawler runs CRAWLER_HOSTS=cloud: MEGA, Pixeldrain and the rest."""
    before = [h.key for h in hosts.enabled()]
    hosts.set_enabled(list(hosts.CLOUD))
    yield
    hosts.set_enabled(before)


def _post(n, date, body, sig=""):
    return (f'<div class="keyinfo"><div class="smalltext">« <strong>Respuesta #{n} en:</strong> {date} »</div></div>'
            f'<div class="post"><div class="inner" id="msg_{n}">{body}</div></div>'
            f'<div class="signature">{sig}</div>')


def _topic(posts, offsets, topic=189337, guest=False):
    def link(o):
        return (f"{BASE}/index.php/topic,{topic}.{o}.html" if guest
                else f"{BASE}/index.php?topic={topic}.{o}")
    pager = " ".join(f'<a class="navPages" href="{link(o)}">{o // 15 + 1}</a>' for o in offsets)
    return ('<html><head><link rel="next" href="' + BASE + f'/index.php?topic={topic}.0;prev_next=next" />'
            '<script>var smf_scripturl = "x";</script></head><body>'
            f'<div class="pagelinks">Páginas: {pager}</div>' + "".join(posts) +
            '<div id="footer">Powered by SMF 2.0.19</div></body></html>')


def test_detects_smf():
    html = _topic([_post(1, "11/04/25, 20:35:08 pm", "x")], [0, 15])
    assert forum_detector.best_match(html).software == "SMF"


def test_both_link_spellings_and_no_actions():
    assert forum_parser.smf_id(f"{BASE}/index.php?topic=189337.16290") == ("topic", 189337, 16290)
    assert forum_parser.smf_id(f"{BASE}/index.php/topic,189337.16290.html") == ("topic", 189337, 16290)
    assert forum_parser.smf_id(f"{BASE}/index.php?board=52.40") == ("board", 52, 40)
    # The guest copy appends the session id after .html
    assert forum_parser.smf_id(f"{BASE}/index.php/topic,120289.0.html?PHPSESSID=iqtae5") == ("topic", 120289, 0)
    # A jump to one message, the NEXT TOPIC link and any action are not pages.
    assert forum_parser.smf_id(f"{BASE}/index.php?topic=189337.msg1065#new") is None
    assert forum_parser.smf_id(f"{BASE}/index.php?topic=189337.0;prev_next=next") is None
    assert forum_parser.smf_id(f"{BASE}/index.php?action=logout;sesc=0123456789abcdef0123456789abcdef") is None
    assert forum_parser.smf_topic_url(f"{BASE}/index.php/topic,5.30.html") == f"{BASE}/index.php?topic=5.0"


def test_next_page_is_the_next_offset_never_rel_next():
    html = _topic([], [0, 15, 30, 45])
    assert pagination.next_page(html, f"{BASE}/index.php?topic=189337.15") == f"{BASE}/index.php?topic=189337.30"
    # The cached guest copy spells its pager the other way.
    guest = _topic([], [0, 15, 30], guest=True)
    assert pagination.next_page(guest, f"{BASE}/index.php?topic=189337.15") == f"{BASE}/index.php?topic=189337.30"
    assert pagination.next_page(html, f"{BASE}/index.php?topic=189337.45") is None


def test_signatures_are_not_releases_and_old_posts_are_skipped():
    html = _topic([
        _post(1, "03/07/18, 21:40:28 pm", 'old <a href="https://mega.nz/folder/OLD#k">x</a>'),
        _post(2, "20/04/25, 14:36:36 pm",
              'https://www.discogs.com/es/release/2599296-Various-Dance-2006 '
              '<a href="https://mega.nz/folder/NEW#k">x</a>',
              sig='<a href="https://mega.nz/file/SIG#k">my sig</a>'),
    ], [0])
    items, det = forum_parser.parse(html, f"{BASE}/index.php?topic=189337.0", since=dt.date(2025, 4, 11))
    urls = {l["url"] for i in items for l in i.links}
    assert det.software == "SMF"
    assert any("NEW" in u for u in urls)
    assert not any("OLD" in u for u in urls), "post from 2018 is outside the window"
    assert not any("SIG" in u for u in urls), "signature links are never harvested"
    assert items[0].name == "Various Dance 2006"


def test_quoted_old_date_does_not_make_a_new_post_old():
    body = ('<blockquote><div class="topslice_quote">Cita de: yasharan en 02/11/21, 21:52:07 pm</div>'
            'quoted</blockquote> Ripeo propio flac <a href="https://pixeldrain.com/u/abc">x</a>')
    html = _topic([_post(3, "20/04/25, 18:01:03 pm", body)], [0])
    items, _ = forum_parser.parse(html, f"{BASE}/index.php?topic=1.0", since=dt.date(2025, 4, 11))
    assert items and items[0].name == "Ripeo propio flac"


def test_window_reads_newest_page_back_and_stops(monkeypatch):
    pages = {
        0: _topic([_post(1, "01/01/20, 10:00:00 am", "a")], [0, 15, 30, 45]),
        15: _topic([_post(16, "05/04/25, 10:00:00 am", "b"), _post(17, "15/04/25, 10:00:00 am", "c")], [0, 15, 30, 45]),
        30: _topic([_post(31, "01/06/25, 10:00:00 am", "d")], [0, 15, 30, 45]),
        45: _topic([_post(46, "Hoy a las 10:00:00", "e")], [0, 15, 30, 45]),
    }
    fetched = []

    def fake_get(url, conditional=True):
        off = forum_parser.smf_id(url)[2]
        fetched.append(off)
        return 200, pages[off], {}

    monkeypatch.setattr(resolve.fetch, "get", fake_get)
    got = [forum_parser.smf_id(u)[2] for u, _ in resolve.smf_window_pages(
        f"{BASE}/index.php?topic=189337.0", dt.date(2025, 4, 11), resume=False)]
    # Page 0 is read once for the pager, then 45 -> 30 -> 15; 15 holds a
    # pre-window post, so the walk ends there and never re-reads page 0.
    assert got == [45, 30, 15]
    assert fetched == [0, 45, 30, 15]


def test_board_row_older_than_window_is_skipped():
    html = ('<html><body><div id="footer">Powered by SMF</div><table class="table_grid"><tbody>'
            f'<tr><td><a href="{BASE}/index.php?topic=10.0">Old topic</a></td><td>29/10/23, 22:50:04 pm por x</td></tr>'
            f'<tr><td><a href="{BASE}/index.php?topic=11.0">New topic</a></td><td>Hoy a las 20:35:08 por y</td></tr>'
            '</tbody></table></body></html>')
    items, _ = forum_parser.parse(html, f"{BASE}/index.php?board=73.0", since=dt.date(2025, 4, 11))
    urls = {l["url"] for i in items for l in i.links}
    assert any("topic=11.0" in u for u in urls) and not any("topic=10.0" in u for u in urls)


def test_a_reread_thread_resumes_from_where_it_stopped(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv("CW_DB_PATH", str(tmp_path / "c.db"))
    from crawler import db as db_mod
    importlib.reload(db_mod)
    db_mod.init(); db_mod.init_settings()
    pages = {o: _topic([_post(o + 1, "Hoy a las 10:00:00", "x")], [0, 15, 30, 45, 60]) for o in (0, 15, 30, 45, 60)}
    monkeypatch.setattr(resolve.fetch, "get", lambda url, conditional=True: (200, pages[forum_parser.smf_id(url)[2]], {}))
    url = f"{BASE}/index.php?topic=189337.0"
    first = [forum_parser.smf_id(u)[2] for u, _ in resolve.smf_window_pages(url, dt.date(2025, 4, 11))]
    assert first == [60, 45, 30, 15, 0]
    assert resolve.smf_mark(url) == 60
    # Two pages later: only the new pages and the old last page are read.
    pages.update({o: _topic([_post(o + 1, "Hoy a las 11:00:00", "y")], [0, 15, 30, 45, 60, 75, 90]) for o in (0, 75, 90)})
    again = [forum_parser.smf_id(u)[2] for u, _ in resolve.smf_window_pages(url, dt.date(2025, 4, 11))]
    assert again == [90, 75, 60]
    assert resolve.smf_mark(url) == 90
    importlib.reload(db_mod)


def _row(i, title, starter, replies, date="Hoy a las 10:00:00"):
    return (f'<tr><td class="windowbg4"></td><td class="windowbg3"><a href="{BASE}/index.php?topic={i}.0">{title}</a></td>'
            f'<td class="windowbg4"><a href="{BASE}/index.php?action=profile;u={abs(hash(starter)) % 999}">{starter}</a></td>'
            f'<td class="windowbg3">{replies}</td><td class="windowbg3">1.234</td>'
            f'<td class="windowbg4">{date} por <a href="{BASE}/index.php?action=profile;u=1">x</a></td></tr>')


def test_bot_floods_are_not_opened_but_real_topics_are():
    rows = [_row(100 + i, f"Crack {i} 2025", "Romdastt", 0) for i in range(8)]
    rows += [_row(200, "RECOPILATORIOS 2000's", "Pinhead", 19018),
             _row(201, "Pont Aeri Vol.V Take A Trip CDM", "uploader", 0),
             _row(202, "Gitta - No More Turning Back", "uploader", 0)]
    html = ('<html><body><div id="footer">Powered by SMF</div><table class="bordercolor">'
            + "".join(rows) + "</table></body></html>")
    items, _ = forum_parser.parse(html, f"{BASE}/index.php?board=73.0", since=dt.date(2025, 4, 11))
    names = [i.name for i in items]
    assert not any(n.startswith("Crack") for n in names)
    assert {"RECOPILATORIOS 2000's", "Pont Aeri Vol.V Take A Trip CDM", "Gitta - No More Turning Back"} <= set(names)
