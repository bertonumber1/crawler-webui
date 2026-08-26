"""Nothing unusable may reach JDownloader, and nothing unusable may be
recorded as sent.

JD gives no feedback either way: it consumes the file, moves it to added/, and
says nothing about whether the contents made sense. So the check has to happen
before the file is written, and the sent-history has to refuse anything that
did not reduce to a real file identity -- a bogus row there suppresses nothing
while claiming to.
"""
import json
import os

import pytest

from crawler import crawljob, links


REAL = "https://rapidgator.net/file/abc123/VA-Thing-(CAT01)-FLAC.rar.html"


@pytest.fixture(autouse=True)
def watch(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_FOLDERWATCH", str(tmp_path))
    return tmp_path


def _write(urls, name="T"):
    return json.load(open(crawljob.write(urls, name)))[0]


def test_a_real_link_is_written():
    assert _write([REAL])["text"] == REAL


def test_garbage_is_refused_rather_than_queued():
    with pytest.raises(ValueError, match="no usable links"):
        crawljob.write(["not-a-url"], "T")


def test_a_host_we_do_not_harvest_is_refused():
    with pytest.raises(ValueError):
        crawljob.write(["https://nitroflare.com/view/xyz/a.rar"], "T")


def test_a_truncated_url_is_refused():
    with pytest.raises(ValueError):
        crawljob.write(["https://rapidgator.net/file/"], "T")


def test_good_links_survive_alongside_bad_ones():
    job = _write([REAL, "not-a-url", "https://nitroflare.com/view/x/a.rar"])
    assert job["text"] == REAL


def test_the_same_file_twice_is_written_once():
    job = _write([REAL, "https://rapidgator.net/file/abc123"])
    assert job["text"].count("rapidgator.net") == 1


def test_links_are_canonicalised_on_the_way_out():
    # Whatever form the UI held, JD receives the canonical URL.
    assert _write(["https://www.rapidgator.net/file/abc123"])["text"].startswith(
        "https://rapidgator.net/file/abc123")


def test_history_refuses_a_url_that_is_not_a_harvested_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_DB_PATH", str(tmp_path / "h.db"))
    import importlib
    from crawler import db as db_mod
    importlib.reload(db_mod)
    from crawler import history as hist
    importlib.reload(hist)
    db_mod.init(); db_mod.init_cache(); hist.init()
    try:
        assert hist.record_sent([{"url": "not-a-url"}]) == 0
        assert hist.counts() == {}
        assert hist.record_sent([{"url": REAL}]) == 1
    finally:
        importlib.reload(db_mod); importlib.reload(hist)
