"""SQLite state: watches, seen-baseline, and the queue state machine.

Two rules the rest of the app relies on:

  1. UNIQUE(store, release_id) on `queue` makes a double-grab impossible at the
     storage layer rather than in application logic.
  2. Rows only move through TRANSITIONS. An illegal move raises instead of
     silently landing somewhere odd, and every move writes an audit row.
"""
import json, os, sqlite3, threading, time
from contextlib import contextmanager
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# CW_DB_PATH names the file directly and wins; CW_DATA_DIR names the directory.
# The compose file has always set CW_DB_PATH, and it was being ignored -- the
# database was written inside the image instead of the mounted volume, so every
# rebuild silently started from an empty history.
_explicit = os.environ.get("CW_DB_PATH", "").strip()
if _explicit:
    DB_PATH = os.path.abspath(_explicit)
    DATA_DIR = os.path.dirname(DB_PATH)
else:
    DATA_DIR = os.path.abspath(os.environ.get("CW_DATA_DIR", os.path.dirname(HERE)))
    DB_PATH = os.path.join(DATA_DIR, "crawler.db")
os.makedirs(DATA_DIR, exist_ok=True)
_lock = threading.Lock()

NEW, QUEUED, CLAIMED, DOWNLOADING = "new", "queued", "claimed", "downloading"
VERIFIED, FILED = "verified", "filed"
SKIPPED_OWNED, FAILED, QUARANTINED, CANCELLED = "skipped_owned", "failed", "quarantined", "cancelled"

