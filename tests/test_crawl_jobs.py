"""Background crawl jobs, release memory and "stop when caught up"."""
import importlib
import threading
import time

import pytest
from fastapi.testclient import TestClient

RG = "https://rapidgator.net/file/aaa111/One-(C1)-FLAC.rar.html"


@pytest.fixture()
def env(tmp_path, monkeypatch):
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
            return [Item(id=url, name="Release " + url[-1], url=url, kind="release",
                         links=[{"url": RG + url[-1], "host": "rapidgator.net"}], store="stub")]

        def resolve(self, url):
            return url

        def check(self, url):
            return []

    base.register(Stub())
    yield srv, TestClient(srv.app), hist_mod
    importlib.reload(db_mod)
    importlib.reload(hist_mod)


def _wait(c, timeout=5):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get("/api/crawl/job").json()
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job never finished")


def test_job_crawls_several_urls_in_turn_and_merges(env):
    srv, c, _ = env
    r = c.post("/api/crawl/start", json={"url": "", "provider": "stub",
                                         "urls": ["https://s.test/a", "https://s.test/b"]})
    assert r.status_code == 200, r.text
    j = _wait(c)
    assert j["state"] == "done" and j["finished_urls"] == 2
    assert j["result"]["count"] == 2 and j["result"]["fresh"] == 2


def test_second_start_is_refused_and_stop_cancels(env, monkeypatch):
    srv, c, _ = env
    gate = threading.Event()

    def slow(inp, cancel=None):
        while not cancel.is_set():
            time.sleep(0.02)
        gate.set()
        return {"url": inp.url, "items": [], "stopped": True}

    monkeypatch.setattr(srv, "_run_crawl", slow)
    assert c.post("/api/crawl/start", json={"url": "https://s.test/a", "provider": "stub"}).status_code == 200
    assert c.post("/api/crawl/start", json={"url": "https://s.test/b", "provider": "stub"}).status_code == 409
    assert c.post("/api/crawl/job/stop").json()["ok"] is True
    assert _wait(c)["state"] == "stopped" and gate.is_set()


def test_release_is_done_only_when_every_file_was_sent(env):
    _, _, hist = env
    a = {"url": "https://rapidgator.net/file/x1/a.rar.html"}
    b = {"url": "https://rapidgator.net/file/x2/b.rar.html"}
    hist.remember_release("https://s.test/rel/1", "One", [a, b])
    assert hist.done_releases(["https://s.test/rel/1"]) == set()
    hist.record_sent([a, b], package="One")
    assert hist.done_releases(["https://s.test/rel/1/"]) == {"https://s.test/rel/1"}


def test_resolve_skips_done_releases_and_honours_cancel(env, monkeypatch):
    _, _, hist = env
    from crawler import resolve
    importlib.reload(resolve)
    from crawler.models import Item
    opened = []
    monkeypatch.setattr(resolve, "page_links",
                        lambda u: opened.append(u) or [{"url": RG + u[-1], "host": "rapidgator.net",
                                                        "downloadable": True}])
    listing = [Item(id="l", name="list", url="https://s.test/genre", kind="listing", store="web",
                    links=[{"url": f"https://s.test/download/rel-{i}", "host": "s.test",
                            "text": f"Rel {i} FLAC"} for i in (1, 2)])]
    done_link = {"url": RG + "1"}
    hist.remember_release("https://s.test/download/rel-1", "Rel 1", [done_link])
    hist.record_sent([done_link])
    res = resolve.resolve(listing, "https://s.test/genre", limit=10, skip_done=True)
    assert res["skipped_known"] == 1 and opened == ["https://s.test/download/rel-2"]
    stop = threading.Event(); stop.set()
    res = resolve.resolve(listing, "https://s.test/genre", limit=10, cancel=stop)
    assert res["cancelled"] and res["releases"] == []


def test_caught_up_only_when_every_release_on_the_page_is_done(env):
    srv, _, hist = env
    from crawler.models import Item
    page = [Item(id="l", name="list", url="https://s.test/genre", kind="listing", store="web",
                 links=[{"url": f"https://s.test/download/rel-{i}", "host": "s.test", "text": f"Rel {i}"}
                        for i in (1, 2)])]
    inp = srv.CrawlIn(url="https://s.test/genre")
    for i in (1, 2):
        link = {"url": f"https://rapidgator.net/file/z{i}/r.rar.html"}
        hist.remember_release(f"https://s.test/download/rel-{i}", "", [link])
        assert not srv._caught_up(page, inp)
        hist.record_sent([link])
    assert srv._caught_up(page, inp)
