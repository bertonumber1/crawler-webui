"""crawler-webui — URL watcher, crawler and notifier. FastAPI + SSE on :8096."""
import asyncio, json, os, re, sys, threading, time, uuid
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawler import (cookies, db, crawljob, direct, fetch, history, hosts, jdapi, jdstate, linkcheck, postdates,
                     links as linkmod, pagination, resolve as resolvemod, thanks,
                     trace)
from crawler.forum_detector import detect as detect_software
from crawler.forum_parser import parse as parse_forum
from crawler.forum_parser import smf_id, smf_topic_url
from crawler.providers import base, web, dryrun, forum, wordpress   # noqa: F401  (registers them)
from crawler.ui import PAGE

app = FastAPI(title="crawler-webui")
db.init()
db.init_cache()
db.init_thanks()
history.init()
_bus: list[asyncio.Queue] = []
# Topics the last crawl opened that carried no links at all. Kept only
# in memory: it is a hand-off to the Thanks panel, not a record.
_zero_link_topics: list[dict] = []


def emit(kind, payload):
    for q in list(_bus):
        try:
            q.put_nowait({"kind": kind, "payload": payload, "ts": db.now()})
        except asyncio.QueueFull:
            pass


class CrawlIn(BaseModel):
    url: str
    # "auto" reads SMF boards with the forum reader (post dates, signatures,
    # newest-first threads) and everything else with "web", as before.
    provider: str = "web"
    pages: int = 1              # follow the pager this many pages; 0 = every page (the UI default)
    include_seen: bool = False  # keep links already sent to JD
    send_each: bool = False     # queue each release in JD the moment it is read
    auto_start: bool = False
    lossless_only: bool = False # open only topics whose title says FLAC/WAV/lossless
    stop_when_caught_up: bool = False  # stop paging at the first page with nothing new
    # Only posts newer than this many months. None = the saved default
    # (Settings, CW_SINCE_MONTHS); 0 = no limit. Applies to every site: posts
    # and board rows provably older are skipped, listings stop paging once a
    # whole page is older, and long SMF threads are read from the end back.
    since_months: int | None = None


class CrawlStartIn(CrawlIn):
    urls: list[str] = []        # several listings, crawled one after another


class WatchIn(BaseModel):
    url: str
    provider: str = "web"
    poll_interval: int = 1800


class ProxyIn(BaseModel):
    proxy: str = ""


class JobIn(BaseModel):
    urls: list[str]
    name: str = "crawler-webui"
    subfolder: str = ""
    auto_start: bool = False


class RetryIn(BaseModel):
    urls: list[str]


class ProbeIn(BaseModel):
    url: str


class ForgetIn(BaseModel):
    keys: list[str]
    all: bool = False           # forget every row, not just the ones on screen


class HostsIn(BaseModel):
    keys: list[str]


class CookiesIn(BaseModel):
    text: str = ""


class StealthIn(BaseModel):
    os: str


class ThanksTargetsIn(BaseModel):
    urls: list[str]


class ThanksUrlIn(BaseModel):
    url: str
    force: bool = False


class ThanksSettingsIn(BaseModel):
    dry_run: bool | None = None
    messages: str | None = None


class ResolveIn(BaseModel):
    url: str
    provider: str = "web"
    limit: int = 40
    send: bool = False          # write the resolved links straight to JD
    auto_start: bool = False
    per_release: bool = True    # one JD package per release, not one big pile


@app.get("/health")
def health():
    jd_ok, jd_detail = crawljob.available()
    return {"ok": True, "jdownloader": {"ok": jd_ok, "detail": jd_detail},
            "data_dir": os.environ.get("CW_DATA_DIR", "default")}


@app.get("/api/providers")
def providers():
    return {"providers": base.names()}


@app.post("/api/crawl")
def crawl(inp: CrawlIn):
    """Synchronous crawl (tests, scripts). The UI uses /api/crawl/start."""
    return _run_crawl(inp)


def _parse_page(provider, body: str, url: str, since=None):
    """Parse a listing page from the body already fetched; only fall back to
    the provider's own fetch when that finds nothing. Fetching it a second
    time doubled the requests, and the second, conditional fetch could come
    back 304 -- a page read as empty."""
    if hasattr(provider, "_parse_html"):
        try:
            got = (provider._parse_html(body, url, since=since) if since is not None
                   and "since" in provider._parse_html.__code__.co_varnames
                   else provider._parse_html(body, url))
            if any(i.links for i in got):
                return got
        except Exception:
            pass
    return provider.poll(url)


