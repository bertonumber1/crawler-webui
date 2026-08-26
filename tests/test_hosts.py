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
