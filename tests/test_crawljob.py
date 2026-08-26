import json
import os
import tempfile
import unittest

from crawler import crawljob


class CrawlJobTests(unittest.TestCase):
    def test_default_job_auto_confirms_without_autostart(self):
        with tempfile.TemporaryDirectory() as watch:
            old = os.environ.get("CW_FOLDERWATCH")
            os.environ["CW_FOLDERWATCH"] = watch
            try:
                path = crawljob.write(
                    ["https://rapidgator.net/file/example"],
                    "Example release",
                )
                with open(path, encoding="utf-8") as fh:
                    job = json.load(fh)[0]
                self.assertEqual(job["autoConfirm"], "TRUE")
                self.assertEqual(job["autoStart"], "FALSE")
                self.assertEqual(job["text"], "https://rapidgator.net/file/example")
            finally:
                if old is None:
                    os.environ.pop("CW_FOLDERWATCH", None)
                else:
                    os.environ["CW_FOLDERWATCH"] = old

    def test_autostart_remains_independent_of_autoconfirm(self):
        with tempfile.TemporaryDirectory() as watch:
            old = os.environ.get("CW_FOLDERWATCH")
            os.environ["CW_FOLDERWATCH"] = watch
            try:
                path = crawljob.write(
                    ["https://rapidgator.net/file/example"],
                    "Example release",
                    auto_start=True,
                )
                with open(path, encoding="utf-8") as fh:
                    job = json.load(fh)[0]
                self.assertEqual(job["autoConfirm"], "TRUE")
                self.assertEqual(job["autoStart"], "TRUE")
            finally:
                if old is None:
                    os.environ.pop("CW_FOLDERWATCH", None)
                else:
                    os.environ["CW_FOLDERWATCH"] = old


if __name__ == "__main__":
    unittest.main()


# --- the emitted field set -------------------------------------------------
# JD accepts a job or discards it in silence; there is no error to catch. The
# six fields below have a long record of being accepted, and one field beyond
# them once made every job vanish, so the set is pinned here deliberately.

SIX = {"enabled", "text", "packageName", "downloadFolder", "autoStart", "autoConfirm"}


def _job(tmp_path, **kw):
    import json, os
    os.environ["CW_FOLDERWATCH"] = str(tmp_path)
    from crawler import crawljob
    path = crawljob.write(["https://rapidgator.net/file/abc/x.rar.html"], "T", **kw)
    return json.load(open(path))[0]


def test_exactly_the_six_proven_fields_are_emitted(tmp_path):
    assert set(_job(tmp_path)) == SIX


def test_overwrite_packagizer_is_never_emitted(tmp_path):
    # Sent as the string "FALSE" this field fails to deserialise and JD drops
    # the entire job without a word. Absent is the only form that cannot.
    assert "overwritePackagizerEnabled" not in _job(tmp_path)


def test_boolean_status_fields_are_jd_strings_not_json_booleans(tmp_path):
    job = _job(tmp_path, auto_start=True)
    assert job["enabled"] == "TRUE"
    assert job["autoStart"] == "TRUE"
    assert job["autoConfirm"] == "TRUE"
    assert _job(tmp_path, auto_start=False)["autoStart"] == "FALSE"