def _pick_provider(url: str) -> str:
    """'forum' for an SMF board (recognised from its URL, else from the page),
    'wordpress' for a WordPress site (read through its REST API), 'web' for
    everything else -- the reader every other site already uses."""
    if smf_id(url):
        return "forum"
    try:
        status, body, _ = fetch.get(url, conditional=False)
    except Exception as e:
        # Never guess: the generic reader on an unread page is how a whole
        # site went to JD as one wrongly named package.
        raise HTTPException(502, f"could not read {url} to choose a reader: "
                                 f"{type(e).__name__}: {e}")
    # A page that says it is WordPress (its generator tag, its REST links)
    # is; the forum signatures are guesses and misread WordPress themes --
    # ravetapepacks.com scored as "Invision Community".
    if wordpress.is_wordpress(body or ""):
        name = "WordPress"
    else:
        found = detect_software(body or "")
        name = found[0].software if found else ""
    trace.event("crawl", "auto provider", url=url, software=name or "none")
    return {"SMF": "forum", "WordPress": "wordpress"}.get(name, "web")


def since_months_default() -> int:
    """The saved window (Settings), else CW_SINCE_MONTHS, else 0 = no limit."""
    try:
        raw = db.setting("since_months", "")
    except Exception:       # a fresh database without the settings table yet
        raw = ""
    if str(raw).strip().lstrip("-").isdigit():
        return max(0, int(raw))
    return postdates.DEFAULT_MONTHS


def _since(inp) -> "postdates._dt.date | None":
    months = inp.since_months if inp.since_months is not None else since_months_default()
    return postdates.cutoff(months)


def _page_is_older(body: str, since) -> bool:
    """Every date on this listing page is before the window: a newest-first
    listing has nothing newer further on. A page with no readable dates never
    counts as old."""
    if since is None:
        return False
    newest = postdates.newest(body)
    return bool(newest and newest < since)


def _caught_up(got, inp) -> bool:
    """Every release this page links to has already been fully sent."""
    if any(linkmod.is_download_host(l.get("url", "")) for i in got for l in i.links):
        return False
    cands = resolvemod.candidates(got, inp.url, 10 ** 6, lossless_only=inp.lossless_only)
    if not cands:
        return False
    done = history.done_releases(c["url"] for c in cands)
    return all(c["url"].rstrip("/").lower() in done for c in cands)


