"""Post a thank-you reply so a gated topic will show its links.

dark-decade (and most foroactivo boards) hide the download box behind a
thanks gate: the links are simply not in the HTML until the reading account
has replied to that topic. That is why Pont Aeri, Chasis History and the rest
kept resolving to zero links — not a block, not a stale cookie, not pacing.
No read-only crawl can ever get past it, so the only way through is to post.

Posting is different in kind from everything else this app does. Every other
request only reads; this one writes to the owner's forum account, under their
name, where other members can see it. So it is built to be hard to fire by
accident:

  * OFF at import. Nothing here runs unless CW_THANKS_ENABLED is set AND the
    runner is started by hand from the UI. A crawl never starts it.
  * DRY RUN by default. Even enabled and started, it walks the whole path and
    reports the exact form it would submit without sending it, until dry run
    is explicitly turned off.
  * One host. CW_THANKS_HOSTS is an allowlist; a URL anywhere else is refused
    rather than posted to.
  * One post per topic, ever. The database holds a UNIQUE url, and the live
    page is re-read first to see whether this account has already replied.
  * A daily ceiling, so a bug loops into a cap rather than into the board.

Pacing is the owner's: three posts, then nothing for 25 minutes. Within a
burst the posts are spaced by a random gap for the same reason the crawler
pauses between pages — three replies in three seconds is a signature.
"""
from __future__ import annotations

import os
import random
import re
import threading
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import db, fetch, trace
from .forum_parser import forumotion_topic_url


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "")


# --- configuration -------------------------------------------------------
# Read once at import, like the rest of the app's env. ENABLED is the outer
# switch: with it unset the API returns "disabled", the UI panel stays hidden
# and the runner refuses to start.
ENABLED = _flag("CW_THANKS_ENABLED", "0")
HOSTS = tuple(h.strip().lower().removeprefix("www.")
              for h in os.getenv("CW_THANKS_HOSTS", "foroactivo.com").split(",")
              if h.strip())
MESSAGE = os.getenv("CW_THANKS_MESSAGE", "gracias amigo")
BURST = max(1, int(os.getenv("CW_THANKS_BURST", "3")))
WINDOW = max(60.0, float(os.getenv("CW_THANKS_WINDOW", "1500")))     # 25 minutes
DAILY_MAX = max(1, int(os.getenv("CW_THANKS_DAILY_MAX", "24")))
# robots.txt has nothing to say about submitting a form, but this app has
# never fetched a disallowed path, so the default stays "respect" and the
# preview reports the verdict before anything is started.
ROBOTS = os.getenv("CW_THANKS_ROBOTS", "respect").strip().lower()


def _spacing() -> tuple[float, float]:
    lo, _, hi = (os.getenv("CW_THANKS_SPACING", "40-90") or "").partition("-")
    try:
        a = float(lo)
        b = float(hi) if hi else a
    except ValueError:
        return 40.0, 90.0
    return min(a, b), max(a, b)


SPACING = _spacing()


class Refused(Exception):
    """The post was not attempted, and why."""


# --- settings the UI can change without a rebuild ------------------------

def dry_run() -> bool:
    return db.setting("thanks_dry_run", "1") != "0"


def set_dry_run(on: bool) -> bool:
    db.set_setting("thanks_dry_run", "1" if on else "0")
    if not on:
        # Coming off dry run, everything previewed is still pending and still
        # wants posting. Without this the runner would skip precisely the
        # topics it had just shown you.
        runner._previewed.clear()
    return dry_run()


def messages() -> list[str]:
    """The phrasings to choose from, one per line. Empty = the env default.

    A list rather than one string because the same eight words under every
    topic is the most mechanical thing an account can do.
    """
    raw = db.setting("thanks_messages", "")
    out = [x.strip() for x in raw.splitlines() if x.strip()]
    return out or [MESSAGE]


def set_messages(text: str) -> list[str]:
    db.set_setting("thanks_messages", (text or "").strip())
    return messages()


def pick_message() -> str:
    return random.choice(messages())


# --- target validation ---------------------------------------------------

def allowed_host(url: str) -> bool:
    host = urlparse(url or "").netloc.lower().split("@")[-1].split(":")[0]
    host = host.removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in HOSTS)


