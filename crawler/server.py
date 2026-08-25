"""crawler-webui — URL watcher, crawler and notifier. FastAPI + SSE on :8096."""
import asyncio, json, os, sys, time
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawler import db, crawljob, fetch, links as linkmod, resolve as resolvemod
from crawler.providers import base, web, dryrun   # noqa: F401  (registers them)

app = FastAPI(title="crawler-webui")
db.init()
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
    """Crawl once and automatically resolve Drupal listing pages to file hosts."""
    try:
        p = base.get(inp.provider)
        items = p.poll(inp.url)
    except Exception as e:
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
            items = []
            for rel in res.get("releases", []):
                links = rel.get("links", [])
                if not links and not rel.get("error"):
                    continue
                from crawler.models import Item
                items.append(Item(
                    id=rel.get("url", ""),
                    name=rel.get("title") or rel.get("url", ""),
                    url=rel.get("url", ""),
                    kind="release",
                    links=links,
                    store=inp.provider,
                ))
            resolved = True

    out = [i.dict() for i in items]
    for d in out:
        d["link_summary"] = linkmod.summarise(d.get("links", []))
    emit("crawl", {"url": inp.url, "count": len(out), "resolved": resolved})
    return {"url": inp.url, "count": len(out), "items": out, "resolved": resolved}


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
                jobs.append(crawljob.write([l["url"] for l in r["links"]],
                                           r["title"] or inp.url,
                                           auto_start=inp.auto_start))
        else:
            jobs.append(crawljob.write([l["url"] for l in res["links"]],
                                       inp.url, auto_start=inp.auto_start))
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


PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<title>crawler-webui</title><style>
*{box-sizing:border-box}
body{margin:0;background:#0e1116;color:#d5dae1;font:14px/1.5 ui-sans-serif,system-ui,sans-serif}
header{padding:14px 18px;background:#151a21;border-bottom:1px solid #232a34;display:flex;gap:10px;align-items:center}
h1{margin:0;font-size:15px;letter-spacing:.14em;text-transform:uppercase;color:#7fb3ff}
#jd{margin-left:auto;font-size:12px;color:#8b95a3}
main{display:grid;grid-template-columns:320px 1fr;gap:0;height:calc(100vh - 53px)}
aside{border-right:1px solid #232a34;overflow:auto;padding:14px}section{overflow:auto;padding:14px}
.bar{display:flex;gap:8px;margin-bottom:14px;align-items:center}
input,select,button{background:#1a212a;color:#d5dae1;border:1px solid #2c3540;border-radius:6px;padding:8px 10px;font:inherit}
input{flex:1}button{cursor:pointer;border-color:#35506f}button:hover{background:#22303f}button:disabled{opacity:.55;cursor:wait}
button.go{background:#1d4e89;border-color:#2f6fbd;color:#fff}
.card{border:1px solid #232a34;border-radius:8px;padding:12px;margin-bottom:10px;background:#131922}
.card h3{margin:0 0 4px;font-size:14px;color:#e8eef6}.meta{font-size:12px;color:#8b95a3;margin-bottom:8px}
.badges span{display:inline-block;font-size:11px;padding:2px 7px;border-radius:99px;margin:0 4px 4px 0;background:#1f2a36;border:1px solid #2c3a49}
.b-source{color:#8fd6a0}.b-build{color:#ffd479}.b-video{color:#ff9ec4}.b-forum{color:#9fc4ff}.b-other{color:#8b95a3}
.b-rapidgator{color:#ffd479}.links{margin-top:8px;border-top:1px solid #232a34;padding-top:8px}
.lnk{display:flex;gap:8px;align-items:flex-start;font-size:12px;padding:5px 0}.lnk a{color:#7fb3ff;text-decoration:none;word-break:break-all}
.host{min-width:82px;color:#8b95a3}.host.download{color:#ffd479;font-weight:600}.prem{color:#ff8f8f;font-size:11px;border:1px solid #5a2b2b;border-radius:4px;padding:0 5px}
.watch{border:1px solid #232a34;border-radius:8px;padding:10px;margin-bottom:8px;background:#131922}.watch b{display:block;color:#e8eef6;font-size:13px}.watch small{color:#8b95a3;word-break:break-all}
.chk{font-size:12px;margin:3px 0}.ok{color:#8fd6a0}.bad{color:#ff8f8f}#log{font:12px ui-monospace,monospace;color:#8b95a3;white-space:pre-wrap;margin-top:12px}
.empty{color:#6b7480;padding:30px;text-align:center}.control{display:inline-flex;align-items:center;gap:5px;color:#8b95a3;font-size:12px}.control input{flex:0}
</style></head><body>
<header><h1>crawler-webui</h1><span id=jd>jd: checking…</span></header>
<main><aside><div class=bar><button class=go id=addbtn style="flex:1">+ Watch this URL</button></div><div id=watches></div><div id=log></div></aside>
<section>
<div class=bar><input id=url placeholder="Paste a Drupal tag, collection, release page, or article URL"><button class=go id=crawl>Crawl</button><button id=test>Test</button><button id=clearResults type=button>Clear results</button></div>
<div id=progressWrap style="display:none;margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:12px;color:#8b95a3"><span id=progressText>Preparing…</span><span id=progressPct>0%</span></div><div style="height:7px;background:#1a212a;border:1px solid #2c3540;border-radius:99px;overflow:hidden"><div id=progressBar style="height:100%;width:0%;background:#2f6fbd"></div></div></div>
<div class=bar id=controls style="display:none;flex-wrap:wrap">
<label class=control><input type=checkbox id=selectAll> Select all</label>
<button id=clearAll type=button>Clear selection</button>
<button id=rgOnly type=button>Rapidgator only</button>
<button id=retryFailed type=button>Retry failed</button>
<button id=send class=go disabled>Send selected to JDownloader</button>
<button id=sendRG class=go disabled>Send all Rapidgator to JDownloader</button>
<button id=rules>JD Rules</button>
<label class=control><input type=checkbox id=autostart> start in JD</label>
<span id=selcount style="color:#8b95a3;font-size:12px"></span></div>
<div id=out class=empty>Paste a URL and press Crawl.</div>
</section></main>
<script>
const $=s=>document.querySelector(s), out=$('#out'); let items=[];
function log(m){$('#log').textContent=(new Date().toLocaleTimeString()+'  '+m+'\n'+$('#log').textContent).slice(0,7000)}
async function jd(){const r=await(await fetch('/api/jd')).json();$('#jd').textContent='jd: '+(r.ok?'folderwatch ready':'unavailable — '+r.detail);$('#jd').style.color=r.ok?'#8fd6a0':'#ff8f8f'}
function esc(s){return String(s??'').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]))}
function hostLabel(l){const h=String(l.host||'').toLowerCase(); if(h==='rapidgator.net')return 'Rapidgator'; return l.label||h||l.bucket||'other'}
function isRG(l){const h=String(l.host||'').toLowerCase(),u=String(l.url||'').toLowerCase();return h==='rapidgator.net'||u.includes('rapidgator.net')||String(l.label||'').toLowerCase().includes('rapidgator')}
function badge(sum){return Object.entries(sum||{}).map(([k,v])=>`<span class="b-${k}">${k} ${v}</span>`).join('')}
function render(){
 if(!items.length){out.className='empty';out.textContent='No downloadable links found.';$('#controls').style.display='none';count();return}
 out.className=''; $('#controls').style.display='flex';
 out.innerHTML=items.map((it,i)=>{
   const links=it.links||[], selectedRG=links.filter(isRG).length, failed=!links.length&&it.error;
   return `<details class="card" ${i<3?'open':''}><summary style="cursor:pointer;list-style:none"><h3 style="display:inline">${esc(it.name||it.url)}</h3> <span class=meta>${esc(it.kind||'release')} · ${links.length} link(s)${selectedRG?' · '+selectedRG+' Rapidgator':''}${failed?' · <span class=bad>failed</span>':''}</span></summary>
   <div class=meta>${it.url?`<a href="${esc(it.url)}" target=_blank style="color:#7fb3ff">open release</a>`:''}</div>
   <div class=badges>${badge(it.link_summary)}</div>
   ${links.length?`<div class=links>${links.map((l,j)=>`<label class=lnk><input type=checkbox data-i="${i}" data-j="${j}"><span class="host ${isRG(l)?'download':''}">${esc(hostLabel(l))}</span><a href="${esc(l.url)}" target=_blank>${esc(l.url)}</a>${l.premium?'<span class=prem>premium host</span>':''}</label>`).join('')}</div>`:`<div class=bad>${esc(it.error||'No direct file-host links found on this release page.')}</div>`}
   </details>`
 }).join('');
 out.querySelectorAll('input[type=checkbox][data-i]').forEach(c=>c.onchange=count); count();
}
function linkBoxes(){return [...out.querySelectorAll('input[type=checkbox][data-i]')]}
function selected(){return linkBoxes().filter(c=>c.checked).map(c=>items[+c.dataset.i].links[+c.dataset.j].url)}
function allRG(){return items.flatMap(it=>(it.links||[]).filter(isRG).map(l=>l.url)).filter((u,i,a)=>a.indexOf(u)===i)}
function count(){const boxes=linkBoxes(), n=selected().length, rg=allRG().length; $('#selcount').textContent=`${n} selected · ${rg} Rapidgator`; $('#send').disabled=!n; $('#sendRG').disabled=!rg; $('#selectAll').checked=!!boxes.length&&n===boxes.length; $('#selectAll').indeterminate=n>0&&n<boxes.length}
function setProgress(done,total,title){const pct=total?Math.round(done*100/total):0;$('#progressWrap').style.display='block';$('#progressText').textContent=`Resolving ${done}/${total}${title?' — '+title:''}`;$('#progressPct').textContent=pct+'%';$('#progressBar').style.width=pct+'%'}
$('#selectAll').onchange=()=>{linkBoxes().forEach(c=>c.checked=$('#selectAll').checked);count()}
$('#clearAll').onclick=()=>{linkBoxes().forEach(c=>c.checked=false);count()}
$('#rgOnly').onclick=()=>{linkBoxes().forEach(c=>{const l=items[+c.dataset.i].links[+c.dataset.j];c.checked=isRG(l)});count()}
$('#clearResults').onclick=()=>{items=[];$('#progressWrap').style.display='none';render();log('results cleared')}
async function sendUrls(urls, label){if(!urls.length)return; const b=label==='Rapidgator'?$('#sendRG'):$('#send');b.disabled=true;try{const r=await fetch('/api/crawljob',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls,name:$('#url').value.trim()||'crawler-webui',auto_start:$('#autostart').checked})});const d=await r.json();if(!r.ok){log('crawljob failed: '+(d.detail||'failed'));return}log(`sent ${d.count} link(s) → ${d.path.split('/').pop()} — ${label}`)}finally{count()}}
$('#send').onclick=()=>sendUrls(selected(),'selected')
$('#sendRG').onclick=()=>sendUrls(allRG(),'Rapidgator')
$('#crawl').onclick=async()=>{const url=$('#url').value.trim();if(!url)return;const b=$('#crawl');b.disabled=true;b.textContent='Crawling…';items=[];render();setProgress(0,1,'fetching listing');
 try{const r=await fetch('/api/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});const d=await r.json();if(!r.ok){out.className='empty';out.textContent='Error: '+(d.detail||'failed');log('crawl failed: '+(d.detail||''));return}items=d.items||[];setProgress(1,1,'complete');log(`crawled ${url} → ${d.count} release(s)${d.resolved?' with concurrent resolution':''}`);render()}catch(e){out.textContent='Error: '+e;log('crawl failed: '+e)}finally{b.disabled=false;b.textContent='Crawl'}}
$('#test').onclick=async()=>{const url=$('#url').value.trim();if(!url)return;const r=await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});const d=await r.json();out.className='';out.innerHTML='<div class=card><h3>Preflight</h3>'+d.checks.map(c=>`<div class=chk><span class="${c.ok?'ok':'bad'}">${c.ok?'PASS':'FAIL'}</span> ${esc(c.name)} — ${esc(c.detail)}</div>`).join('')+'</div>'}
$('#retryFailed').onclick=async()=>{const failed=items.filter(it=>it.error||!(it.links||[]).length);if(!failed.length){log('no failed releases to retry');return}const b=$('#retryFailed');b.disabled=true;b.textContent='Retrying…';setProgress(0,failed.length,'retrying failed releases');try{const r=await fetch('/api/retry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:failed.map(x=>x.url)})});const d=await r.json();if(!r.ok){log('retry failed: '+(d.detail||'failed'));return}for(const rr of d.items||[]){const idx=items.findIndex(x=>x.url===rr.url);if(idx>=0)items[idx]=rr}setProgress(failed.length,failed.length,'retry complete');render();log(`retried ${failed.length} failed release(s)`)}finally{b.disabled=false;b.textContent='Retry failed'}}
$('#addbtn').onclick=async()=>{const url=$('#url').value.trim();if(!url)return;const r=await fetch('/api/watches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});const d=await r.json();if(!r.ok){log('add failed: '+(d.detail||''));return}log(`watching "${d.name}" — ${d.baselined} existing items baselined`);watches()}
async function watches(){const d=await(await fetch('/api/watches')).json();$('#watches').innerHTML=d.watches.length?d.watches.map(w=>`<div class=watch><b>${esc(w.name)}</b><small>${esc(w.ref)}</small><div style="margin-top:6px"><button data-t="${w.id}">Test</button> <button data-d="${w.id}">Remove</button></div></div>`).join(''):'<div style="color:#6b7480;font-size:12px">No watches yet.</div>';$('#watches').querySelectorAll('[data-d]').forEach(b=>b.onclick=async()=>{await fetch('/api/watches/'+b.dataset.d,{method:'DELETE'});watches()});$('#watches').querySelectorAll('[data-t]').forEach(b=>b.onclick=async()=>{const d=await(await fetch('/api/watches/'+b.dataset.t+'/test',{method:'POST'})).json();log(d.checks.map(c=>`${c.ok?'PASS':'FAIL'} ${c.name} — ${c.detail}`).join('\n'))})}
$('#rules').onclick=async()=>{const d=await(await fetch('/api/jd/rules')).json();alert('JDownloader rule types: '+d.types.join(', ')+'\n\n'+d.note)}
const events=new EventSource('/events');
events.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.kind==='resolve_progress'){const p=m.payload||{};setProgress(p.done||0,p.total||0,p.title||'');}if(m.kind==='crawl'){log(`crawl complete — ${m.payload.count||0} release(s)`);}}catch(_){}};
jd();watches();
</script></body></html>"""