def _run_crawl(inp: CrawlIn, cancel: threading.Event | None = None):
    """Crawl once, resolve to file hosts, and say what is genuinely new.

    Three things happen that did not before: the pager is followed when asked,
    every link is checked against what we have already sent, and the result is
    recorded so the next crawl of the same URL can be compared to this one.
    """
    t0 = time.time()
    provider = base.get(_pick_provider(inp.url) if inp.provider == "auto" else inp.provider)
    since = _since(inp)
    trace.event("crawl", "start", url=inp.url, provider=inp.provider,
                pages=inp.pages, since=str(since) if since else "no limit")

    pages_read, detected = 0, ""
    caught_up = False
    items = []
    sent = {"packages": 0, "links": 0, "failed": 0}

    def send_release(title: str, links: list[dict]):
        """Queue one release's new links in JD now. Never raises: one failed
        package must not stop the crawl reading the rest."""
        if not inp.send_each:
            return
        fresh = [l for l in history.annotate(linkmod.dedupe_download_links(links))
                 if not l["seen_before"] and linkmod.is_download_host(l["url"])]
        if inp.lossless_only:
            # Ask the host: a dead link or one whose real files are MP3 never
            # reaches JD. Hosts that cannot be asked pass; JD's own check decides.
            kept = []
            for l in fresh:
                ok, res = linkcheck.keep_for_lossless(l["url"], title or "")
                if ok:
                    kept.append(l)
                else:
                    trace.event("linkcheck", "dropped", url=l["url"], title=(title or "")[:60],
                                alive=res.get("alive"), verdict=res.get("verdict"), why=res.get("why"))
            fresh = kept
        if not fresh:
            return
        name = title or inp.url
        # A provider that knows each file's proper name (and folder) says so
        # on the link; JD then saves it under that name, not the upload name.
        names = {l["url"]: l["filename"] for l in fresh if l.get("filename")}
        folder = next((l["folder"] for l in fresh if l.get("folder")), "")
        try:
            extra = ({"names": names, "source_url": next(
                         (l["source"] for l in fresh if l.get("source")), "")}
                     if names else {})
            _queue([l["url"] for l in fresh], folder or name,
                   auto_start=inp.auto_start, **extra)
            sent["packages"] += 1
            sent["links"] += len(fresh)
            emit("sent_release", {"title": name, "count": len(fresh)})
        except Exception as e:
            sent["failed"] += 1
            trace.event("crawl", "send failed", title=name[:60],
                        error=f"{type(e).__name__}: {e}")
            emit("sent_release", {"title": name, "count": 0, "error": str(e)})

    windowed = since is not None and bool(smf_topic_url(inp.url))
    try:
        if getattr(provider, "reads_whole_site", False):
            # The provider pages through the site's own API; the date window
            # is applied there, post by post.
            items = provider.poll(inp.url, since=since)
            pages_read = 1
            if inp.send_each:
                for item in items:
                    if cancel is not None and cancel.is_set():
                        trace.event("crawl", "stopped by user", url=inp.url)
                        break
                    send_release(item.name, item.links)
                    emit("resolve_progress", {"done": items.index(item) + 1,
                                              "total": len(items), "title": item.name})
        elif inp.pages != 1 or windowed:
            # Follow the pager through fetch.get so robots and Crawl-delay
            # still apply; each page is parsed by the chosen provider.
            # An SMF thread with a date window is read from its newest page
            # back to the window's start; anything else follows the pager.
            pages_iter = (resolvemod.smf_window_pages(inp.url, since)
                          if since is not None and smf_topic_url(inp.url)
                          else pagination.walk(
                              inp.url, lambda u: fetch.get(u, conditional=False),
                              max_pages=inp.pages))
            for page_url, body in pages_iter:
                if cancel is not None and cancel.is_set():
                    trace.event("crawl", "stopped by user", url=page_url, pages=pages_read)
                    break
                pages_read += 1
                if not windowed and _page_is_older(body, since):
                    trace.event("crawl", "page older than the date window: stopping",
                                url=page_url, since=str(since))
                    break
                try:
                    got = _parse_page(provider, body, page_url, since=since)
                except Exception as e:
                    trace.event("crawl", "page failed", url=page_url,
                                error=f"{type(e).__name__}: {e}")
                    continue
                items.extend(got)
                trace.event("crawl", "page parsed", url=page_url, items=len(got))
                # A thread's posts ARE the releases: send this page's now
                # rather than after the last page. A later pass finds them
                # already in the sent history and sends nothing twice.
                if inp.send_each and any(linkmod.is_download_host(l.get("url", ""))
                                         for i in got for l in i.links):
                    for item in got:
                        send_release(item.name, item.links)
                emit("resolve_progress", {"done": pages_read, "total": 0,
                                          "title": f"page {pages_read} read · {len(items)} release(s) so far"})
                if inp.stop_when_caught_up and not inp.include_seen and _caught_up(got, inp):
                    caught_up = True
                    trace.event("crawl", "caught up: every release on this page was already sent",
                                url=page_url, pages=pages_read)
                    break
        else:
            items = (provider.poll(inp.url, since=since) if since is not None
                     and "since" in provider.poll.__code__.co_varnames
                     else provider.poll(inp.url))
            pages_read = 1
    except Exception as e:
        trace.event("crawl", "failed", url=inp.url, error=f"{type(e).__name__}: {e}")
        raise HTTPException(400, f"{type(e).__name__}: {e}")

    direct = any(linkmod.is_download_host(l.get("url", ""))
                 for item in items for l in item.links)
    if direct and not getattr(provider, "reads_whole_site", False):
        # (A whole-site provider's releases were sent as they were read.)
        for item in items:
            send_release(item.name, item.links)
    resolved = False
    if not direct and not getattr(provider, "reads_whole_site", False):
        def progress(i, total, title):
            emit("resolve_progress", {"done": i, "total": total, "title": title})
        try:
            res = resolvemod.resolve(
                # 40 was the whole budget when a crawl was one page; with the
                # pager followed, every release found is worth opening.
                items, inp.url, limit=max(40, len(items)), on_progress=progress,
                on_release=lambda rel: send_release(rel.get("title") or rel.get("url", ""),
                                                    rel["links"]),
                lossless_only=inp.lossless_only,
                skip_done=not inp.include_seen, cancel=cancel, since=since)
        except Exception as e:
            raise HTTPException(400, f"{type(e).__name__}: {e}")
        if res.get("hopped"):
            from crawler.models import Item
            rebuilt = []
            zero = []
            for rel in res.get("releases", []):
                links = rel.get("links", [])
                if not links and not rel.get("error"):
                    # Read fine, parsed fine, carried no links: on a board
                    # with a thanks gate this is exactly the gated topic, and
                    # the Thanks panel offers to queue this list.
                    zero.append({"url": rel.get("url", ""),
                                 "title": rel.get("title", "")})
                    continue
                rebuilt.append(Item(
                    id=rel.get("url", ""),
                    name=rel.get("title") or rel.get("url", ""),
                    url=rel.get("url", ""),
                    kind="release",
                    summary=rel.get("error", ""),
                    links=links,
                    store=inp.provider,
                ))
            items = rebuilt
            resolved = True
            _zero_link_topics[:] = [z for z in zero if z["url"]]

    # Canonicalise to one usable URL per file, then say which we already hold.
    total_links = fresh_links = 0
    for item in items:
        item.links = history.annotate(linkmod.dedupe_download_links(item.links))
        total_links += len(item.links)
        fresh_links += sum(1 for l in item.links if not l["seen_before"])
        if not inp.include_seen:
            item.links = [l for l in item.links if not l["seen_before"]]

    # A release whose links were all sent before is not an error; it is simply
    # done. Keep it only when the caller asked to see everything.
    items = [i for i in items if i.links or (inp.include_seen and i.summary)]

    out = [i.dict() for i in items]
    for d in out:
        d["link_summary"] = linkmod.summarise(d.get("links", []))

    history.record_crawl(inp.url, inp.provider, items=len(out), links=total_links,
                         fresh=fresh_links, pages=pages_read, detected=detected)
    trace.event("crawl", "done", url=inp.url, items=len(out), links=total_links,
                fresh=fresh_links, ms=int((time.time() - t0) * 1000))
    emit("crawl", {"url": inp.url, "count": len(out), "resolved": resolved,
                   "fresh": fresh_links, "total": total_links})
    return {"url": inp.url, "count": len(out), "items": out, "resolved": resolved,
            "pages": pages_read, "links": total_links, "fresh": fresh_links,
            "already_sent": total_links - fresh_links, "sent": sent,
            "caught_up": caught_up,
            "skipped_known": (res.get("skipped_known", 0) if resolved else 0),
            "stopped": bool(cancel is not None and cancel.is_set())}


