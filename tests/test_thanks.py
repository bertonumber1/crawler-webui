"""The thank-you poster: the one thing in this app that writes to the board.

These tests are mostly about what it refuses to do. It posts to the owner's
account under their name, so "off unless two separate switches say otherwise,
and never twice in the same topic" is the behaviour worth pinning down.
"""
import importlib
import os
import tempfile

import pytest


@pytest.fixture()
def th(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    monkeypatch.setenv("CW_DB_PATH", path)
    from crawler import db as db_mod
    importlib.reload(db_mod)
    from crawler import thanks as th_mod
    importlib.reload(th_mod)
    db_mod.init_settings()
    db_mod.init_thanks()
    th_mod.ENABLED = True          # the env switch, forced on for the tests
    yield th_mod


# A Forumotion topic as served to a logged-in member: the quick reply carries
# a per-session token (tid) and a post timer (lt), neither of which can be
# invented, plus a file input we must not submit.
TOPIC = """
<html><head><script>
  _userdata['user_id'] = 4242;
  _userdata['username'] = "elitemakina";
</script></head><body>
<div class="post"><div class="postprofile"><a href="/u77">otro_usuario</a></div>
  <div class="postbody">Aqui va el enlace <div class="codebox hidecode">hidden</div></div></div>
<form action="/post" method="post" name="post">
  <input type="hidden" name="t" value="12294">
  <input type="hidden" name="mode" value="reply">
  <input type="hidden" name="tid" value="9f3c1ab77e">
  <input type="hidden" name="lt" value="0">
  <input type="file" name="fileupload">
  <input type="checkbox" name="notify" value="1">
  <input type="checkbox" name="attach_sig" value="1" checked>
  <textarea name="message"></textarea>
  <input type="submit" name="post" value="Enviar">
  <input type="submit" name="preview" value="Vista previa">
</form></body></html>
"""

GUEST = """<html><head><script>_userdata['user_id'] = 1;</script></head>
<body><div class="post"><div class="postbody">no form here</div></div></body></html>"""


# --------------------------------------------------------------- targets

def test_only_topic_urls_on_allowed_hosts(th):
    assert th.normalise("https://dark-decade.foroactivo.com/t12294-pont-aeri") \
        == "https://dark-decade.foroactivo.com/t12294-pont-aeri"
    # ?highlight= comes from a search result and is harmless; anything else
    # in the query string is an action, not a topic.
    assert th.normalise(
        "https://dark-decade.foroactivo.com/t12294-x?highlight=pont").endswith("/t12294-x")
    for bad in ("https://patched.to/Thread-x",
                "https://dark-decade.foroactivo.com/f12-chasis",
                "https://dark-decade.foroactivo.com/u77",
                "https://dark-decade.foroactivo.com/t12294-x?eval=plus&p_vote=1",
                ""):
        with pytest.raises(th.Refused):
            th.normalise(bad)


def test_a_topic_is_queued_once(th):
    from crawler import db
    url = "https://dark-decade.foroactivo.com/t1-a"
    assert db.thanks_add(url) is True
    assert db.thanks_add(url) is False
    assert len(db.thanks_pending()) == 1
    db.thanks_set(url, state="posted")
    assert db.thanks_pending() == []


# ------------------------------------------------------------ page reading

def test_viewer_and_guest(th):
    who = th.viewer(TOPIC)
    assert who["user_id"] == "4242" and who["username"] == "elitemakina"
    assert who["logged_in"]
    # user_id 1 is the guest account on every Forumotion board.
    assert not th.viewer(GUEST)["logged_in"]
    assert not th.viewer("")["logged_in"]


def test_already_replied_only_counts_author_position(th):
    assert not th.already_replied(TOPIC, "4242")
    mine = TOPIC.replace('<a href="/u77">otro_usuario</a>',
                         '<a href="/u4242">elitemakina</a>')
    assert th.already_replied(mine, "4242")
    # The same name quoted inside someone else's post is not a reply.
    quoted = TOPIC.replace("Aqui va el enlace",
                           '<blockquote><a href="/u4242">elitemakina</a></blockquote>')
    assert not th.already_replied(quoted, "4242")
    assert not th.already_replied(TOPIC, "")


def test_form_is_read_off_the_page_not_invented(th):
    form = th.find_form(TOPIC, "https://dark-decade.foroactivo.com/t12294-x")
    assert form["action"] == "https://dark-decade.foroactivo.com/post"
    f = form["fields"]
    # The token and the post timer are carried through verbatim.
    assert f["tid"] == "9f3c1ab77e" and f["lt"] == "0" and f["t"] == "12294"
    assert f["mode"] == "reply" and "message" in f
    # The submit that was "pressed" is kept; preview is a different action.
    assert f["post"] == "Enviar" and "preview" not in f
    # Never submitted: an upload we are not making, or an unticked box.
    assert "fileupload" not in f and "notify" not in f
    assert f["attach_sig"] == "1"


def test_no_form_means_no_guess(th):
    assert th.find_form(GUEST, "https://dark-decade.foroactivo.com/t1-a") is None


# ----------------------------------------------------------------- posting

def _serve(monkeypatch, th, html):
    monkeypatch.setattr(th.fetch, "get", lambda url, conditional=True: (200, html, {}))


def test_dry_run_sends_nothing(th, monkeypatch):
    _serve(monkeypatch, th, TOPIC)
    sent = []
    monkeypatch.setattr(th.fetch, "post", lambda *a, **k: sent.append(a))
    th.set_dry_run(True)
    res = th.post_one("https://dark-decade.foroactivo.com/t12294-x")
    assert res["state"] == "dry-run" and res["posted"] is False
    assert sent == []
    assert res["message"] == "gracias amigo"


def test_live_post_carries_the_token_and_the_message(th, monkeypatch):
    _serve(monkeypatch, th, TOPIC)
    seen = {}

    def fake_post(url, data, referer="", respect_robots=True):
        seen.update(url=url, data=data, referer=referer)
        return 200

    monkeypatch.setattr(th.fetch, "post", fake_post)
    th.set_dry_run(False)
    res = th.post_one("https://dark-decade.foroactivo.com/t12294-x")
    assert res["posted"] and res["state"] == "posted"
    assert seen["data"]["message"] == "gracias amigo"
    assert seen["data"]["tid"] == "9f3c1ab77e"
    # A forum checks the Referer on a form post; it is the topic we replied to.
    assert seen["referer"].endswith("/t12294-x")

    from crawler import db
    row = db.thanks_get("https://dark-decade.foroactivo.com/t12294-x")
    assert row["state"] == "posted" and row["posted_at"]

    # Asked again, it does not post a second time.
    seen.clear()
    again = th.post_one("https://dark-decade.foroactivo.com/t12294-x")
    assert again["posted"] is False and seen == {}


def test_a_topic_already_replied_to_is_left_alone(th, monkeypatch):
    mine = TOPIC.replace('<a href="/u77">otro_usuario</a>',
                         '<a href="/u4242">elitemakina</a>')
    _serve(monkeypatch, th, mine)
    monkeypatch.setattr(th.fetch, "post", lambda *a, **k: pytest.fail("posted twice"))
    th.set_dry_run(False)
    res = th.post_one("https://dark-decade.foroactivo.com/t12294-x")
    assert res["posted"] is False and res["state"] == "posted"


def test_a_lapsed_session_refuses_rather_than_posting_as_guest(th, monkeypatch):
    _serve(monkeypatch, th, GUEST)
    monkeypatch.setattr(th.fetch, "post", lambda *a, **k: pytest.fail("posted as guest"))
    th.set_dry_run(False)
    with pytest.raises(th.Refused):
        th.post_one("https://dark-decade.foroactivo.com/t12294-x")


def test_disabled_never_posts(th, monkeypatch):
    _serve(monkeypatch, th, TOPIC)
    monkeypatch.setattr(th.fetch, "post", lambda *a, **k: pytest.fail("posted while off"))
    th.ENABLED = False
    th.set_dry_run(False)
    with pytest.raises(th.Refused):
        th.post_one("https://dark-decade.foroactivo.com/t12294-x")
    with pytest.raises(th.Refused):
        th.runner.start()


def test_inspect_reports_without_writing(th, monkeypatch):
    _serve(monkeypatch, th, TOPIC)
    monkeypatch.setattr(th.fetch, "post", lambda *a, **k: pytest.fail("preview posted"))
    monkeypatch.setattr(th.fetch, "robots_ok", lambda url: (True, ""))
    d = th.inspect("https://dark-decade.foroactivo.com/t12294-x")
    assert d["logged_in"] and d["form_found"] and d["would_post"]
    assert d["already_replied"] is False
    assert "tid" in d["fields"]


def test_robots_refusal_is_explained(th, monkeypatch):
    _serve(monkeypatch, th, TOPIC)
    monkeypatch.setattr(th.fetch, "robots_ok", lambda url: (False, ""))
    th.ROBOTS = "respect"
    d = th.inspect("https://dark-decade.foroactivo.com/t12294-x")
    assert d["would_post"] is False and "CW_THANKS_ROBOTS=ignore" in d["robots_note"]


# ------------------------------------------------------------------ pacing

def test_three_posts_then_a_quiet_window(th, monkeypatch):
    """The owner's pacing: a burst of three, then nothing for 25 minutes."""
    from crawler import db
    for i in range(7):
        db.thanks_add(f"https://dark-decade.foroactivo.com/t{i}-x")

    posted, waits = [], []

    def fake_post_one(url, message=None, force=False):
        posted.append(url)
        db.thanks_set(url, state="posted", posted_at=db.now())
        return {"url": url, "posted": True, "state": "posted"}

    def fake_wait(self, seconds):
        waits.append(seconds)
        if len(waits) >= 6:       # two full cycles is enough to see the shape
            self._stop.set()
            return False
        return True

    monkeypatch.setattr(th, "post_one", fake_post_one)
    monkeypatch.setattr(th.Runner, "_wait", fake_wait)
    th.BURST, th.WINDOW, th.SPACING = 3, 1500.0, (40.0, 90.0)
    th.set_dry_run(False)          # the window only governs real posts
    th.runner._loop()

    assert len(posted) == 6                      # 3 + 3, not 7 in one go
    spacing = [waits[0], waits[1], waits[3], waits[4]]
    assert all(40.0 <= w <= 90.0 for w in spacing), spacing
    assert waits[2] > 1400 and waits[5] > 1400   # the rest of the 25 minutes


def test_the_daily_ceiling_stops_it(th, monkeypatch):
    from crawler import db
    for i in range(10):
        db.thanks_add(f"https://dark-decade.foroactivo.com/t{i}-x")

    posted, waits = [], []
    monkeypatch.setattr(th, "post_one", lambda url, message=None, force=False: (
        posted.append(url), db.thanks_set(url, state="posted"),
        {"url": url, "posted": True, "state": "posted"})[-1])

    def fake_wait(self, seconds):
        waits.append(seconds)
        if len(waits) >= 4:
            self._stop.set()
            return False
        return True

    monkeypatch.setattr(th.Runner, "_wait", fake_wait)
    th.BURST, th.WINDOW, th.SPACING, th.DAILY_MAX = 3, 1500.0, (1.0, 1.0), 3
    th.set_dry_run(False)
    th.runner.posted_today = 0
    th.runner._loop()
    assert len(posted) == 3
    assert th.runner.state == "capped"


def test_nothing_runs_at_import(monkeypatch):
    """The guarantee the whole design rests on: importing starts nothing.

    A fresh module and a fresh database rather than the fixture's: the other
    tests drive that runner and flip its settings on purpose, and this is
    about what a cold install does.
    """
    monkeypatch.setenv("CW_DB_PATH", os.path.join(tempfile.mkdtemp(), "cold.db"))
    monkeypatch.delenv("CW_THANKS_ENABLED", raising=False)
    from crawler import db as db_mod
    importlib.reload(db_mod)
    db_mod.init_settings()
    import crawler.thanks as cold
    cold = importlib.reload(cold)
    assert cold.ENABLED is False          # off unless the env says otherwise
    assert cold.runner.running() is False
    assert cold.runner.state == "idle"
    assert cold.dry_run() is True         # and dry even once it is on


def test_a_dry_run_walks_the_queue_instead_of_repeating_its_head(th, monkeypatch):
    """A dry run leaves rows pending so turning it off still posts them --
    which means the runner has to remember what it has already previewed, or
    it previews topic one three times a cycle and never sees topic two."""
    from crawler import db
    for i in range(4):
        db.thanks_add(f"https://dark-decade.foroactivo.com/t{i}-x")

    seen, waits = [], []
    monkeypatch.setattr(th, "post_one", lambda url, message=None, force=False: (
        seen.append(url), {"url": url, "posted": False, "state": "dry-run"})[-1])
    monkeypatch.setattr(th.Runner, "_wait", lambda self, s: waits.append(s) or True)
    th.BURST, th.WINDOW, th.SPACING, th.DAILY_MAX = 3, 1500.0, (1.0, 1.0), 24
    th.set_dry_run(True)
    th.runner._previewed.clear()
    th.runner._loop()

    assert seen == sorted(set(seen)), f"a topic was previewed twice: {seen}"
    assert len(seen) == 4               # the whole queue, once
    # Nothing was posted, so every row is still there to post for real later.
    assert len(db.thanks_pending()) == 4
    assert "turn dry run off" in th.runner.note


def test_coming_off_dry_run_does_not_skip_what_was_previewed(th):
    """The topics you just previewed are the ones you want posted."""
    th.set_dry_run(True)
    th.runner._previewed.add("https://dark-decade.foroactivo.com/t1-a")
    th.set_dry_run(False)
    assert th.runner._previewed == set()


def test_a_dry_run_is_not_slowed_by_the_posting_window(th, monkeypatch):
    from crawler import db
    for i in range(6):
        db.thanks_add(f"https://dark-decade.foroactivo.com/t{i}-x")
    waits = []
    monkeypatch.setattr(th, "post_one", lambda url, message=None, force=False:
                        {"url": url, "posted": False, "state": "dry-run"})
    monkeypatch.setattr(th.Runner, "_wait", lambda self, s: waits.append(s) or True)
    th.BURST, th.WINDOW, th.SPACING = 3, 1500.0, (40.0, 90.0)
    th.set_dry_run(True)
    th.runner._previewed.clear()
    th.runner._loop()
    assert waits == [], f"a dry run waited {waits} for posts it never sent"
