from crawler import links, resolve
from crawler import crawljob


def test_rapidgator_is_explicit_download_host():
    x = links.classify("https://rapidgator.net/file/ABC123/example.zip")
    assert x["host"] == "rapidgator.net"
    assert x["label"] == "Rapidgator"
    assert x["downloadable"] is True
    assert x["bucket"] == "build"


def test_wrapper_page_resolves_to_rapidgator(monkeypatch):
    release = "https://lossless-music.org/download/123/test"
    wrapper = "https://flakattak.example/go/123"
    rapid = "https://rapidgator.net/file/ABC123/test.zip"

    pages = {
        release: f'<a href="{wrapper}">Download mirror</a>',
        wrapper: f'<a href="{rapid}">Rapidgator</a>',
    }

    def fake_get(url, conditional=False):
        return 200, pages[url], {"content-type": "text/html"}

    monkeypatch.setattr(resolve.fetch, "get", fake_get)
    monkeypatch.setattr(resolve.fetch, "resolve_final_url", lambda url: url)

    got = resolve.page_links(release)
    assert [x["url"] for x in got] == [rapid]


def test_crawljob_is_json_and_deduped(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_FOLDERWATCH", str(tmp_path))
    monkeypatch.setenv("CW_DOWNLOAD_ROOT", "/output/_CRAWLER")
    path = crawljob.write([
        "https://rapidgator.net/file/A",
        "https://rapidgator.net/file/A",
        "https://rapidgator.net/file/B",
    ], "Test Release")
    import json
    data = json.loads(open(path, encoding="utf-8").read())
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["autoConfirm"] == "TRUE"
    assert data[0]["autoStart"] == "FALSE"
    assert data[0]["text"].splitlines() == [
        "https://rapidgator.net/file/A",
        "https://rapidgator.net/file/B",
    ]
