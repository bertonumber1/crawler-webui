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
