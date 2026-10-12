"""The date window: read a post's date on any forum family, never drop what can't be dated."""
import datetime as dt

from bs4 import BeautifulSoup

from crawler import postdates


def _soup(html):
    return BeautifulSoup(html, "lxml")


def test_smf_day_first_short_year():
    assert postdates.parse("« Respuesta #3 en: 11/04/25, 20:35:08 pm »") == dt.date(2025, 4, 11)


def test_today_and_yesterday_in_spanish(monkeypatch):
    monkeypatch.setattr(postdates, "_today", lambda: dt.date(2026, 10, 11))
    assert postdates.parse("Hoy a las 20:35:08") == dt.date(2026, 10, 11)
    assert postdates.parse("Ayer a las 10:00") == dt.date(2026, 10, 10)


def test_month_names_es_en_fr():
    assert postdates.parse("Sáb 14 Sep 2024 - 21:34") == dt.date(2024, 9, 14)
    assert postdates.parse("14 de abril de 2025") == dt.date(2025, 4, 14)
    assert postdates.parse("Apr 14, 2025") == dt.date(2025, 4, 14)
    assert postdates.parse("le 3 août 2025") == dt.date(2025, 8, 3)


def test_machine_readable_time_wins():
    s = _soup('<article><time datetime="2025-05-02T10:00:00+00:00">2 May</time><p>x</p></article>')
    assert postdates.post_date(s.article) == dt.date(2025, 5, 2)


def test_quoted_date_is_not_the_post_date():
    # SMF: the header is the keyinfo before the body; the 2018 date is a quote.
    html = ('<div class="keyinfo">« Respuesta #9 en: 20/04/25, 14:36:36 pm »</div>'
            '<div class="post"><blockquote><div class="topslice_quote">Cita de: yasharan en 02/11/21</div>'
            'old</blockquote> new text</div>')
    post = _soup(html).select_one("div.post")
    assert postdates.post_date(post) == dt.date(2025, 4, 20)


def test_undatable_post_is_never_too_old():
    assert postdates.post_date(_soup("<div class='post'>no date</div>").div) is None
    assert postdates.too_old(None, dt.date(2025, 1, 1)) is False


def test_cutoff_months():
    t = dt.date(2026, 10, 11)
    assert postdates.cutoff(18, today=t) == dt.date(2025, 4, 11)
    assert postdates.cutoff(0, today=t) is None
    assert postdates.cutoff(1, today=dt.date(2026, 3, 31)) == dt.date(2026, 2, 28)


def test_newest_reads_a_board_row():
    row = _soup("<tr><td>Topic</td><td>29/10/25, 22:50:04 pm por abcde</td></tr>").tr
    assert postdates.newest(row) == dt.date(2025, 10, 29)


def test_custom_theme_date_before_the_post_not_the_join_date():
    # makinamania.net's table theme: profile box (join date, no time), then the
    # header date in a plain div.smalltext, then the post.
    html = ('<table><tr><td class="windowbg2">'
            '<span class="smalltext">Registro: 22-08-18</span>'
            '<table><tr><td><div class="smalltext">« Respuesta #3 en: 25/08/26, 18:47:15 pm »</div></td></tr></table>'
            '<table><tr><td><div class="post">hello</div></td></tr></table>'
            '</td></tr></table>')
    post = _soup(html).select_one("div.post")
    assert postdates.post_date(post) == dt.date(2026, 8, 25)


def test_header_date_of_an_earlier_post_is_not_borrowed():
    html = ('<div class="smalltext">01/01/20, 10:00:00 am</div><div class="post">first</div>'
            '<div class="post">second, its header missing</div>')
    second = _soup(html).select("div.post")[1]
    assert postdates.post_date(second) is None