def normalise(url: str) -> str:
    """The bare topic URL, or raise Refused saying what is wrong with it."""
    url = (url or "").strip()
    if not url:
        raise Refused("empty URL")
    if not allowed_host(url):
        raise Refused(f"host is not in CW_THANKS_HOSTS ({', '.join(HOSTS) or 'none'})")
    topic = forumotion_topic_url(url)
    if not topic:
        raise Refused("not a plain Forumotion topic URL (/t<id>-slug)")
    return topic


# --- reading the page ----------------------------------------------------

_USER_ID = re.compile(r"_userdata\s*\[\s*['\"]user_id['\"]\s*\]\s*=\s*(\d+)")
_USERNAME = re.compile(r"_userdata\s*\[\s*['\"]username['\"]\s*\]\s*=\s*['\"](.*?)['\"]")


def viewer(html: str) -> dict:
    """Who the board thinks is reading. Forumotion states it in page JS."""
    uid = _USER_ID.search(html or "")
    name = _USERNAME.search(html or "")
    user_id = uid.group(1) if uid else ""
    return {
        "user_id": user_id,
        "username": name.group(1) if name else "",
        # user_id 1 is the guest account on every Forumotion board.
        "logged_in": bool(user_id) and user_id != "1",
    }


def already_replied(html: str, user_id: str) -> bool:
    """Has this account already posted in this topic?

    Looked for in post author position only: a /u<id> link inside a post's
    author column. The member's own name in a quote or the online list is not
    a reply, and treating it as one would leave a gated topic unthanked.
    """
    if not user_id:
        return False
    soup = BeautifulSoup(html or "", "lxml")
    needle = f"/u{user_id}"
    for post in soup.select(".post, .postbody, .post-entry"):
        for holder in post.select(".postprofile, .poster-profile, .post-author, "
                                  ".postdetails, .name, span.name"):
            for a in holder.find_all("a", href=True):
                href = a["href"].split("?")[0].rstrip("/")
                if href.endswith(needle):
                    return True
    return False


# Never carried over from the page into our submission: a file upload we are
# not making, and the preview/delete buttons, which do something else entirely.
_SKIP_FIELDS = {"fileupload", "preview", "delete", "cancel", "attachsig_preview"}


def find_form(html: str, base_url: str) -> dict | None:
    """The topic's reply form, as the fields that would be submitted.

    Read off the page rather than hardcoded: Forumotion's quick reply carries
    a per-session token (tid) and a post-timer field (lt), both of which are
    rejected if invented. If the page has no reply form -- a locked topic, or
    a session that has lapsed to guest -- this returns None and the caller
    skips the topic instead of guessing at a submission.
    """
    soup = BeautifulSoup(html or "", "lxml")
    for form in soup.find_all("form"):
        textarea = form.find("textarea", attrs={"name": "message"})
        if not textarea:
            continue
        action = (form.get("action") or "").strip()
        if "/post" not in action and "post" not in (form.get("name") or ""):
            continue
        fields: dict[str, str] = {}
        for el in form.find_all(("input", "select", "textarea")):
            name = (el.get("name") or "").strip()
            if not name or name.lower() in _SKIP_FIELDS:
                continue
            kind = (el.get("type") or "").lower()
            if kind == "file":
                continue
            if kind in ("checkbox", "radio") and not el.has_attr("checked"):
                continue
            if el.name == "textarea":
                fields[name] = el.get_text()
            elif el.name == "select":
                chosen = el.find("option", selected=True) or el.find("option")
                fields[name] = (chosen.get("value") if chosen else "") or ""
            else:
                fields[name] = el.get("value") or ""
        if "message" not in fields:
            continue
        return {
            "action": urljoin(base_url, action or base_url),
            "fields": fields,
            # Forumotion only accepts the reply when the submit button that
            # was "pressed" is in the body, so it is kept above.
            "submit": [k for k in fields if k.lower() in ("post", "submit")],
        }
    return None


