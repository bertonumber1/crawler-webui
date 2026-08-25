"""crawler-webui — URL watcher, crawler and notifier. FastAPI + SSE on :8096."""
import asyncio, json, os, sys
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawler import db, crawljob, fetch, links as linkmod, resolve as resolvemod
from crawler.providers import base, web, dryrun  # noqa: F401

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


class ResolveIn(BaseModel):
    url: str
    provider: str = "web"
    limit: int = 40
    send: bool = False
    auto_start: bool = False
    per_release: bool = True


@app.get("/api/providers")
def providers():
    return {"providers": base.names()}


@app.post("/api/crawl")
def crawl(inp: CrawlIn):
    try:
        p = base.get(inp.provider)
        items = p.poll(inp.url)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    out = [i.dict() for i in items]
    for d in out:
        d["link_summary"] = linkmod.summarise(d.get("links", []))
    emit("crawl", {"url": inp.url, "count": len(out)})
    return {"url": inp.url, "count": len(out), "items": out}


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


@app.post("/api/resolve")
def resolve_links(inp: ResolveIn):
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
    return crawljob.status()


@app.post("/api/crawljob")
def make_job(inp: JobIn):
    try:
        path = crawljob.write(inp.urls, inp.name, subfolder=inp.subfolder,
                              auto_start=inp.auto_start)
    except Exception as e:
        raise HTTPException(400, str(e))
    emit("crawljob", {"path": path, "count": len(inp.urls)})
    return {"ok": True, "path": path, "count": len(inp.urls),
            "note": ("queued in JDownloader and started" if inp.auto_start else
                     "queued in JDownloader with autoStart FALSE — press go in JD")}


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
header{padding:14px 18px;background:#151a21;border-bottom:1px solid #232a34;display:flex;gap:12px;align-items:center}
h1{margin:0;font-size:15px;letter-spacing:.14em;text-transform:uppercase;color:#7fb3ff}
#jd{margin-left:auto;font-size:12px;color:#8b95a3;max-width:460px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
main{display:grid;grid-template-columns:320px 1fr;gap:0;height:calc(100vh - 53px)}
aside{border-right:1px solid #232a34;overflow:auto;padding:14px}
section{overflow:auto;padding:14px}
.bar{display:flex;gap:8px;margin-bottom:14px;align-items:center}
input,select,button{background:#1a212a;color:#d5dae1;border:1px solid #2c3540;border-radius:6px;padding:8px 10px;font:inherit}
input{flex:1}
button{cursor:pointer;border-color:#35506f}
button:hover{background:#22303f}
button.go{background:#1d4e89;border-color:#2f6fbd;color:#fff}
button:disabled{opacity:.5;cursor:wait}
.card{border:1px solid #232a34;border-radius:8px;padding:12px;margin-bottom:10px;background:#131922}
.card h3{margin:0 0 4px;font-size:14px;color:#e8eef6}
.meta{font-size:12px;color:#8b95a3;margin-bottom:8px}
.badges span{display:inline-block;font-size:11px;padding:2px 7px;border-radius:99px;margin:0 4px 4px 0;background:#1f2a36;border:1px solid #2c3a49}
.b-source{color:#8fd6a0}.b-build{color:#ffd479}.b-video{color:#ff9ec4}.b-forum{color:#9fc4ff}.b-other{color:#8b95a3}
.links{margin-top:8px;border-top:1px solid #232a34;padding-top:8px}
.lnk{display:grid;grid-template-columns:22px 70px minmax(0,1fr) auto;gap:8px;align-items:center;font-size:12px;padding:5px 0}
.lnk a{color:#7fb3ff;text-decoration:none;word-break:break-all}
.prem{color:#ff8f8f;font-size:11px;border:1px solid #5a2b2b;border-radius:4px;padding:0 5px;white-space:nowrap}
.watch{border:1px solid #232a34;border-radius:8px;padding:10px;margin-bottom:8px;background:#131922}
.watch b{display:block;color:#e8eef6;font-size:13px}.watch small{color:#8b95a3;word-break:break-all}
.chk{font-size:12px;margin:3px 0}.ok{color:#8fd6a0}.bad{color:#ff8f8f}
#log{font:12px ui-monospace,monospace;color:#8b95a3;white-space:pre-wrap;margin-top:12px}
.empty{color:#6b7480;padding:30px;text-align:center}
.selectbar{display:flex;align-items:center;gap:8px;padding:8px 10px;margin-bottom:8px;background:#131922;border:1px solid #232a34;border-radius:8px;font-size:12px;color:#8b95a3}
.resolvebox{margin:0 0 10px;padding:9px 11px;border:1px solid #2c3540;border-radius:7px;background:#111820;color:#9aa5b3;font-size:12px}
</style></head><body>
<header>
  <h1>crawler-webui</h1>
  <input id=proxy placeholder="proxy — http:// or socks5:// (blank = direct)" style="flex:0 0 320px;font-size:12px;padding:6px 9px">
  <button id=psave style="font-size:12px;padding:6px 10px">Save</button>
  <button id=ptest style="font-size:12px;padding:6px 10px">Test</button>
  <span id=jd>jd: checking…</span>
</header>
<main><aside>
  <div class=bar><button class=go id=addbtn style="flex:1">+ Watch this URL</button></div>
  <div id=watches></div><div id=log></div>
</aside><section>
  <div class=bar><input id=url placeholder="Paste a URL — Drupal tag, release page, article, or RSS feed"><button class=go id=crawl>Crawl</button><button id=test>Test</button></div>
  <div class=bar>
    <button class=go id=resolve>Resolve to Rapidgator</button>
    <button id=send>Send selected links to JDownloader</button><button id=rules>JD Rules</button>
    <label style="color:#8b95a3;font-size:12px"><input type=checkbox id=autostart> start in JD</label>
    <span id=selcount style="color:#8b95a3;font-size:12px"></span>
  </div>
  <div id=out class=empty>Paste a URL and press Crawl.</div>
</section></main>
<script>
const $=s=>document.querySelector(s), out=$('#out');
let items=[];
function log(m){ $('#log').textContent=(new Date().toLocaleTimeString()+'  '+m+'\n'+$('#log').textContent).slice(0,3000); }
async function jd(){ const r=await (await fetch('/api/jd')).json();
  $('#jd').textContent='jd: '+(r.ok?'folderwatch ready — '+r.folderwatch+' → '+r.download_root_for_jd:r.detail);
  $('#jd').style.color=r.ok?'#8fd6a0':'#ff8f8f'; }
function badge(sum){return Object.entries(sum||{}).map(([k,v])=>`<span class="b-${k}">${k} ${v}</span>`).join('');}
function render(){
  if(!items.length){out.className='empty';out.textContent='No items found.';return;}
  out.className='';
  const total=items.reduce((n,it)=>n+(it.links?.length||0),0);
  out.innerHTML=`<div class=selectbar><input type=checkbox id=selectall><label for=selectall><b>Select all</b></label><span>${total} link(s)</span><span id=resolvehint>Check individual links below or use Select all.</span></div>`+
    `<div class=resolvebox>Only links shown in these rows are sent to JDownloader. For a Drupal listing, use <b>Resolve to Rapidgator</b> first; it follows the release-page hop and replaces the listing links with the actual file-host URLs.</div>`+
    items.map((it,i)=>`<div class=card><h3>${esc(it.name||it.url)}</h3>
      <div class=meta>${esc(it.kind)} · ${esc(it.author||'unknown')} · ${esc(it.published||'')} ${it.url?` · <a href="${esc(it.url)}" target=_blank>source</a>`:''}</div>
      <div class=badges>${badge(it.link_summary)}</div>
      ${it.links&&it.links.length?`<div class=links>${it.links.map((l,j)=>`<label class=lnk><input type=checkbox data-i="${i}" data-j="${j}"><span class="b-${l.bucket}">${esc(l.bucket)}</span><a href="${esc(l.url)}" target=_blank title="${esc(l.url)}">${esc(l.url)}</a>${l.premium?'<span class=prem>premium host</span>':''}</label>`).join('')}</div>`:''}
    </div>`).join('');
  out.querySelectorAll('.lnk input').forEach(c=>c.onchange=count);
  $('#selectall').onchange=()=>{out.querySelectorAll('.lnk input').forEach(c=>c.checked=$('#selectall').checked);count();};
  count();
}
function esc(s){return String(s??'').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));}
function boxes(){return [...out.querySelectorAll('.lnk input[type=checkbox]')];}
function selected(){return boxes().filter(c=>c.checked).map(c=>items[c.dataset.i].links[c.dataset.j].url);}
function count(){const b=boxes(),n=selected().length;$('#selcount').textContent=n?`${n} selected`:'';const all=$('#selectall');if(all){all.checked=b.length>0&&n===b.length;all.indeterminate=n>0&&n<b.length;}}
$('#crawl').onclick=async()=>{const url=$('#url').value.trim();if(!url)return;out.className='empty';out.textContent='Crawling…';const r=await fetch('/api/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});const d=await r.json();if(!r.ok){out.textContent='Error: '+(d.detail||'failed');return;}items=d.items;log(`crawled ${url} → ${d.count} items`);render();};
$('#test').onclick=async()=>{const url=$('#url').value.trim();if(!url)return;const r=await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});const d=await r.json();out.className='';out.innerHTML='<div class=card><h3>Preflight</h3>'+d.checks.map(c=>`<div class=chk><span class="${c.ok?'ok':'bad'}">${c.ok?'PASS':'FAIL'}</span> ${esc(c.name)} — ${esc(c.detail)}</div>`).join('')+'</div>';};
$('#addbtn').onclick=async()=>{const url=$('#url').value.trim();if(!url)return;const r=await fetch('/api/watches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});const d=await r.json();if(!r.ok){log('add failed: '+(d.detail||''));return;}log(`watching "${d.name}" — ${d.baselined} existing items baselined`);watches();};
$('#send').onclick=async()=>{const urls=selected();if(!urls.length){log('nothing selected');return;}const r=await fetch('/api/crawljob',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls,name:$('#url').value.trim()||'crawler-webui',auto_start:$('#autostart').checked})});const d=await r.json();log(r.ok?`wrote ${d.count} link(s) → ${d.path.split('/').pop()}`:`crawljob failed: ${d.detail}`);if(r.ok)jd();};
async function watches(){const d=await (await fetch('/api/watches')).json();$('#watches').innerHTML=d.watches.length?d.watches.map(w=>`<div class=watch><b>${esc(w.name)}</b><small>${esc(w.ref)}</small><div style="margin-top:6px"><button data-t="${w.id}">Test</button><button data-d="${w.id}">Remove</button></div></div>`).join(''):'<div style="color:#6b7480;font-size:12px">No watches yet.</div>';
  $('#watches').querySelectorAll('[data-d]').forEach(b=>b.onclick=async()=>{await fetch('/api/watches/'+b.dataset.d,{method:'DELETE'});watches();});
  $('#watches').querySelectorAll('[data-t]').forEach(b=>b.onclick=async()=>{const d=await (await fetch('/api/watches/'+b.dataset.t+'/test',{method:'POST'})).json();log(d.checks.map(c=>`${c.ok?'PASS':'FAIL'} ${c.name} — ${c.detail}`).join('\n'));});}
$('#resolve').onclick=async()=>{const u=$('#url').value.trim();if(!u)return;const b=$('#resolve'),was=b.textContent;b.disabled=true;b.textContent='Resolving…';out.className='empty';out.textContent='Following release pages and extracting file-host links…';log('resolving '+u+' — following release pages');try{const r=await fetch('/api/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:u,send:false,auto_start:$('#autostart').checked,limit:40})});const d=await r.json();if(!r.ok){out.textContent='Resolve failed: '+(d.detail||r.status);log('resolve failed: '+(d.detail||r.status));return;}
    items=(d.releases||[]).filter(x=>x.links&&x.links.length).map(r=>({name:r.title||r.url,url:r.url,kind:'resolved release',author:'',published:'',links:r.links,link_summary:{build:r.links.length}}));
    if(!items.length&&d.links?.length)items=[{name:u,url:u,kind:'resolved',links:d.links,link_summary:linkmodSummary(d.links)}];
    log(d.hopped?`followed ${d.followed} release page(s) → ${d.count} actual link(s)`:`found ${d.count} actual link(s) without a second hop`);render();
  }catch(e){out.textContent='Resolve failed: '+e;log('resolve failed: '+e);}finally{b.disabled=false;b.textContent=was;}};
function linkmodSummary(ls){const s={};for(const l of ls)s[l.bucket]=(s[l.bucket]||0)+1;return s;}
$('#rules').onclick=async()=>{const d=await (await fetch('/api/jd/rules')).json();alert('JDownloader LinkCrawler rule types:\n\n'+d.types.join('\n')+'\n\n'+d.note);};
new EventSource('/events').onmessage=e=>{const m=JSON.parse(e.data);if(m.kind!=='hello')log('· '+m.kind+' '+JSON.stringify(m.payload));};
async function loadSettings(){const d=await (await fetch('/api/settings')).json();$('#proxy').value=d.proxy||'';$('#proxy').style.borderColor=d.proxy?'#5a7f3a':'#2c3540';}
$('#psave').onclick=async()=>{const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({proxy:$('#proxy').value.trim()})});const d=await r.json();log(r.ok?`proxy set to ${d.proxy||'direct'} — ${d.note}`:`proxy rejected: ${d.detail}`);loadSettings();};
$('#ptest').onclick=async()=>{log('testing egress…');const d=await (await fetch('/api/settings/proxy-test',{method:'POST'})).json();log(`direct: ${d.direct}\nproxied: ${d.proxied??'n/a'}\n${d.verdict}`);};
jd();watches();loadSettings();
</script></body></html>"""
