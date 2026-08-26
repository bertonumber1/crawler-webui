"""The crawl endpoint must not offer a file it has already sent.

Proving this against a live site is slow and depends on a challenge solver, so
it is proved here against a stub provider: the point under test is the
endpoint's own filtering, not the network.
"""
import importlib

import pytest
from fastapi.testclient import TestClient


RG1 = "https://rapidgator.net/file/aaa111/One-(C1)-FLAC.rar.html"
RG2 = "https://rapidgator.net/file/bbb222/Two-(C2)-FLAC.rar.html"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_DB_PATH", str(tmp_path / "c.db"))
    monkeypatch.setenv("CW_FOLDERWATCH", str(tmp_path))
    from crawler import db as db_mod
    importlib.reload(db_mod)
    from crawler import history as hist_mod
    importlib.reload(hist_mod)
    from crawler import server as srv
    importlib.reload(srv)

    from crawler.models import Item
    from crawler.providers import base

    class Stub:
        name = "stub"

        def poll(self, url, limit=50):
            return [Item(id="r1", name="Release One", url=url, kind="release",
                         links=[{"url": RG1, "host": "rapidgator.net"},
                                {"url": RG2, "host": "rapidgator.net"}],
                         store="stub")]

        def resolve(self, url):
            return url

        def check(self, url):
            return []

    base.register(Stub())
    yield TestClient(srv.app), hist_mod
    importlib.reload(db_mod)
    importlib.reload(hist_mod)


def _crawl(c, **kw):
    body = {"url": "https://site.test/tag/x", "provider": "stub"}
    body.update(kw)
    r = c.post("/api/crawl", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_first_crawl_reports_everything_as_new(client):
    c, _ = client
    d = _crawl(c)
    assert d["links"] == 2 and d["fresh"] == 2 and d["already_sent"] == 0
    assert len(d["items"][0]["links"]) == 2


def test_a_sent_file_is_withheld_from_the_next_crawl(client):
    c, hist = client
    hist.record_sent([{"url": RG1, "host": "rapidgator.net"}], package="P")
    d = _crawl(c)
    assert d["already_sent"] == 1 and d["fresh"] == 1
    assert [l["url"] for l in d["items"][0]["links"]] == [RG2]


def test_a_release_wholly_sent_disappears_rather_than_showing_empty(client):
    c, hist = client
    for u in (RG1, RG2):
        hist.record_sent([{"url": u, "host": "rapidgator.net"}], package="P")
    d = _crawl(c)
    assert d["count"] == 0 and d["fresh"] == 0 and d["already_sent"] == 2


def test_include_seen_shows_them_again_marked_as_sent(client):
    c, hist = client
    hist.record_sent([{"url": RG1, "host": "rapidgator.net"}], package="P")
    d = _crawl(c, include_seen=True)
    links = {l["url"]: l for l in d["items"][0]["links"]}
    assert links[RG1]["seen_before"] is True and links[RG1]["prior_state"] == "sent"
    assert links[RG2]["seen_before"] is False


def test_every_link_carries_a_canonical_identity(client):
    c, _ = client
    d = _crawl(c)
    keys = [l["file_key"] for l in d["items"][0]["links"]]
    assert keys == ["rapidgator:aaa111", "rapidgator:bbb222"]