# ------------------------------------------------------------------ crawl jobs
# A whole genre is hundreds of pages and hours of pacing: far too long for one
# browser request. A crawl runs here in a thread; the page watches it over SSE,
# picks it up again after a refresh, and can stop it.
_job: dict = {}
_job_lock = threading.Lock()


def _job_view(full: bool = False) -> dict:
    d = {k: v for k, v in _job.items() if k not in ("cancel", "results")}
    res = _job.get("results", [])
    d["finished_urls"] = len(res)
    if full:
        items = [it for r in res for it in r.get("items", [])]
        d["result"] = {
            "count": len(items), "items": items,
            "pages": sum(r.get("pages", 0) for r in res),
            "links": sum(r.get("links", 0) for r in res),
            "fresh": sum(r.get("fresh", 0) for r in res),
            "already_sent": sum(r.get("already_sent", 0) for r in res),
            "skipped_known": sum(r.get("skipped_known", 0) or 0 for r in res),
            "caught_up": any(r.get("caught_up") for r in res),
            "sent": {k: sum((r.get("sent") or {}).get(k, 0) for r in res)
                     for k in ("packages", "links", "failed")},
            "errors": [f'{r["url"]}: {r["error"]}' for r in res if r.get("error")],
        }
    return d


@app.post("/api/crawl/start")
def crawl_start(inp: CrawlStartIn):
    urls = [u.strip() for u in (inp.urls or [inp.url]) if u and u.strip()]
    if not urls:
        raise HTTPException(400, "no URL")
    with _job_lock:
        if _job.get("state") == "running":
            raise HTTPException(409, "a crawl is already running: stop it or wait for it")
        cancel = threading.Event()
        _job.clear()
        _job.update(id=uuid.uuid4().hex[:8], state="running", urls=urls, index=0,
                    url=urls[0], started=db.now(), finished="", results=[], cancel=cancel)
    base_args = inp.model_dump(exclude={"urls"})

    def run():
        try:
            for i, u in enumerate(urls):
                if cancel.is_set():
                    break
                _job.update(index=i, url=u)
                emit("crawl_job", _job_view())
                try:
                    r = _run_crawl(CrawlIn(**{**base_args, "url": u}), cancel)
                except HTTPException as e:
                    r = {"url": u, "error": str(e.detail), "items": []}
                except Exception as e:
                    r = {"url": u, "error": f"{type(e).__name__}: {e}", "items": []}
                _job["results"].append(r)
        finally:
            _job.update(state="stopped" if cancel.is_set() else "done", finished=db.now())
            emit("crawl_job", _job_view())

    threading.Thread(target=run, daemon=True, name="crawl-job").start()
    return _job_view()


@app.get("/api/crawl/job")
def crawl_job():
    return _job_view(full=True) if _job else {"state": "idle"}


@app.post("/api/crawl/job/stop")
def crawl_job_stop():
    if _job.get("state") != "running":
        return {"ok": False, "state": _job.get("state", "idle")}
    _job["cancel"].set()
    trace.event("crawl", "stop requested")
    return {"ok": True}


@app.post("/api/check")
def check(inp: CrawlIn):
    p = base.get(inp.provider)
    return {"checks": [{"name": n, "ok": o, "detail": d} for n, o, d in p.check(inp.url)]}


@app.get("/api/watches")
def watches():
    return {"watches": db.list_watches()}


@app.post("/api/watches")
def add_watch(inp: WatchIn):
    p = base.get(inp.provider)
    try:
        name = p.resolve(inp.url)
        current = p.poll(inp.url)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    wid = db.add_watch(inp.provider, inp.url, name, inp.poll_interval)
    n = db.baseline(current, wid)
    emit("watch_added", {"id": wid, "name": name, "baselined": n})
    return {"id": wid, "name": name, "baselined": n,
            "note": f"{n} existing items marked seen; only new ones will alert"}


@app.delete("/api/watches/{wid}")
def del_watch(wid: int):
    db.delete_watch(wid)
    emit("watch_removed", {"id": wid})
    return {"ok": True}


@app.post("/api/watches/{wid}/test")
def test_watch(wid: int):
    w = db.get_watch(wid)
    if not w:
        raise HTTPException(404, "no such watch")
    p = base.get(w["store"])
    return {"checks": [{"name": n, "ok": o, "detail": d} for n, o, d in p.check(w["ref"])]}


@app.get("/api/queue")
def queue(status: str | None = None):
    return {"counts": db.queue_counts(), "items": db.queue_items(state=status)}


