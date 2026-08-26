"""Crawl memory: a file already handed to JD must never be offered twice."""
import os
import tempfile

import pytest


@pytest.fixture()
def store(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    monkeypatch.setenv("CW_DB_PATH", path)
    import importlib
    from crawler import db as db_mod
    importlib.reload(db_mod)
    from crawler import history as hist_mod
    importlib.reload(hist_mod)
    db_mod.init()
    db_mod.init_cache()
    hist_mod.init()
    yield hist_mod
    importlib.reload(db_mod)
    importlib.reload(hist_mod)


LONG = ("https://rapidgator.net/file/abc123/"
        "VA-Something-(CAT01)-CD-FLAC-1994.rar.html")
SHORT = "https://rapidgator.net/file/abc123"


def test_unknown_file_is_fresh(store):
    assert store.annotate([{"url": LONG}])[0]["seen_before"] is False


def test_sent_file_is_recognised_in_a_different_url_form(store):
    store.record_sent(store.annotate([{"url": LONG}]), package="P")
    row = store.annotate([{"url": SHORT}])[0]
    assert row["seen_before"] is True
    assert row["prior_state"] == store.SENT


def test_resending_does_not_duplicate_the_row(store):
    store.record_sent(store.annotate([{"url": LONG}]), package="P")
    store.record_sent(store.annotate([{"url": SHORT}]), package="P")
    assert store.counts() == {store.SENT: 1}


def test_forget_allows_a_file_to_be_queued_again(store):
    store.record_sent(store.annotate([{"url": LONG}]), package="P")
    assert store.forget(["rapidgator:abc123"]) == 1
    assert store.annotate([{"url": LONG}])[0]["seen_before"] is False


def test_mark_moves_state_without_losing_the_row(store):
    store.record_sent(store.annotate([{"url": LONG}]), package="P")
    store.mark(["rapidgator:abc123"], store.CONFIRMED)
    assert store.annotate([{"url": LONG}])[0]["prior_state"] == store.CONFIRMED
