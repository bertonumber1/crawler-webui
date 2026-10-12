"""The polite fetcher for self-hosting sites (crawler/direct.py)."""
import importlib
import os

import httpx
import pytest

from crawler import direct, hosts

U = "https://www.ravetapepacks.com/music/P/Side_1_-_Hype.mp3"
REF = "https://www.ravetapepacks.com/?p=2609"
BODY = b"ID3" + b"x" * 5000


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_DIRECT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("CW_DB_PATH", str(tmp_path / "c.db"))
    monkeypatch.setenv("CW_OUTPUT_UID", "")       # no chown in tests
    from crawler import db
    importlib.reload(db)
    before = [h.key for h in hosts.enabled()]
    hosts.set_enabled(before + ["ravetapepacks"])
    yield tmp_path
    hosts.set_enabled(before)
    importlib.reload(db)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def row(**kw):
    return dict({"url": U, "folder": "Planet London Vol 1 (1997)",
                 "filename": "01 - Side 1 - DJ Hype.mp3", "referer": REF}, **kw)


def test_saves_under_the_folder_and_name_with_referer(env):
    seen = {}

    def h(req):
        seen["ref"] = req.headers.get("referer")
        return httpx.Response(200, content=BODY, headers={"content-type": "audio/mpeg"})

    ok, _ = direct.fetch_one(row(), client(h))
    path = env / "out" / "Planet London Vol 1 (1997)" / "01 - Side 1 - DJ Hype.mp3"
    assert ok and path.read_bytes() == BODY
    assert seen["ref"] == REF
    assert not os.path.exists(str(path) + ".part")


def test_resumes_a_partial_file_with_range(env):
    path = env / "out" / "Planet London Vol 1 (1997)" / "01 - Side 1 - DJ Hype.mp3"
    path.parent.mkdir(parents=True)
    (path.parent / (path.name + ".part")).write_bytes(BODY[:1000])

    def h(req):
        assert req.headers["range"] == "bytes=1000-"
        return httpx.Response(206, content=BODY[1000:], headers={"content-type": "audio/mpeg"})

    ok, _ = direct.fetch_one(row(), client(h))
    assert ok and path.read_bytes() == BODY


def test_an_html_page_is_never_saved_as_the_file(env):
    def h(req):
        return httpx.Response(200, content=b"<html>Access forbidden!</html>",
                              headers={"content-type": "text/html"})

    ok, detail = direct.fetch_one(row(), client(h))
    assert not ok and "HTML" in detail
    assert not (env / "out" / "Planet London Vol 1 (1997)" / "01 - Side 1 - DJ Hype.mp3").exists()


def test_a_finished_file_is_not_fetched_again(env):
    path = env / "out" / "Planet London Vol 1 (1997)" / "01 - Side 1 - DJ Hype.mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(BODY)

    def h(req):
        raise AssertionError("must not fetch")

    assert direct.fetch_one(row(), client(h)) == (True, "already on disk")


def test_403_is_a_failure_not_a_file(env):
    ok, detail = direct.fetch_one(row(), client(lambda req: httpx.Response(403)))
    assert not ok and detail == "HTTP 403"


def test_queue_never_holds_a_url_twice(env, monkeypatch):
    monkeypatch.setattr(direct, "ensure_worker", lambda: None)
    assert direct.enqueue([row()], "P") == 1
    assert direct.enqueue([row()], "P") == 0
    assert direct.status()["counts"] == {"queued": 1}


def test_send_to_a_direct_host_goes_to_the_fetcher_not_jd(env, monkeypatch):
    import crawler.server as srv
    from crawler import history
    importlib.reload(history)
    importlib.reload(srv)
    monkeypatch.setattr(srv.direct, "ensure_worker", lambda: None)
    monkeypatch.setattr(srv.jdapi, "add_named", lambda *a, **k: (_ for _ in ()).throw(AssertionError("JD")))
    path = srv._queue([U], "Planet London Vol 1 (1997)",
                      names={U: "01 - Side 1 - DJ Hype.mp3"}, source_url=REF)
    assert path == "direct:1"
    q = direct._load()
    assert q[0]["filename"] == "01 - Side 1 - DJ Hype.mp3" and q[0]["referer"] == REF
    assert q[0]["folder"] == "Planet London Vol 1 (1997)"


def test_nested_folder_lands_in_nested_dirs_and_cannot_escape(env):
    t = direct.target(row(folder="Rave Tape Packs/Helter Skelter/../../etc"))
    assert t == str(env / "out" / "Rave Tape Packs" / "Helter Skelter" / "etc"
                    / "01 - Side 1 - DJ Hype.mp3")
