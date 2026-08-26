"""Reading JD's own lists, rather than trusting that a crawljob was accepted."""
import json
import os
import tempfile
import zipfile

import pytest

from crawler import jdstate


def _write_list(dirpath, name, urls, package="Pkg"):
    path = os.path.join(dirpath, name)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("00", json.dumps({"name": package, "downloadFolder": "/output/x"}))
        for i, u in enumerate(urls):
            z.writestr(f"00_{i:02d}", json.dumps({"downloadLink": {"url": u}}))
        z.writestr("extraInfo", json.dumps({"rootPath": "/config"}))
    return path


@pytest.fixture()
def cfg(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("CW_JD_CFG", d)
    return d


RG = "https://rapidgator.net/file/deadbeef/Thing-(CAT).rar.html"


def test_missing_dir_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("CW_JD_CFG", "/nowhere/at/all")
    snap = jdstate.snapshot()
    assert snap["ok"] is False and "not found" in snap["detail"]


def test_urls_are_read_as_canonical_file_identities(cfg):
    _write_list(cfg, "linkcollector7.zip", [RG])
    snap = jdstate.snapshot()
    assert snap["ok"] is True
    assert snap["linkgrabber"] == ["rapidgator:deadbeef"]


def test_highest_numbered_list_wins_not_the_first_found(cfg):
    _write_list(cfg, "linkcollector9.zip", [RG])
    _write_list(cfg, "linkcollector10.zip", [])
    snap = jdstate.snapshot()
    assert snap["linkgrabber_file"] == "linkcollector10.zip"
    assert snap["linkgrabber"] == []


def test_a_corrupt_archive_yields_nothing_rather_than_raising(cfg):
    with open(os.path.join(cfg, "downloadList3.zip"), "wb") as f:
        f.write(b"not a zip at all")
    assert jdstate.snapshot()["downloads"] == []


def test_packages_are_reported(cfg):
    _write_list(cfg, "downloadList4.zip", [RG], package="My Album")
    assert any(p["name"] == "My Album" for p in jdstate.snapshot()["packages"])
