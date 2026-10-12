"""The harvest set is configuration, not three copies of a hardcoded name."""
import importlib
import os

from crawler import hosts


def test_rapidgator_is_the_default():
    assert [h.key for h in hosts.enabled()] == ["rapidgator"]


def test_same_file_two_url_forms_reduce_to_one_identity():
    long_form = ("https://rapidgator.net/file/abc123/"
                 "VA-Something-(CAT01)-CD-FLAC-1994.rar.html")
    short_form = "https://rapidgator.net/file/abc123"
    assert hosts.reduce(long_form)[0] == hosts.reduce(short_form)[0] == "rapidgator:abc123"


def test_truncated_url_is_rejected_rather_than_queued():
    # A URL cut short by a bad extractor must not reach the downloader.
    identity, canonical = hosts.reduce("https://rapidgator.net/file/")
    assert identity is None and canonical is None


def test_disabled_host_is_not_harvested_but_is_still_recognised():
    assert not hosts.is_enabled("https://nitroflare.com/view/xyz/a.rar")
    assert "nitroflare.com" in hosts.ALL_DOMAINS


def test_set_enabled_ignores_unknown_keys_and_never_empties():
    try:
        hosts.set_enabled(["nitroflare", "not-a-host"])
        assert [h.key for h in hosts.enabled()] == ["nitroflare"]
        hosts.set_enabled(["nothing-valid"])
        assert [h.key for h in hosts.enabled()] == ["nitroflare"]
    finally:
        hosts.set_enabled(["rapidgator"])


def test_cloud_hosts_recognised_under_every_domain():
    try:
        hosts.set_enabled(hosts.CLOUD)
        for url in (
            "https://mega.nz/folder/TuxFEb4I#d6Xpmi3Qng6SZd4qUIwQOA",
            "https://1024terabox.com/s/1x0TRu54CHknzwSC929Kd4A",
            "https://www.terabox.com/s/1x0TRu54CHknzwSC929Kd4A",
            "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing",
            "https://www.dropbox.com/scl/fi/abc/file.rar?dl=0",
            "https://1drv.ms/u/s!AbCdEf",
            "https://yadi.sk/d/abcdef",
            "https://u.pcloud.link/publink/show?code=XZabc",
        ):
            assert hosts.is_enabled(url), url
        assert "rapidgator" in hosts.CLOUD and "nitroflare" not in hosts.CLOUD
    finally:
        hosts.set_enabled(["rapidgator"])


def test_google_drive_url_forms_collapse_to_one_file():
    try:
        hosts.set_enabled(["gdrive"])
        forms = (
            "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing",
            "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp",
            "https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOp&export=download",
            "https://docs.google.com/uc?export=download&id=1AbCdEfGhIjKlMnOp",
        )
        assert {hosts.reduce(u)[0] for u in forms} == {"gdrive:1AbCdEfGhIjKlMnOp"}
        folder = hosts.reduce("https://drive.google.com/drive/folders/1FoLdErIdXyZ123?usp=share_link")
        assert folder == ("gdrive:1FoLdErIdXyZ123",
                          "https://drive.google.com/drive/folders/1FoLdErIdXyZ123")
    finally:
        hosts.set_enabled(["rapidgator"])


def test_cloud_keyword_from_env(monkeypatch):
    monkeypatch.setenv("CRAWLER_HOSTS", "cloud")
    assert hosts._configured() == hosts.CLOUD


def test_a_hosts_home_or_language_page_is_not_a_file():
    from crawler import hosts, links
    with_hosts = [h.key for h in hosts.enabled()]
    try:
        hosts.set_enabled(list(hosts.CLOUD))
        assert not links.is_download_host("https://www.swisstransfer.com/es")
        assert not links.is_download_host("https://mega.nz/")
        assert not links.is_download_host("https://www.mediafire.com/about")
        assert links.is_download_host("https://www.swisstransfer.com/d/6113e480-547a-4e55")
        assert links.is_download_host("https://mega.co.nz/#!aJZ2xTTD!W_mXkx1yOWOPpAxhm96Bv")
        assert links.is_download_host("http://www.mediafire.com/?0eylv2y3j1x")
    finally:
        hosts.set_enabled(with_hosts)
