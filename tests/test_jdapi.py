"""The MyJDownloader client, exercised without a JDownloader.

The value of the API over reading save files is that it answers at the time
and can act, so the things worth pinning are: it stays out of the way when it
is not configured, it sends the query shape the device actually accepts, and
it turns JD's own OFFLINE verdict into the file identities the crawler
suppresses on.
"""
import importlib

import pytest

from crawler import jdapi


RG = "https://rapidgator.net/file/abc123/Thing-(CAT01)-FLAC.rar.html"
DEAD = "https://rapidgator.net/file/def456/Gone.rar.html"


@pytest.fixture(autouse=True)
def no_creds(monkeypatch):
    for k in ("CW_MYJD_EMAIL", "CW_MYJD_PASSWORD", "CW_MYJD_DEVICE"):
        monkeypatch.delenv(k, raising=False)
    jdapi._device = None


def test_absent_credentials_are_reported_not_raised():
    assert jdapi.configured() is False
    s = jdapi.status()
    assert s["ok"] is False and s["configured"] is False


def test_credentials_are_read_at_call_time(monkeypatch):
    # Binding these at import would capture whatever .env had loaded first.
    assert jdapi.configured() is False
    monkeypatch.setenv("CW_MYJD_EMAIL", "a@b.c")
    monkeypatch.setenv("CW_MYJD_PASSWORD", "x")
    assert jdapi.configured() is True


def test_package_uuid_filter_is_a_list_not_a_boolean():
    """A boolean here makes the device reject the whole query.

    JD answers BAD_PARAMETERS and the entire snapshot fails, which shows up
    as a silent fall back to reading save files rather than as an error.
    """
    field = jdapi.LINK_FIELDS[0]
    assert isinstance(field["packageUUIDs"], list)
    assert field["packageUUIDs"] == []


def test_snapshot_turns_offline_links_into_file_identities(monkeypatch):
    rows = {
        "links": [
            {"uuid": 1, "url": RG, "availability": "ONLINE", "bytesTotal": 10},
            {"uuid": 2, "url": DEAD, "availability": "OFFLINE"},
        ],
        "packages": [{"uuid": 9, "name": "P", "saveTo": "/out", "childCount": 2}],
    }
    monkeypatch.setattr(jdapi, "linkgrabber", lambda: rows)
    monkeypatch.setattr(jdapi, "downloads", lambda: {"links": [], "packages": []})

    snap = jdapi.snapshot()
    assert snap["linkgrabber"] == ["rapidgator:abc123", "rapidgator:def456"]
    assert snap["offline"] == ["rapidgator:def456"]
    assert snap["packages"][0]["name"] == "P"


def test_add_refuses_an_empty_list():
    with pytest.raises(ValueError):
        jdapi.add([], "P")


def test_links_are_joined_with_newlines_for_the_device(monkeypatch):
    sent = {}

    def fake_call(fn, *a, **kw):
        class Dev:
            class linkgrabber:
                @staticmethod
                def add_links(jobs):
                    sent["jobs"] = jobs
                    return {"id": 42}
        return fn(Dev())

    monkeypatch.setattr(jdapi, "_call", fake_call)
    res = jdapi.add([RG, DEAD], "Pack", folder="/out/Pack")
    assert res["job"] == 42 and res["count"] == 2
    job = sent["jobs"][0]
    assert job["links"] == RG + "\n" + DEAD
    assert job["packageName"] == "Pack" and job["destinationFolder"] == "/out/Pack"
    assert job["autostart"] is False
