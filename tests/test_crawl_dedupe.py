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


def test_send_each_queues_a_release_once(client, monkeypatch):
    c, _ = client
    from crawler import server as srv
    from crawler import jdapi
    monkeypatch.setattr(jdapi, "configured", lambda: False)
    calls = []
    monkeypatch.setattr(srv.crawljob, "write",
                        lambda urls, name, **kw: calls.append((name, list(urls))) or "/tmp/x.crawljob")
    d = _crawl(c, send_each=True)
    assert d["sent"]["packages"] == 1 and d["sent"]["links"] == 2
    assert calls[0][0] == "Release One"
    # Sent now, so a second crawl sends nothing again.
    d = _crawl(c, send_each=True)
    assert d["sent"]["packages"] == 0 and len(calls) == 1


def test_api_failure_falls_back_to_folderwatch(client, monkeypatch):
    c, _ = client
    from crawler import server as srv
    from crawler import jdapi
    monkeypatch.setattr(jdapi, "configured", lambda: True)
    def boom(*a, **k):
        raise RuntimeError("no JDownloader devices on this account")
    monkeypatch.setattr(jdapi, "add", boom)
    calls = []
    monkeypatch.setattr(srv.crawljob, "write",
                        lambda urls, name, **kw: calls.append(name) or "/tmp/x.crawljob")
    d = _crawl(c, send_each=True)
    assert d["sent"]["packages"] == 1 and d["sent"]["failed"] == 0
    assert calls == ["Release One"]


def test_send_each_sends_per_page_and_never_twice(client, monkeypatch):
    """A multi-page crawl sends each page's releases as that page is read, and
    the end-of-crawl pass does not send them again."""
    c, hist = client
    from crawler import server as srv
    from crawler.models import Item
    from crawler.providers import base

    pages = {"https://site.test/t?p=1": RG1, "https://site.test/t?p=2": RG2}

    class Paged:
        name = "paged"

        def _parse_html(self, body, url):
            return [Item(id=url, name="Rel " + url[-1], url=url, kind="release",
                         links=[{"url": pages[url], "host": "rapidgator.net"}], store="paged")]

        def poll(self, url, limit=50):
            return self._parse_html("", url)

        def resolve(self, url):
            return url

        def check(self, url):
            return []

    base.register(Paged())
    monkeypatch.setattr(srv.pagination, "walk",
                        lambda url, get, max_pages=0: iter([(u, "<html></html>") for u in pages]))
    sent = []
    monkeypatch.setattr(srv, "_queue", lambda urls, name, subfolder="", auto_start=False:
                        sent.append((name, tuple(urls))) or hist.record_sent(
                            [{"url": u, "host": "rapidgator.net"} for u in urls],
                            package=name, job_path="test", title=name))
    out = _crawl(c, provider="paged", pages=0, send_each=True)
    assert [n for n, _ in sent] == ["Rel 1", "Rel 2"], sent
    assert out["sent"]["packages"] == 2