@app.get("/api/settings")
def get_settings():
    return {"proxy": fetch.current_proxy()}


@app.post("/api/settings")
def put_settings(inp: ProxyIn):
    try:
        val = fetch.set_proxy(inp.proxy)
    except ValueError as e:
        raise HTTPException(400, str(e))
    emit("proxy", {"proxy": val or "direct"})
    return {"proxy": val, "note": "robots + rate-limit state cleared for the new egress"}


class SinceIn(BaseModel):
    months: int = 0


@app.get("/api/settings/since")
def get_since():
    """The default date window for every crawl, in months (0 = no limit)."""
    months = since_months_default()
    d = postdates.cutoff(months)
    return {"months": months, "since": str(d) if d else ""}


@app.post("/api/settings/since")
def put_since(inp: SinceIn):
    if inp.months < 0 or inp.months > 600:
        raise HTTPException(400, "months must be 0-600")
    db.set_setting("since_months", inp.months)
    emit("since", {"months": inp.months})
    return get_since()


@app.post("/api/settings/proxy-test")
def proxy_test():
    """Compare what the far end sees with and without the proxy. If the two
    IPs match, the proxy is not actually carrying your traffic."""
    dok, direct = fetch.egress_ip(False)
    proxy = fetch.current_proxy()
    if not proxy:
        return {"proxy": "", "direct": direct, "proxied": None,
                "verdict": "no proxy set — all traffic goes out directly"}
    pok, proxied = fetch.egress_ip(True)
    if not pok:
        verdict = "proxy unreachable — " + proxied
    elif not dok:
        verdict = f"proxy reachable, egress {proxied} (direct check failed)"
    elif proxied == direct:
        verdict = "WARNING: same IP with and without the proxy — traffic is NOT being proxied"
    else:
        verdict = f"OK — proxied traffic exits from {proxied}, not {direct}"
    return {"proxy": proxy, "direct": direct, "proxied": proxied, "verdict": verdict}


@app.get("/api/jd/rules")
def jd_rules_info():
    return {"types":["DEEPDECRYPT","REWRITE","DIRECTHTTP","FOLLOWREDIRECT","SUBMITFORM"],"schema":"jd_rules/jd2mcr.schema.json","note":"JDownloader calls the deep-crawl rule DEEPDECRYPT (not DEEPCRYPT)."}

class JDRulesIn(BaseModel):
    rules: list[dict]

@app.post("/api/jd/rules/validate")
def jd_rules_validate(inp: JDRulesIn):
    from . import jd_rules
    return jd_rules.validate(inp.rules)

@app.post("/api/jd/rules/save")
def jd_rules_save(inp: JDRulesIn):
    from . import jd_rules
    path=os.environ.get("CW_JD_RULES_PATH",os.path.join(os.path.dirname(os.path.dirname(__file__)),"jd_rules","custom.linkcrawlerrules.json"))
    try: saved=jd_rules.save(inp.rules,path)
    except Exception as e: raise HTTPException(400,str(e))
    emit("jd_rules_saved",{"path":saved,"count":len(inp.rules)})
    return {"ok":True,"path":saved,"count":len(inp.rules)}

@app.post("/api/retry")
def retry_failed(inp: RetryIn):
    from crawler.models import Item
    urls = list(dict.fromkeys(u for u in inp.urls if u))
    if not urls:
        return {"items": []}
    def one(url):
        try:
            found = resolvemod.page_links(url)
            return Item(id=url, name=url, url=url, kind="release", links=found, store="web")
        except Exception as e:
            return {"id": url, "name": url, "url": url, "kind": "release", "links": [], "store": "web", "error": f"{type(e).__name__}: {e}"}
    # Retry failed releases concurrently using the same bounded resolver setting.
    from concurrent.futures import ThreadPoolExecutor
    workers=max(1,min(len(urls),int(os.getenv("CRAWLER_RESOLVE_CONCURRENCY","6"))))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="retry") as pool:
        out=[f.result() for f in [pool.submit(one,u) for u in urls]]
    for d in out:
        d["link_summary"] = linkmod.summarise(d.get("links", []))
    return {"items": out}

@app.post("/api/resolve")
def resolve_links(inp: ResolveIn):
    """Crawl a page, then follow its release pages to the actual file hosts.

    A tag or genre listing carries no downloadable links -- only links to
    release pages -- so sending it to JD queues nothing. This does the second
    hop and returns what JD can really take.
    """
    try:
        p = base.get(inp.provider)
        items = p.poll(inp.url)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")

    def progress(i, total, title):
        emit("resolve_progress", {"done": i, "total": total, "title": title})

    try:
        res = resolvemod.resolve(items, inp.url, limit=inp.limit, on_progress=progress)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")

    res["url"] = inp.url
    res["count"] = len(res["links"])
    emit("resolve", {"url": inp.url, "count": res["count"],
                     "followed": res.get("followed", 0)})

    if inp.send and res["links"]:
        jobs = []
        if inp.per_release and res.get("releases"):
            for r in res["releases"]:
                if not r["links"]:
                    continue
                job = crawljob.write([l["url"] for l in r["links"]],
                                     r["title"] or inp.url,
                                     auto_start=inp.auto_start)
                history.record_sent(r["links"], package=r["title"] or inp.url,
                                    job_path=job, source_url=r.get("url", ""),
                                    title=r["title"] or "")
                jobs.append(job)
        else:
            job = crawljob.write([l["url"] for l in res["links"]],
                                 inp.url, auto_start=inp.auto_start)
            history.record_sent(res["links"], package=inp.url, job_path=job,
                                source_url=inp.url)
            jobs.append(job)
        res["queued"] = jobs
        emit("crawljob", {"count": len(res["links"]), "jobs": len(jobs)})
    return res