def inspect(url: str) -> dict:
    """Read a topic and report exactly what posting would do. Writes nothing.

    This is the safe way to prove the whole path works -- session, form,
    token, pacing, robots -- with no reply left on the board.
    """
    topic = normalise(url)
    status, body, _ = fetch.get(topic, conditional=False)
    who = viewer(body)
    form = find_form(body, topic)
    robots_ok = True
    robots_note = ""
    if form:
        try:
            robots_ok, _ = fetch.robots_ok(form["action"])
        except Exception as e:
            robots_ok, robots_note = True, f"robots check failed: {type(e).__name__}"
        if not robots_ok and ROBOTS == "respect":
            robots_note = (f"robots.txt disallows {urlparse(form['action']).path} — "
                           "set CW_THANKS_ROBOTS=ignore to post anyway")
    done = already_replied(body, who["user_id"])
    row = db.thanks_get(topic)
    return {
        "url": topic,
        "status": status,
        "logged_in": who["logged_in"],
        "username": who["username"],
        "already_replied": done,
        "recorded": row["state"] if row else "",
        "form_found": bool(form),
        "action": form["action"] if form else "",
        "fields": sorted(form["fields"]) if form else [],
        "robots_ok": robots_ok,
        "robots_note": robots_note,
        "message": pick_message(),
        "would_post": bool(form) and who["logged_in"] and not done
                      and (robots_ok or ROBOTS != "respect"),
        "dry_run": dry_run(),
    }


# --- posting -------------------------------------------------------------

def post_one(url: str, message: str | None = None, force: bool = False) -> dict:
    """Thank one topic. Returns what happened; never posts twice.

    Order matters here: the page is re-read immediately before posting, so a
    reply left by hand in the browser five minutes ago still counts and the
    account does not say thank you twice in the same thread.
    """
    topic = normalise(url)
    if not ENABLED:
        raise Refused("CW_THANKS_ENABLED is not set")
    row = db.thanks_get(topic)
    if row and row["state"] == "posted" and not force:
        return {"url": topic, "posted": False, "state": "posted",
                "note": "already thanked on " + (row["posted_at"] or "?")}

    # A row must exist before anything is recorded against it: post_one is
    # also reachable for a single topic that was never queued, and an UPDATE
    # against a missing row silently records nothing -- which would leave the
    # only defence against thanking twice being the live page read.
    db.thanks_add(topic)

    status, body, _ = fetch.get(topic, conditional=False)
    who = viewer(body)
    if not who["logged_in"]:
        raise Refused("the board is serving this as a guest — the session has lapsed")
    if already_replied(body, who["user_id"]) and not force:
        db.thanks_set(topic, state="posted", error="",
                      note=f"already replied as {who['username']}")
        return {"url": topic, "posted": False, "state": "posted",
                "note": f"{who['username']} had already replied"}

    form = find_form(body, topic)
    if not form:
        db.thanks_set(topic, state="skipped", error="no reply form on the page")
        return {"url": topic, "posted": False, "state": "skipped",
                "note": "no reply form (locked topic, or not allowed to post)"}

    text = message or pick_message()
    fields = dict(form["fields"])
    fields["message"] = text
    # A quick reply with no subject is normal; Forumotion fills it in.
    fields.setdefault("subject", "")

    if dry_run():
        trace.event("thanks", "dry run", url=topic, message=text,
                    action=form["action"], fields=len(fields))
        return {"url": topic, "posted": False, "state": "dry-run",
                "note": "dry run — nothing was sent", "message": text,
                "action": form["action"], "field_count": len(fields)}

    try:
        st = fetch.post(form["action"], fields, referer=topic,
                        respect_robots=(ROBOTS == "respect"))
    except fetch.Blocked as e:
        db.thanks_set(topic, state="failed", error=str(e))
        raise Refused(str(e)) from e
    except Exception as e:
        db.thanks_set(topic, state="failed", error=f"{type(e).__name__}: {e}")
        raise

    db.thanks_set(topic, state="posted", error="", message=text,
                  posted_at=db.now())
    trace.event("thanks", "posted", url=topic, message=text, status=st)
    return {"url": topic, "posted": True, "state": "posted",
            "message": text, "status": st, "username": who["username"]}


# --- the runner ----------------------------------------------------------

