"""Is a file-host link alive, and is it really lossless? Without downloading it.

A forum post that says "FLAC" proves nothing: on makinamania.net one release
posted as WAV was labelled 320kbps by its own uploader, and of 734 links in
the 2025-26 posts 242 SwissTransfer and 117 MEGA links were already dead. The
hosts themselves will say, for free:

  MEGA          the API returns size + encrypted attributes; the link's own key
                decrypts the real file/folder names (that is how mega.nz shows
                them to a browser)
  MediaFire     api/1.5 file/get_info, folder/get_content
  Pixeldrain    api/file/<id>/info, api/list/<id>
  SwissTransfer api/links/<uuid>  (data.type "expired" when gone)
  Rapidgator    the file page title ("Download file <name>")

Every other host returns alive=None: unknown, never "dead". With lossless
only on, a crawl drops a link only when the host PROVES it dead or proves
every file in it lossy. Unknown goes to JDownloader, whose own link check
then decides.
"""
from __future__ import annotations

import base64
import json
import re
import struct

import httpx

try:                                    # pycryptodome; MEGA names need it
    from Crypto.Cipher import AES
except Exception:                       # pragma: no cover - checked at runtime
    AES = None

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"}
_client = httpx.Client(timeout=25, headers=UA, follow_redirects=True)

LOSSLESS_FILE = re.compile(r"\.(flac|wav|aiff?|ape|wv|dsf|dff|alac)$", re.I)
LOSSY_FILE = re.compile(r"\.(mp3|m4a|aac|ogg|opus|wma)$", re.I)
LOSSLESS_TXT = re.compile(r"(?<![a-z0-9])(flac|wav|wave|lossless|aiff?|ape)(?![a-z0-9])", re.I)
LOSSY_TXT = re.compile(r"(?<![a-z0-9])(mp3|\d{3}\s*kbps|aac|lossy)(?![a-z0-9])", re.I)


