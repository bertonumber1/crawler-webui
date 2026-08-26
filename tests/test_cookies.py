"""Crawling as a member rather than a guest.

A members-only board renders the thread to a guest and hides the links inside
it, so an anonymous crawl returns zero and looks exactly like a board with
nothing on it. These pin the parts that make the difference.
"""
import http.cookiejar
import importlib

import pytest


@pytest.fixture()
def ck(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_COOKIES_FILE", str(tmp_path / "cookies.txt"))
    from crawler import cookies as mod
    importlib.reload(mod)
    yield mod
    importlib.reload(mod)


SAMPLE = (
    "seriousaboutsound.net\tFALSE\t/\tTRUE\t0\tips4_member_id\t1234\n"
    ".example.org\tTRUE\t/\tFALSE\t0\tsession\tabc\n"
)


def test_no_file_is_not_an_error(ck):
    st = ck.status()
    assert st["loaded"] is False and st["error"] == ""


def test_pasted_text_without_a_header_is_still_accepted(ck):
    # Browser extensions vary; requiring the magic first line would reject
    # perfectly good exports for a cosmetic reason.
    st = ck.save_text(SAMPLE)
    assert st["loaded"] is True and st["count"] == 2


def test_domains_are_reported_for_the_ui(ck):
    ck.save_text(SAMPLE)
    assert ck.domains() == ["example.org", "seriousaboutsound.net"]


def test_have_for_matches_host_and_subdomains(ck):
    ck.save_text(SAMPLE)
    assert ck.have_for("https://seriousaboutsound.net/topic/1-x/") is True
    assert ck.have_for("https://www.example.org/a") is True
    assert ck.have_for("https://elsewhere.test/a") is False


def test_session_cookies_with_no_expiry_survive_loading(ck):
    # The cookie proving you are signed in usually has no expiry set. Loading
    # with the defaults discards exactly that one.
    ck.save_text("site.test\tFALSE\t/\tFALSE\t0\tsid\txyz\n")
    assert ck.status()["count"] == 1


def test_clear_forgets_and_removes_the_file(ck):
    ck.save_text(SAMPLE)
    st = ck.clear()
    assert st["loaded"] is False and st["domains"] == []
    assert ck.have_for("https://seriousaboutsound.net/") is False


def test_a_malformed_file_reports_rather_than_raises(ck, tmp_path):
    p = tmp_path / "cookies.txt"
    p.write_text("# Netscape HTTP Cookie File\nthis is not a cookie line at all\n")
    st = ck.load(str(p))
    assert st["loaded"] is False and st["error"]