class Runner:
    """Three posts, then quiet for the rest of the 25 minutes.

    Held as one object rather than a loose thread so the UI can ask what it is
    doing and stop it mid-wait: the sleeps are Event.wait(), so stop takes
    effect at once instead of up to 25 minutes later.
    """

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # Topics this run has already dry-run. A dry run leaves the row
        # pending on purpose -- turning dry run off must still post it -- so
        # without this the runner would preview the head of the queue over and
        # over and never reach the rest of it.
        self._previewed: set[str] = set()
        self.state = "idle"
        self.note = ""
        self.next_at = 0.0
        self.posted_today = 0
        self._day = time.strftime("%Y-%m-%d")

    # -- lifecycle
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> dict:
        if not ENABLED:
            raise Refused("CW_THANKS_ENABLED is not set — the feature is built "
                          "but switched off")
        with self._lock:
            if self.running():
                return self.status()
            if not db.thanks_pending(1):
                raise Refused("no topics queued")
            self._stop.clear()
            self._previewed.clear()
            self.state = "running"
            self.note = ""
            self._thread = threading.Thread(target=self._loop, name="thanks",
                                            daemon=True)
            self._thread.start()
        trace.event("thanks", "runner started", dry_run=dry_run(),
                    burst=BURST, window=WINDOW)
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        self.state = "stopping" if self.running() else "idle"
        trace.event("thanks", "runner stopped")
        return self.status()

    def status(self) -> dict:
        pending = db.thanks_counts()
        return {
            "enabled": ENABLED,
            "running": self.running(),
            "state": self.state,
            "note": self.note,
            "dry_run": dry_run(),
            "hosts": list(HOSTS),
            "messages": messages(),
            "burst": BURST,
            "window": WINDOW,
            "spacing": list(SPACING),
            "daily_max": DAILY_MAX,
            "posted_today": self.posted_today,
            "next_in": max(0, int(self.next_at - time.monotonic())) if self.next_at else 0,
            "counts": pending,
            "targets": db.thanks_list(),
        }

    # -- the loop
    def _wait(self, seconds: float) -> bool:
        """Sleep, but wake immediately on stop. False = we were stopped."""
        self.next_at = time.monotonic() + max(0.0, seconds)
        stopped = self._stop.wait(max(0.0, seconds))
        self.next_at = 0.0
        return not stopped

    def _next_target(self) -> dict | None:
        """The next topic to act on, skipping what this run has only previewed."""
        for row in db.thanks_pending(200):
            if row["url"] not in self._previewed:
                return row
        return None

    def _roll_day(self):
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self.posted_today = today, 0

    def _loop(self):
        try:
            while not self._stop.is_set():
                self._roll_day()
                if self.posted_today >= DAILY_MAX:
                    self.state = "capped"
                    self.note = f"daily ceiling of {DAILY_MAX} reached"
                    if not self._wait(WINDOW):
                        break
                    continue

                started = time.monotonic()
                done = 0
                for i in range(BURST):
                    if self._stop.is_set():
                        break
                    target = self._next_target()
                    if not target:
                        self.note = "queue empty"
                        break
                    self.state = "posting"
                    try:
                        res = post_one(target["url"])
                        done += 1
                        if res.get("posted"):
                            self.posted_today += 1
                        elif res.get("state") == "dry-run":
                            self._previewed.add(target["url"])
                        self.note = f"{res['state']}: {target['url']}"
                    except Refused as e:
                        db.thanks_set(target["url"], state="failed", error=str(e))
                        self.note = f"refused: {e}"
                        trace.event("thanks", "refused", url=target["url"],
                                    error=str(e))
                    except Exception as e:
                        db.thanks_set(target["url"], state="failed",
                                      error=f"{type(e).__name__}: {e}")
                        self.note = f"failed: {type(e).__name__}"
                        trace.event("thanks", "failed", url=target["url"],
                                    error=f"{type(e).__name__}: {e}")
                    # Pacing exists to space out posts. A dry run sends
                    # nothing, so it is only limited by the page reads, which
                    # fetch already throttles -- otherwise previewing a queue
                    # of twenty would take the best part of three hours.
                    if (not dry_run()) and i < BURST - 1 and self._next_target():
                        self.state = "spacing"
                        if not self._wait(random.uniform(*SPACING)):
                            break

                if self._stop.is_set():
                    break
                if not self._next_target():
                    self.state = "idle"
                    self.note = ("previewed the whole queue — turn dry run off to post"
                                 if dry_run() else "queue empty — nothing left to thank")
                    break
                if dry_run():
                    continue
                self.state = "waiting"
                rest = WINDOW - (time.monotonic() - started)
                if rest > 0 and not self._wait(rest):
                    break
        finally:
            self.next_at = 0.0
            if self.state != "capped":
                self.state = "idle"


runner = Runner()