TRANSITIONS = {
    NEW:         {QUEUED, SKIPPED_OWNED, CANCELLED},
    QUEUED:      {CLAIMED, CANCELLED, FAILED},
    CLAIMED:     {DOWNLOADING, QUEUED, FAILED, CANCELLED},   # back to QUEUED = released claim
    DOWNLOADING: {VERIFIED, FAILED, QUARANTINED},
    VERIFIED:    {FILED, FAILED},
    FILED:       set(),
    SKIPPED_OWNED: {QUEUED},        # manual override: grab it anyway
    FAILED:      {QUEUED},          # retry
    QUARANTINED: {QUEUED},
    CANCELLED:   {QUEUED},
}
TERMINAL = {FILED}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    with _lock:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store TEXT NOT NULL, ref TEXT NOT NULL, name TEXT NOT NULL,
            notify INTEGER DEFAULT 1, auto_queue TEXT DEFAULT 'off',
            muted INTEGER DEFAULT 0, poll_interval INTEGER DEFAULT 1800,
            last_poll TEXT DEFAULT '', last_ok TEXT DEFAULT '', last_error TEXT DEFAULT '',
            created TEXT NOT NULL,
            UNIQUE(store, ref)
        );
        CREATE TABLE IF NOT EXISTS seen (
            store TEXT NOT NULL, release_id TEXT NOT NULL,
            watch_id INTEGER, first_seen TEXT NOT NULL, reason TEXT DEFAULT '',
            PRIMARY KEY (store, release_id)
        );
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store TEXT NOT NULL, release_id TEXT NOT NULL,
            watch_id INTEGER, payload TEXT NOT NULL,
            state TEXT NOT NULL, state_since TEXT NOT NULL,
            claimed_by TEXT DEFAULT '', attempts INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '', blocked_reason TEXT DEFAULT '',
            local_path TEXT DEFAULT '', created TEXT NOT NULL,
            UNIQUE(store, release_id)
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, queue_id INTEGER,
            from_state TEXT, to_state TEXT, actor TEXT, note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_queue_state ON queue(state);
        """)


# ---------------------------------------------------------------- watches

def add_watch(store, ref, name, poll_interval=1800) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO watches (store, ref, name, poll_interval, created)"
            " VALUES (?,?,?,?,?)", (store, ref, name, poll_interval, now()))
        if cur.lastrowid:
            return cur.lastrowid
        return c.execute("SELECT id FROM watches WHERE store=? AND ref=?",
                         (store, ref)).fetchone()["id"]


def list_watches() -> list:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM watches ORDER BY store, name")]


def get_watch(wid) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM watches WHERE id=?", (wid,)).fetchone()
        return dict(r) if r else None


def update_watch(wid, **fields):
    allowed = {"notify", "auto_queue", "muted", "poll_interval", "name",
               "last_poll", "last_ok", "last_error"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=?" for k in sets)
    with db() as c:
        c.execute(f"UPDATE watches SET {cols} WHERE id=?", (*sets.values(), wid))


def delete_watch(wid):
    with db() as c:
        c.execute("DELETE FROM watches WHERE id=?", (wid,))


# ------------------------------------------------------------------- seen

def is_seen(store, release_id) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM seen WHERE store=? AND release_id=?",
                         (store, str(release_id))).fetchone() is not None


def mark_seen(store, release_id, watch_id=None, reason=""):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO seen (store, release_id, watch_id, first_seen, reason)"
                  " VALUES (?,?,?,?,?)", (store, str(release_id), watch_id, now(), reason))


def baseline(releases, watch_id, reason="baseline (predates watch)") -> int:
    """Mark everything currently listed as seen so adding a watch does not
    fire a notification per back-catalogue release."""
    n = 0
    with db() as c:
        for r in releases:
            cur = c.execute(
                "INSERT OR IGNORE INTO seen (store, release_id, watch_id, first_seen, reason)"
                " VALUES (?,?,?,?,?)", (r.store, str(r.id), watch_id, now(), reason))
            n += cur.rowcount
    return n


# ------------------------------------------------------------------ queue

def enqueue(release, watch_id, state=NEW, blocked_reason="") -> int | None:
    with db() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO queue (store, release_id, watch_id, payload, state,"
            " state_since, blocked_reason, created) VALUES (?,?,?,?,?,?,?,?)",
            (release.store, str(release.id), watch_id, json.dumps(release.dict()),
             state, now(), blocked_reason, now()))
        if not cur.lastrowid:
            return None
        c.execute("INSERT INTO events (ts, queue_id, from_state, to_state, actor, note)"
                  " VALUES (?,?,?,?,?,?)", (now(), cur.lastrowid, "", state, "poller", blocked_reason))
        return cur.lastrowid


class IllegalTransition(Exception):
    pass


def transition(qid, to_state, actor="system", note="", claimed_by=None, local_path=None):
    with db() as c:
        row = c.execute("SELECT * FROM queue WHERE id=?", (qid,)).fetchone()
        if not row:
            raise IllegalTransition(f"queue id {qid} not found")
        frm = row["state"]
        if to_state not in TRANSITIONS.get(frm, set()):
            raise IllegalTransition(
                f"{frm} -> {to_state} is not allowed (legal: {sorted(TRANSITIONS.get(frm, set())) or 'none, terminal'})")
        sets = ["state=?", "state_since=?"]
        vals = [to_state, now()]
        if to_state == FAILED:
            sets.append("attempts=attempts+1")
        if note:
            sets.append("last_error=?"); vals.append(note)
        if claimed_by is not None:
            sets.append("claimed_by=?"); vals.append(claimed_by)
        if local_path is not None:
            sets.append("local_path=?"); vals.append(local_path)
        vals.append(qid)
        c.execute(f"UPDATE queue SET {', '.join(sets)} WHERE id=?", vals)
        c.execute("INSERT INTO events (ts, queue_id, from_state, to_state, actor, note)"
                  " VALUES (?,?,?,?,?,?)", (now(), qid, frm, to_state, actor, note))
    return True


def claim(qid, worker) -> bool:
    """Atomic claim: only succeeds if the row is still QUEUED."""
    with db() as c:
        cur = c.execute(
            "UPDATE queue SET state=?, state_since=?, claimed_by=? WHERE id=? AND state=?",
            (CLAIMED, now(), worker, qid, QUEUED))
        if cur.rowcount == 0:
            return False
        c.execute("INSERT INTO events (ts, queue_id, from_state, to_state, actor, note)"
                  " VALUES (?,?,?,?,?,?)", (now(), qid, QUEUED, CLAIMED, worker, "claimed"))
    return True


def queue_items(state=None, store=None, limit=200) -> list:
    q, args = "SELECT * FROM queue", []
    where = []
    if state:
        where.append("state=?"); args.append(state)
    if store:
        where.append("store=?"); args.append(store)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    with db() as c:
        out = []
        for r in c.execute(q, args):
            d = dict(r)
            d["release"] = json.loads(d.pop("payload"))
            out.append(d)
        return out


def queue_counts() -> dict:
    with db() as c:
        return {r["state"]: r["n"] for r in
                c.execute("SELECT state, COUNT(*) n FROM queue GROUP BY state")}


def recent_events(limit=50) -> list:
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]


# ------------------------------------------------------------- http cache

def init_cache():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS http_cache (
            url TEXT PRIMARY KEY,
            etag TEXT DEFAULT '', last_modified TEXT DEFAULT '',
            fetched TEXT NOT NULL, status INTEGER DEFAULT 0
        );
        """)


def cache_get(url) -> dict:
    with db() as c:
        r = c.execute("SELECT * FROM http_cache WHERE url=?", (url,)).fetchone()
        return dict(r) if r else {}


def cache_put(url, etag="", last_modified="", status=0):
    with db() as c:
        c.execute("INSERT INTO http_cache (url, etag, last_modified, fetched, status)"
                  " VALUES (?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET"
                  " etag=excluded.etag, last_modified=excluded.last_modified,"
                  " fetched=excluded.fetched, status=excluded.status",
                  (url, etag, last_modified, now(), status))


# --------------------------------------------------------------- settings

def init_settings():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT NOT NULL
        );
        """)


def setting(key, default="") -> str:
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_setting(key, value):
    with db() as c:
        c.execute("INSERT INTO settings (key, value, updated) VALUES (?,?,?)"
                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                  " updated=excluded.updated", (key, str(value), now()))


def all_settings() -> dict:
    with db() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
