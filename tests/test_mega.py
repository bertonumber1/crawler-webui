"""MEGA links keep their decryption key.

mega.nz puts the key after the # -- it is not a page anchor, it is the only
thing that makes the folder openable. Stripping it, as the generic reducer
does for every other host, turns a good link into a locked box. This is the
one host where the fragment is load-bearing.
"""
import importlib
import os

import pytest


@pytest.fixture()
def h(monkeypatch):
    monkeypatch.setenv("CRAWLER_HOSTS", "rapidgator,mega")
    from crawler import hosts
    importlib.reload(hosts)
    yield hosts
    monkeypatch.delenv("CRAWLER_HOSTS", raising=False)
    importlib.reload(hosts)


FULL = "https://mega.nz/folder/DiwjnbiQ#6i3ES0isJCDvob6_MrVL2A"


def test_the_key_is_preserved(h):
    ident, canonical = h.reduce(FULL)
    assert canonical == FULL
    assert "#6i3ES0isJCDvob6_MrVL2A" in ident


def test_a_keyless_link_is_rejected(h):
    # A folder id with no key cannot be downloaded, so it must not be queued.
    assert h.reduce("https://mega.nz/folder/DiwjnbiQ") == (None, None)


def test_same_folder_different_key_is_a_different_grab(h):
    a, _ = h.reduce(FULL)
    b, _ = h.reduce("https://mega.nz/folder/DiwjnbiQ#adifferentkeyaltogether")
    assert a != b


def test_file_links_work_too(h):
    ident, canonical = h.reduce("https://mega.nz/file/AbC123#thekey")
    assert canonical == "https://mega.nz/file/AbC123#thekey"


def test_the_key_survives_extraction_and_dedupe(h):
    from crawler import links as L
    importlib.reload(L)
    html = f'<a href="{FULL}" rel="external">x</a>'
    out = L.dedupe_download_links(L.extract(html, [FULL]))
    assert [l["url"] for l in out] == [FULL]