# ----------------------------------------------------------------- MEGA ----
def _b64(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/").replace(",", "")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def _a32(b: bytes):
    return struct.unpack(">%dI" % (len(b) // 4), b)


def _fold(key: bytes) -> bytes:
    """A file key is 256 bits; the AES key is its two halves XORed."""
    k = _a32(key)
    if len(k) == 8:
        return struct.pack(">4I", k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7])
    return key[:16]


def mega_attr_name(attr_b64: str, key: bytes) -> str | None:
    """Decrypt a node's attribute block ("MEGA{...json...}") and return its name."""
    if AES is None:
        return None
    try:
        raw = AES.new(key, AES.MODE_CBC, b"\0" * 16).decrypt(_b64(attr_b64)).rstrip(b"\0")
        if not raw.startswith(b"MEGA"):
            return None
        return json.loads(raw[4:raw.rindex(b"}") + 1]).get("n")
    except Exception:
        return None


_MEGA_NEW = re.compile(r"mega(?:\.co)?\.nz/(file|folder)/([\w-]+)#([\w-]+)")
_MEGA_OLD = re.compile(r"mega(?:\.co)?\.nz/#(F?)!([\w-]+)!([\w-]+)")


def _mega_api(payload: dict, node: str | None = None):
    url = "https://g.api.mega.co.nz/cs?id=1" + (f"&n={node}" if node else "")
    return _client.post(url, json=[payload]).json()


def _mega_error(r) -> int | None:
    if isinstance(r, int):
        return r
    if isinstance(r, list) and r and isinstance(r[0], int):
        return r[0]
    return None


def _mega(url: str):
    m = _MEGA_NEW.search(url) or _MEGA_OLD.search(url)
    if not m:
        return None, "no key in the link"
    folder = m.group(1) in ("folder", "F")
    handle, key = m.group(2), _b64(m.group(3))
    if not folder:
        r = _mega_api({"a": "g", "p": handle})
        err = _mega_error(r)
        if err is not None:
            return False, f"MEGA error {err}"     # -9 not found, -16 taken down
        r = r[0]
        return True, [(mega_attr_name(r.get("at", ""), _fold(key)) or "?", r.get("s", 0))]
    r = _mega_api({"a": "f", "c": 1, "r": 1, "ca": 1}, node=handle)
    err = _mega_error(r)
    if err is not None:
        return False, f"MEGA error {err}"
    files = []
    for n in r[0].get("f", []):
        if n.get("t") != 0:
            continue
        name = None
        if AES is not None and ":" in n.get("k", ""):
            try:
                nk = AES.new(key, AES.MODE_ECB).decrypt(_b64(n["k"].split(":", 1)[1]))
                name = mega_attr_name(n.get("a", ""), _fold(nk))
            except Exception:
                name = None
        files.append((name or "?", n.get("s", 0)))
    return (True, files) if files else (False, "empty folder")


# -------------------------------------------------------- other hosts ----
def _mediafire(url: str):
    m = re.search(r"mediafire\.com/(?:\?|file/|download/|view/|folder/|download\.php\?)?([a-z0-9]{11,19})", url, re.I)
    if not m:
        return None, "no MediaFire key"
    k = m.group(1)
    j = _client.get("https://www.mediafire.com/api/1.5/file/get_info.php",
                    params={"quick_key": k, "response_format": "json"}).json()["response"]
    if j.get("result") == "Success":
        f = j["file_info"]
        return True, [(f["filename"], int(f["size"]))]
    first = j.get("message")
    j = _client.get("https://www.mediafire.com/api/1.5/folder/get_content.php",
                    params={"folder_key": k, "content_type": "files", "response_format": "json"}).json()["response"]
    if j.get("result") == "Success":
        fs = [(f["filename"], int(f["size"])) for f in j["folder_content"].get("files", [])]
        return (True, fs) if fs else (False, "empty folder")
    return False, first or j.get("message")


def _pixeldrain(url: str):
    m = re.search(r"pixeldrain\.com/(u|l)/(\w+)", url)
    if not m:
        return None, "no Pixeldrain id"
    kind, k = m.groups()
    r = _client.get(f"https://pixeldrain.com/api/file/{k}/info" if kind == "u"
                    else f"https://pixeldrain.com/api/list/{k}")
    if r.status_code == 404:
        return False, "not found"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    j = r.json()
    return True, ([(j["name"], j["size"])] if kind == "u"
                  else [(f["name"], f["size"]) for f in j.get("files", [])])


def _swisstransfer(url: str):
    # The newer /dl/<uuid> links have no public API; the page itself answers
    # 404 once the transfer has expired (verified 2026-10-11), as /d/ does.
    m = re.search(r"swisstransfer\.com/dl/([\w-]+)", url)
    if m:
        r = _client.get(url)
        return (False, "expired (404)") if r.status_code == 404 else (None, f"HTTP {r.status_code}")
    m = re.search(r"swisstransfer\.com/d/([\w-]+)", url)
    if not m:
        return None, "no SwissTransfer id"
    r = _client.get(f"https://www.swisstransfer.com/api/links/{m.group(1)}")
    if r.status_code == 404:
        return False, "not found"
    try:
        d = r.json().get("data", {})
    except Exception:
        return None, f"HTTP {r.status_code}"
    cont = d.get("container")
    if not cont:
        return False, d.get("type") or d.get("message") or "gone"
    return True, [(f.get("fileName"), f.get("fileSizeInBytes", 0)) for f in cont.get("files", [])]


def _rapidgator(url: str):
    r = _client.get(url)
    t = (re.findall(r"<title>([^<]+)", r.text) or [""])[0]
    if r.status_code == 404 or "not found" in t.lower():
        return False, "not found"
    m = re.search(r"Download file (.+)", t)
    return (True, [(m.group(1).strip(), 0)]) if m else (None, t[:60] or f"HTTP {r.status_code}")


CHECKERS = {
    "mega.nz": _mega, "mega.co.nz": _mega, "mediafire.com": _mediafire,
    "pixeldrain.com": _pixeldrain, "swisstransfer.com": _swisstransfer,
    "rapidgator.net": _rapidgator, "rg.to": _rapidgator,
}


def host_of(url: str) -> str:
    return re.sub(r"^https?://(?:www\.)?", "", url or "").split("/")[0].split(":")[0].lower()


def check(url: str) -> dict:
    """{'alive': True|False|None, 'files': [(name, bytes)], 'why': str}."""
    fn = CHECKERS.get(host_of(url))
    if fn is None:
        return {"alive": None, "files": [], "why": "host not checkable"}
    try:
        alive, info = fn(url)
    except Exception as e:
        return {"alive": None, "files": [], "why": f"{type(e).__name__}"}
    if alive:
        return {"alive": True, "files": list(info), "why": ""}
    return {"alive": alive, "files": [], "why": str(info)}


def verdict(files, text: str = "") -> str:
    """'lossless' / 'lossy' from real filenames; '?' suffix when only the
    post's words say so; 'unknown' when neither does."""
    names = [n for n, _ in files or [] if n and n != "?"]
    ll = sum(bool(LOSSLESS_FILE.search(n)) for n in names)
    ly = sum(bool(LOSSY_FILE.search(n)) for n in names)
    if ll or ly:
        return "lossless" if ll >= ly else "lossy"
    blob = " ".join(names) + " " + (text or "")
    if LOSSLESS_TXT.search(blob) and not LOSSY_TXT.search(blob):
        return "lossless?"
    if LOSSY_TXT.search(blob):
        return "lossy?"
    return "unknown"


def keep_for_lossless(url: str, text: str = "") -> tuple[bool, dict]:
    """Should a lossless-only crawl send this link? Drops only what a host
    PROVES dead, or proves (by real filenames) to be lossy."""
    res = check(url)
    v = verdict(res.get("files"), text)
    res["verdict"] = v
    if res["alive"] is False:
        return False, res
    if v == "lossy":
        return False, res
    return True, res