@app.on_event("startup")
def _resume_jd_names():
    """Renames left pending by a restart carry on."""
    try:
        if jdapi.configured() and jdapi.pending_names()["packages"]:
            jdapi._ensure_worker()
    except Exception:
        pass


@app.on_event("startup")
def _resume_direct():
    try:
        if direct.status()["counts"].get("queued"):
            direct.ensure_worker()
    except Exception:
        pass


@app.get("/api/direct")
def direct_status():
    """The polite fetcher for self-hosting sites: queue counts, current file."""
    return direct.status()


@app.post("/api/direct/retry")
def direct_retry():
    return {"requeued": direct.retry_failed()}


@app.post("/api/direct/stop")
def direct_stop():
    direct.stop()
    return {"ok": True}


@app.post("/api/direct/start")
def direct_start():
    direct.ensure_worker()
    return {"ok": True}


@app.get("/api/jd/names")
def jd_names():
    """Packages still waiting for JD to collect their links to be renamed."""
    return jdapi.pending_names()


@app.get("/api/jd")
def jd_status():
    ok, detail = crawljob.available()
    return {"ok": ok, "detail": detail}


def _queue(urls: list[str], name: str, subfolder: str = "",
           auto_start: bool = False, names: dict | None = None,
           source_url: str = "") -> str:
    """Hand links to JD as one package and record them as sent. Returns the
    job reference. Raises ValueError when no link is on a harvested host."""
    # Prefer the API: it answers at the time. The folderwatch file is the
    # fallback and is fire-and-forget -- JD consumes it and reports
    # nothing, so a rejected job is indistinguishable from an accepted one.
    clean = [u for u in (linkmod.canonical_download_url(x) for x in urls) if u]
    if not clean:
        raise ValueError("no usable links: nothing on a harvested host")

    if all(hosts.is_direct(u) for u in clean):
        # A self-hosting site: fetched one file at a time by the crawler
        # itself (crawler/direct.py), never 60 connections from JD.
        by_canon = {linkmod.canonical_download_url(u) or u: n
                    for u, n in (names or {}).items()}
        n = direct.enqueue([{"url": u, "folder": name, "filename": by_canon.get(u, ""),
                             "referer": source_url} for u in clean], package=name)
        path = f"direct:{n}"
        history.record_sent(
            [{"url": u, "host": linkmod.host_only(u)} for u in clean],
            package=name, job_path=path, title=name)
        return path

    path = ""
    if jdapi.configured():
        folder = subfolder or os.path.join(crawljob.download_root(),
                                           crawljob._safe(name))
        try:
            if names:
                by_canon = {linkmod.canonical_download_url(u) or u: n
                            for u, n in names.items()}
                res = jdapi.add_named([(u, by_canon.get(u, "")) for u in clean],
                                      name, folder=folder, auto_start=auto_start,
                                      source_url=source_url)
            else:
                res = jdapi.add(clean, name, folder=folder, auto_start=auto_start)
            path = f"myjdownloader:job/{res.get('job')}"
        except Exception as e:
            # JD drops off MyJDownloader after a restart of the VPN stack
            # while the folderwatch still works: fall back, don't lose it.
            trace.event("jd", "api add failed, using folderwatch",
                        title=name[:60], error=f"{type(e).__name__}: {e}")
    if not path:
        path = crawljob.write(urls, name, subfolder=subfolder,
                              auto_start=auto_start,
                              names=({linkmod.canonical_download_url(u) or u: n
                                      for u, n in names.items()} if names else None))
    history.record_sent(
        [{"url": u, "host": linkmod.host_only(u)} for u in clean],
        package=name, job_path=path, title=name)
    return path


@app.post("/api/crawljob")
def make_job(inp: JobIn):
    """Write the links you selected into JD's folderwatch folder."""
    try:
        path = _queue(inp.urls, inp.name, subfolder=inp.subfolder,
                      auto_start=inp.auto_start)
    except Exception as e:
        raise HTTPException(400, str(e))
    emit("crawljob", {"path": path, "count": len(inp.urls)})
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return {"ok": True, "path": path, "count": len(inp.urls), "bytes": size,
            "note": ("queued in JDownloader and started" if inp.auto_start else
                     "queued in JDownloader; autoStart is FALSE")}


