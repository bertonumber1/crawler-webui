"""Link checks: prove dead or lossy from the host, never guess."""
import base64
import json

import pytest

from crawler import linkcheck

AES = pytest.importorskip("Crypto.Cipher.AES")


def _b64u(b):
    return base64.b64encode(b).decode().replace("+", "-").replace("/", "_").rstrip("=")


def _attr(name, key):
    raw = b"MEGA" + json.dumps({"n": name}).encode()
    raw += b"\0" * (-len(raw) % 16)
    return _b64u(AES.new(key, AES.MODE_CBC, b"\0" * 16).encrypt(raw))


def test_mega_file_name_is_decrypted_with_the_link_key(monkeypatch):
    filekey = bytes(range(32))
    aes = linkcheck._fold(filekey)
    monkeypatch.setattr(linkcheck, "_mega_api",
                        lambda payload, node=None: [{"s": 123, "at": _attr("01 Pista 1.wav", aes)}])
    res = linkcheck.check(f"https://mega.nz/file/AbCdEf12#{_b64u(filekey)}")
    assert res["alive"] is True and res["files"] == [("01 Pista 1.wav", 123)]
    assert linkcheck.verdict(res["files"]) == "lossless"


def test_mega_takedown_is_dead(monkeypatch):
    monkeypatch.setattr(linkcheck, "_mega_api", lambda payload, node=None: [-9])
    res = linkcheck.check("https://mega.nz/file/AbCdEf12#" + _b64u(bytes(32)))
    assert res["alive"] is False and "-9" in res["why"]


def test_unknown_host_is_never_called_dead():
    res = linkcheck.check("https://transfer.it/t/abc")
    assert res["alive"] is None
    keep, _ = linkcheck.keep_for_lossless("https://transfer.it/t/abc", "FLAC")
    assert keep is True


def test_real_filenames_beat_the_post_text():
    # A post can say WAV; the files say MP3.
    assert linkcheck.verdict([("Tribute vol.1.mp3", 1)], "WAV rip") == "lossy"
    assert linkcheck.verdict([("CD1.rar", 1)], "320kbps") == "lossy?"
    assert linkcheck.verdict([("CD1.rar", 1)], "FLAC rip") == "lossless?"
    assert linkcheck.verdict([], "") == "unknown"


def test_lossless_only_drops_proven_mp3(monkeypatch):
    monkeypatch.setattr(linkcheck, "check",
                        lambda u: {"alive": True, "files": [("a.mp3", 1), ("b.mp3", 1)], "why": ""})
    keep, res = linkcheck.keep_for_lossless("https://pixeldrain.com/u/x", "FLAC")
    assert keep is False and res["verdict"] == "lossy"
