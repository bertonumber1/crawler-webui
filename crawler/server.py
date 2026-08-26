"""crawler-webui — URL watcher, crawler and notifier. FastAPI + SSE on :8096."""
import asyncio, json, os, sys, time
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawler import (db, crawljob, fetch, history, hosts, jdstate,
                     links as linkmod, pagination, resolve as resolvemod, trace)
from crawler.forum_detector import detect as detect_software
from crawler.forum_parser import parse as parse_forum
from crawler.providers import base, web, dryrun, forum   # noqa: F401  (registers them)
from crawler.ui import PAGE

app = FastAPI(title="crawler-webui")
db.init()
db.init_cache()
history.init()
_bus: list[asyncio.Queue] = []


def emit(kind, payload):
    for q in list(_bus):
        try:
            q.put_nowait({"kind": kind, "payload": payload, "ts": db.now()})
        except asyncio.QueueFull:
            pass


class CrawlIn(BaseModel):
    url: str
    provider: str = "web"
    pages: int = 1              # follow the pager this many pages
    include_seen: bool = False  # keep links already sent to JD


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


class HostsIn(BaseModel):
    keys: list[str]

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
    """Crawl once, resolve to file hosts, and say what is genuinely new.

    Three things happen that did not before: the pager is followed when asked,
    every link is checked against what we have already sent, and the result is
    recorded so the next crawl of the same URL can be compared to this one.
    """
    t0 = time.time()
    provider = base.get(inp.provider)
    trace.event("crawl", "start", url=inp.url, provider=inp.provider,
                pages=inp.pages)

    pages_read, detected = 0, ""
    items = []
    try:
        if inp.pages > 1:
            # Follow the pager through fetch.get so robots and Crawl-delay
            # still apply; each page is parsed by the chosen provider.
            for page_url, body in pagination.walk(
                    inp.url, lambda u: fetch.get(u, conditional=False),
                    max_pages=inp.pages):
                pages_read += 1
                try:
                    got = provider.poll(page_url)
                except Exception as e:
                    trace.event("crawl", "page failed", url=page_url,
                                error=f"{type(e).__name__}: {e}")
                    continue
                items.extend(got)
                trace.event("crawl", "page parsed", url=page_url, items=len(got))
        else:
            items = provider.poll(inp.url)
            pages_read = 1
    except Exception as e:
        trace.event("crawl", "failed", url=inp.url, error=f"{type(e).__name__}: {e}")
        raise HTTPException(400, f"{type(e).__name__}: {e}")

    direct = any(linkmod.is_download_host(l.get("url", ""))
                 for item in items for l in item.links)
    resolved = False
    if not direct:
        def progress(i, total, title):
            emit("resolve_progress", {"done": i, "total": total, "title": title})
        try:
            res = resolvemod.resolve(items, inp.url, limit=40, on_progress=progress)
        except Exception as e:
            raise HTTPException(400, f"{type(e).__name__}: {e}")
        if res.get("hopped"):
            from crawler.models import Item
            rebuilt = []
            for rel in res.get("releases", []):
                links = rel.get("links", [])
                if not links and not rel.get("error"):
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
            "already_sent": total_links - fresh_links}


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


@app.get("/api/jd")
def jd_status():
    ok, detail = crawljob.available()
    return {"ok": ok, "detail": detail}


@app.post("/api/crawljob")
def make_job(inp: JobIn):
    """Write the links you selected into JD's folderwatch folder."""
    try:
        path = crawljob.write(inp.urls, inp.name, subfolder=inp.subfolder,
                              auto_start=inp.auto_start)
        history.record_sent(
            [{"url": u, "host": linkmod.host_only(u)} for u in inp.urls],
            package=inp.name, job_path=path, title=inp.name)
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
    n = history.forget(inp.keys)
    emit("history", {"forgotten": n})
    return {"ok": True, "forgotten": n}


@app.get("/api/crawls")
def get_crawls(limit: int = 25):
    return {"crawls": history.crawl_history(limit)}


@app.get("/api/jd/state")
def jd_state():
    """What JD actually holds, read from its own list files."""
    snap = jdstate.snapshot()
    snap.pop("keys", None)          # a set is not JSON
    return snap


@app.post("/api/jd/reconcile")
def jd_reconcile():
    """Compare our record against JD and update what we believe."""
    res = jdstate.reconcile()
    emit("jd_reconcile", res)
    return res


@app.get("/api/trace")
def get_trace(limit: int = 200, stage: str = "", since: int = 0):
    return {"status": trace.status(), "events": trace.tail(limit, stage, since)}


@app.post("/api/trace/clear")
def clear_trace():
    return {"ok": True, "cleared": trace.clear()}


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