@app.get("/api/history")
def get_history(limit: int = 200):
    """Everything we have handed to JD, newest first."""
    return {"counts": history.counts(), "items": history.recent(limit)}


@app.post("/api/history/forget")
def forget_history(inp: ForgetIn):
    """Drop rows so those files may be queued again."""
    n = history.forget_all() if inp.all else history.forget(inp.keys)
    emit("history", {"forgotten": n})
    return {"ok": True, "forgotten": n}


@app.get("/api/crawls")
def get_crawls(limit: int = 25):
    return {"crawls": history.crawl_history(limit)}


@app.delete("/api/crawls")
def clear_crawls():
    """Empty the Recent crawls list. Sent-file history is untouched."""
    return {"ok": True, "cleared": history.clear_crawls()}


@app.get("/api/jd/state")
def jd_state():
    """What JD actually holds: live over the API, or its save files."""
    snap = jdstate.best_snapshot()
    snap.pop("keys", None)          # a set is not JSON
    snap["api"] = jdapi.status()
    return snap


@app.post("/api/jd/cleanup")
def jd_cleanup():
    """Remove every link JD has found to be dead, and forget them.

    Both halves matter. Removing them stops dead packages accumulating;
    forgetting them is what lets the release be crawled again, because a
    link recorded as sent suppresses itself forever.
    """
    if not jdapi.configured():
        raise HTTPException(400, "MyJDownloader is not configured; "
                                 "cleanup needs the API, not the save files")
    try:
        res = jdapi.remove_offline()
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    forgotten = history.forget(res.get("keys", []))
    emit("jd_cleanup", {"removed": res["removed"], "forgotten": forgotten})
    return {"ok": True, "removed": res["removed"], "forgotten": forgotten}


class JDRemoveIn(BaseModel):
    packages: list[str]


@app.post("/api/jd/remove")
def jd_remove(inp: JDRemoveIn):
    """Remove named packages from JD."""
    if not jdapi.configured():
        raise HTTPException(400, "MyJDownloader is not configured")
    try:
        res = jdapi.remove_packages(inp.packages)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    emit("jd_remove", res)
    return res


@app.post("/api/jd/reconcile")
def jd_reconcile():
    """Compare our record against JD and update what we believe."""
    res = jdstate.reconcile()
    emit("jd_reconcile", res)
    return res


@app.get("/api/cookies")
def get_cookies():
    """Which sites we currently hold a logged-in session for."""
    return cookies.status()


@app.post("/api/cookies")
def put_cookies(inp: CookiesIn):
    """Accept a pasted Netscape cookies.txt and start using it.

    Pasted as text rather than uploaded because the app runs in a container
    and the browser exporting the cookies does not: a path would have to be
    shared, and the file would then sit on disk in two places.
    """
    try:
        st = fetch.reload_cookies(inp.text)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    if not st["loaded"]:
        raise HTTPException(400, st.get("error") or "no cookies could be read")
    emit("cookies", {"domains": st["domains"], "count": st["count"]})
    return st


@app.delete("/api/cookies")
def drop_cookies():
    st = cookies.clear()
    fetch.reload_cookies()
    emit("cookies", {"domains": [], "count": 0})
    return st


@app.get("/api/trace")
def get_trace(limit: int = 200, stage: str = "", since: int = 0):
    return {"status": trace.status(), "events": trace.tail(limit, stage, since)}


@app.post("/api/trace/clear")
def clear_trace():
    return {"ok": True, "cleared": trace.clear()}


def _stealth_status() -> dict:
    lo, hi = fetch.STEALTH_GAP
    return {"hosts": list(fetch.STEALTH_HOSTS), "os": fetch.stealth_os(),
            "options": sorted(fetch.STEALTH_UAS), "gap": [lo, hi]}


@app.get("/api/stealth")
def get_stealth():
    """Which sites are read like a person browsing, and as which browser."""
    return _stealth_status()


@app.post("/api/stealth")
def set_stealth(inp: StealthIn):
    try:
        fetch.set_stealth_os(inp.os)
    except ValueError as e:
        raise HTTPException(400, str(e))
    trace.event("stealth", "browser OS set", os=inp.os)
    return _stealth_status()


# ------------------------------------------------------------------ thanks
# Everything here is inert until CW_THANKS_ENABLED is set: with it unset the
# status endpoint says so and start refuses, so the UI panel stays hidden and
# nothing can post by accident.


@app.get("/api/thanks")
def thanks_status():
    """What the thank-you runner is, has and would do. Reads nothing remote."""
    st = thanks.runner.status()
    st["zero_link_topics"] = list(_zero_link_topics)
    return st


@app.post("/api/thanks/targets")
def thanks_add_targets(inp: ThanksTargetsIn):
    """Queue topics to be thanked. Validated now so a bad URL is rejected at
    the point it is pasted, not half an hour later inside the runner."""
    added, rejected = [], []
    for raw in inp.urls:
        try:
            url = thanks.normalise(raw)
        except thanks.Refused as e:
            rejected.append({"url": raw, "why": str(e)})
            continue
        if db.thanks_add(url):
            added.append(url)
        else:
            rejected.append({"url": url, "why": "already queued"})
    trace.event("thanks", "targets queued", added=len(added),
                rejected=len(rejected))
    return {"added": added, "rejected": rejected, **thanks.runner.status()}


