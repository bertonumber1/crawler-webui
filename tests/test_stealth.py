"""Stealth hosts read like a person; every other host keeps the honest identity."""
import threading

from crawler import fetch


def test_only_listed_hosts_get_browser_headers(monkeypatch):
    monkeypatch.setattr(fetch, "STEALTH_HOSTS", ("foroactivo.com",))
    h = fetch._headers_for("https://dark-decade.foroactivo.com/t1-a")
    assert "Chrome/" in h["User-Agent"] and "crawler-webui" not in h["User-Agent"]
    assert h["Accept"].startswith("text/html")
    assert fetch._headers_for("https://patched.to/Thread-x") == {}
    assert not fetch.is_stealth("https://notforoactivo.com/")


def test_off_when_unset(monkeypatch):
    monkeypatch.setattr(fetch, "STEALTH_HOSTS", ())
    assert fetch._headers_for("https://dark-decade.foroactivo.com/") == {}


def test_gap_range_parsing():
    assert fetch._gap_range("90-150") == (90.0, 150.0)
    assert fetch._gap_range("120") == (120.0, 120.0)
    assert fetch._gap_range("junk") == (90.0, 150.0)


def test_parallel_workers_are_spaced_not_simultaneous(monkeypatch):
    # The resolver follows topics on 6 threads. Each must get its own slot.
    monkeypatch.setattr(fetch, "STEALTH_HOSTS", ("foroactivo.com",))
    monkeypatch.setattr(fetch, "STEALTH_GAP", (0.2, 0.2))
    monkeypatch.setattr(fetch, "_robots", {})
    monkeypatch.setattr(fetch, "_last_hit", {})
    times = []
    lock = threading.Lock()

    def hit():
        fetch._throttle("https://dark-decade.foroactivo.com/t1-a")
        with lock:
            times.append(fetch.time.monotonic())

    threads = [threading.Thread(target=hit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    times.sort()
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= 0.18 for g in gaps), gaps


def test_browser_os_switch(monkeypatch):
    monkeypatch.setattr(fetch, "STEALTH_HOSTS", ("foroactivo.com",))
    monkeypatch.setattr(fetch.db, "set_setting", lambda k, v: None)
    before = fetch.stealth_os()
    try:
        fetch.set_stealth_os("windows")
        assert "Windows NT 10.0" in fetch._headers_for("https://x.foroactivo.com/")["User-Agent"]
        fetch.set_stealth_os("Linux")
        assert "X11; Linux" in fetch._headers_for("https://x.foroactivo.com/")["User-Agent"]
        try:
            fetch.set_stealth_os("mac")
            raise AssertionError("unknown OS accepted")
        except ValueError:
            pass
        assert fetch.stealth_os() == "linux"
    finally:
        fetch._stealth_os = before


def test_cloudflare_beacon_on_a_real_page_is_not_a_challenge():
    from crawler import fetch
    page = "<html><body>" + "<div class='post'>release</div>" * 3000 + \
        '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></body></html>'
    assert fetch._looks_like_challenge(page, {"content-type": "text/html"}) is False
    shell = '<html><title>x</title><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1"></script></html>'
    assert fetch._looks_like_challenge(shell, {"content-type": "text/html"}) is True
    assert fetch._looks_like_challenge("<title>Just a moment...</title>" + "x" * 90000, {}) is True
