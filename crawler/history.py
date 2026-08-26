"""What has already been sent to JDownloader, keyed by file identity.

Without this the crawler has no memory: crawl the same thread twice and it
re-resolves every release page and writes every crawljob again. JD then drops
the duplicates silently -- its dupe manager does not report -- so the second
run looks exactly like a successful first run while achieving nothing.

Identity is the canonical key from hosts.reduce(), not the URL, because the
same file appears as /file/<id> and /file/<id>/<name>.html and both must
count as the same download.

This is separate from db.seen/db.queue, which track releases per watch. A
file is not a release: the same Rapidgator link can appear in three threads on
two forums, and once it is downloaded none of them should offer it again.
"""
from __future__ import annotations

import json

from . import db, links as linkmod

SENT = "sent"          # crawljob written to folderwatch
CONFIRMED = "in_jd"    # observed in JD's own lists
GONE = "gone"          # observed leaving JD (finished or deleted)


def init():
    with db.db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS downloads (
            file_key   TEXT PRIMARY KEY,
            url        TEXT NOT NULL,
            host       TEXT NOT NULL,
            title      TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            state      TEXT NOT NULL,
            package    TEXT DEFAULT '',
            job_path   TEXT DEFAULT '',
            first_sent TEXT NOT NULL,
            last_seen  TEXT NOT NULL,
            note       TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_downloads_state ON downloads(state);
        CREATE TABLE IF NOT EXISTS crawls (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            url       TEXT NOT NULL,
            provider  TEXT DEFAULT '',
            ts        TEXT NOT NULL,
            items     INTEGER DEFAULT 0,
            links     INTEGER DEFAULT 0,
            fresh     INTEGER DEFAULT 0,
            pages     INTEGER DEFAULT 1,
            detected  TEXT DEFAULT '',
            note      TEXT DEFAULT ''
        );
        """)


def key_for(url: str) -> str:
    return linkmod.canonical_download_key(url)


def known(keys) -> dict[str, dict]:
    """Return {file_key: row} for the keys we already hold."""
    keys = [k for k in keys if k]
    if not keys:
        return {}
    out = {}
    with db.db() as c:
        # Chunked so a large crawl does not build an enormous IN clause.
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            q = ",".join("?" * len(chunk))
            for r in c.execute(
                f"SELECT * FROM downloads WHERE file_key IN ({q})", chunk
            ).fetchall():
                out[r["file_key"]] = dict(r)
    return out


def annotate(links: list[dict]) -> list[dict]:
    """Tag each link with what we already know about it.

    Adds `file_key`, `seen_before` and `prior_state`, so the UI can show that a
    link was already sent before you tick it rather than after JD eats it.
    """
    keyed = [(l, key_for(l.get("url", ""))) for l in links]
    rows = known([k for _, k in keyed])
    out = []
    for link, k in keyed:
        item = dict(link)
        row = rows.get(k)
        item["file_key"] = k
        item["seen_before"] = bool(row)
        item["prior_state"] = row["state"] if row else ""
        item["prior_sent"] = row["first_sent"] if row else ""
        out.append(item)
    return out


def record_sent(links: list[dict], package: str = "", job_path: str = "",
                source_url: str = "", title: str = "") -> int:
    """Remember that these links were handed to JD. Returns rows written."""
    n = 0
    ts = db.now()
    with db.db() as c:
        for l in links:
            url = l.get("url", "")
            # Only a real canonical identity may be written down. A key that
            # is just the URL back again means the reducer did not recognise
            # it, and recording that would put a permanent entry in the way of
            # nothing at all.
            if not linkmod.is_download_host(url):
                continue
            k = l.get("file_key") or key_for(url)
            if not k or k == url.rstrip("/").lower():
                continue
            c.execute("""
                INSERT INTO downloads
                    (file_key,url,host,title,source_url,state,package,job_path,
                     first_sent,last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(file_key) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    package=CASE WHEN excluded.package<>'' THEN excluded.package
                                 ELSE downloads.package END,
                    job_path=excluded.job_path
            """, (k, url, l.get("host", ""), title or l.get("title", ""),
                  source_url, SENT, package, job_path, ts, ts))
            n += 1
    return n


def mark(keys, state: str, note: str = "") -> int:
    keys = [k for k in keys if k]
    if not keys:
        return 0
    ts = db.now()
    with db.db() as c:
        for k in keys:
            c.execute(
                "UPDATE downloads SET state=?, last_seen=?, note=? WHERE file_key=?",
                (state, ts, note, k))
    return len(keys)


def counts() -> dict:
    with db.db() as c:
        rows = c.execute(
            "SELECT state, COUNT(*) n FROM downloads GROUP BY state").fetchall()
    return {r["state"]: r["n"] for r in rows}


def recent(limit: int = 100) -> list[dict]:
    with db.db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM downloads ORDER BY last_seen DESC LIMIT ?",
            (limit,)).fetchall()]


def forget(keys) -> int:
    """Drop rows so a file may be queued again."""
    keys = [k for k in keys if k]
    if not keys:
        return 0
    with db.db() as c:
        for k in keys:
            c.execute("DELETE FROM downloads WHERE file_key=?", (k,))
    return len(keys)


# ------------------------------------------------------------------ crawls

def record_crawl(url: str, provider: str = "", items: int = 0, links: int = 0,
                 fresh: int = 0, pages: int = 1, detected: str = "",
                 note: str = "") -> int:
    with db.db() as c:
        cur = c.execute("""
            INSERT INTO crawls (url,provider,ts,items,links,fresh,pages,detected,note)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (url, provider, db.now(), items, links, fresh, pages, detected, note))
        return cur.lastrowid


def crawl_history(limit: int = 25) -> list[dict]:
    with db.db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM crawls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