@app.post("/api/thanks/from-crawl")
def thanks_from_crawl():
    """Queue every topic the last crawl opened and found no links in."""
    return thanks_add_targets(ThanksTargetsIn(
        urls=[z["url"] for z in _zero_link_topics]))


@app.delete("/api/thanks/targets/{tid}")
def thanks_drop_target(tid: int):
    return {"removed": db.thanks_delete(tid), **thanks.runner.status()}


@app.post("/api/thanks/targets/{tid}/retry")
def thanks_retry_target(tid: int):
    return {"requeued": db.thanks_retry(tid), **thanks.runner.status()}


@app.post("/api/thanks/preview")
def thanks_preview(inp: ThanksUrlIn):
    """Read one topic and report exactly what posting would do.

    This is the honest way to prove the path end to end -- session, reply
    form, token, robots -- without leaving a reply on the board.
    """
    try:
        return thanks.inspect(inp.url)
    except thanks.Refused as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.post("/api/thanks/post-one")
def thanks_post_one(inp: ThanksUrlIn):
    """Thank exactly one topic, now. Still obeys dry run."""
    try:
        res = thanks.post_one(inp.url, force=inp.force)
    except thanks.Refused as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    emit("thanks", res)
    return res


@app.post("/api/thanks/settings")
def thanks_settings(inp: ThanksSettingsIn):
    if inp.dry_run is not None:
        thanks.set_dry_run(inp.dry_run)
        trace.event("thanks", "dry run " + ("on" if inp.dry_run else "OFF"))
    if inp.messages is not None:
        thanks.set_messages(inp.messages)
    return thanks.runner.status()


@app.post("/api/thanks/start")
def thanks_start():
    try:
        return thanks.runner.start()
    except thanks.Refused as e:
        raise HTTPException(400, str(e))


@app.post("/api/thanks/stop")
def thanks_stop():
    return thanks.runner.stop()


@app.get("/api/hosts")
def get_hosts():
    """Which file hosts are being harvested, and what else is available."""
    return {
        "enabled": [h.key for h in hosts.enabled()],
        "known": [{"key": h.key, "domain": h.domain, "label": h.label,
                   "premium": h.premium} for h in hosts.KNOWN.values()],
    }


@app.post("/api/hosts")
def set_hosts(inp: HostsIn):
    active = hosts.set_enabled(inp.keys)
    emit("hosts", {"enabled": [h.key for h in active]})
    return {"enabled": [h.key for h in active]}


@app.post("/api/probe")
def probe(inp: ProbeIn):
    """Identify a page without crawling it.

    Answers the question that costs the most time on a new forum: is this
    software we understand, and what would we pull out of it?
    """
    try:
        status, body, headers = fetch.get(inp.url, conditional=False)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")

    detections = [{"software": d.software, "confidence": round(d.confidence, 2),
                   "evidence": list(d.evidence)} for d in detect_software(body)]

    # Use the parser that would actually handle this page. forum_parser knows
    # the forum families; anything else (Drupal, or software we do not
    # recognise) is the web provider's job, and reporting "0 items" because we
    # asked the wrong parser would be a lie about the page.
    items, detection = parse_forum(body, inp.url)
    parser = "forum"
    if not items:
        try:
            items = base.get("web")._parse_html(body, inp.url)
            parser = "web"
        except Exception:
            items = items or []
    by_host: dict[str, int] = {}
    downloadable = 0
    for it in items:
        for l in it.links:
            by_host[l.get("host", "")] = by_host.get(l.get("host", ""), 0) + 1
            downloadable += bool(l.get("downloadable"))

    nxt = pagination.next_page(body, inp.url)
    # A members-only board renders fine to a guest and hides the links, so
    # "0 downloadable" means nothing unless you know which one you were.
    authed = cookies.have_for(inp.url)
    guest_wall = bool(re.search(
        r"sign in to (?:view|see|download)|you (?:do not|don't) have permission"
        r"|members only|to unlock|hidden (?:content|link)", body, re.I))
    trace.event("probe", "page probed", url=inp.url,
                software=(detection.software if detection else "none"),
                items=len(items), downloadable=downloadable)
    return {
        "url": inp.url, "status": status, "bytes": len(body),
        "content_type": headers.get("content-type", ""),
        "detections": detections,
        "parser": parser,
        "best": detection.software if detection else "",
        "items": len(items),
        "downloadable": downloadable,
        "hosts": sorted(by_host.items(), key=lambda kv: -kv[1])[:15],
        "next_page": nxt or "",
        "authenticated": authed,
        "guest_wall": guest_wall,
        "titles": [i.name[:90] for i in items[:12]],
    }


@app.get("/events")
async def events():
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _bus.append(q)

    async def gen():
        try:
            yield f"data: {json.dumps({'kind':'hello','ts':db.now()})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if q in _bus:
                _bus.remove(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
