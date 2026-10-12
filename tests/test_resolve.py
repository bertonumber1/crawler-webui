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


def test_lossless_only_picks_topics_by_title_before_opening():
    from crawler.resolve import looks_lossless
    assert looks_lossless("", "/t12293-lossless-pont-aeri-10-anos")
    assert looks_lossless("", "/t3223-wav-pont-aeri-trilogy-bit-music-32-815")
    assert looks_lossless("", "/t1642-flac-va-the-rave-master-vol-3-20004cdflac")
    assert not looks_lossless("", "/t13049-lossy-3-djs-ahead-trip-to-shadow")
    assert not looks_lossless("", "/t9633-mp3-va-the-megarave-live-at-pont-aeri")
    assert not looks_lossless("", "/t2099-se-buscan-en-lossless-pastis-buenri")
    assert not looks_lossless("", "/t9629-peticiones-buscar-the-megarave")
    assert not looks_lossless("", "/t55-pont-aeri-trilogy")          # format unknown


def test_on_release_fires_per_topic(monkeypatch):
    from crawler import resolve
    monkeypatch.setattr(resolve, "page_links",
                        lambda u: [{"url": "https://mega.nz/folder/a#k", "downloadable": True}])
    items = [{"name": "board", "links": [
        {"url": "https://x.foroactivo.com/t1-flac-a", "host": "x.foroactivo.com"},
        {"url": "https://x.foroactivo.com/t2-flac-b", "host": "x.foroactivo.com"}]}]
    seen = []
    resolve.resolve(items, "https://x.foroactivo.com/f1-board",
                    on_release=lambda rel: seen.append(rel["url"]))
    assert sorted(seen) == ["https://x.foroactivo.com/t1-flac-a",
                            "https://x.foroactivo.com/t2-flac-b"]


def test_lossless_only_adapts_when_titles_never_say_the_format():
    from crawler.resolve import candidates
    base = "https://www.mm.test/index.php?board=73.0"
    def item(*titles):
        return [{"name": "board", "links": [{"url": f"https://www.mm.test/index.php?topic={i}.0", "text": t,
                                              "host": "mm.test"} for i, t in enumerate(titles, 1)]}]
    # makinamania: no title names a format -> open all but MP3/requests
    got = candidates(item("RECOPILATORIOS 2000's", "[SELLO] Brainstorm (1992-1995) MP3",
                          "[Peticion] Saga Maquina Total en Flac", "[DISCOTECA] Coliseum"),
                     base, lossless_only=True)
    assert [c["url"][-9:] for c in got] == ["topic=1.0", "topic=4.0"]
    # dark-decade style: titles say the format -> only lossless ones, as before
    got = candidates(item("Pont Aeri [FLAC]", "Megarave [MP3]", "Trilogy"), base, lossless_only=True)
    assert [c["url"][-9:] for c in got] == ["topic=1.0"]
