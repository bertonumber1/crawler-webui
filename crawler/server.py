"""crawler-webui — URL watcher, crawler and notifier. FastAPI + SSE on :8096."""
import asyncio, json, os, sys, time
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawler import db, crawljob, fetch, links as linkmod
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


@app.get("/api/providers")
def providers():
    return {"providers": base.names()}


@app.post("/api/crawl")
def crawl(inp: CrawlIn):
    """Crawl a URL once, right now. Nothing is stored or queued."""
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

@app.get("/api/jd")
def jd_status():
    ok, detail = crawljob.available()
    return {"ok": ok, "detail": detail}


@app.post("/api/crawljob")
def make_job(inp: JobIn):
    """Write the links you selected into JD's folderwatch folder."""
    try:
        path = crawljob.write(inp.urls, inp.name, subfolder=inp.subfolder)
    except Exception as e:
        raise HTTPException(400, str(e))
    emit("crawljob", {"path": path, "count": len(inp.urls)})
    return {"ok": True, "path": path, "count": len(inp.urls),
            "note": "queued in JDownloader with autoStart FALSE — press go in JD"}


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
#jd{margin-left:auto;font-size:12px;color:#8b95a3}
main{display:grid;grid-template-columns:320px 1fr;gap:0;height:calc(100vh - 53px)}
aside{border-right:1px solid #232a34;overflow:auto;padding:14px}
section{overflow:auto;padding:14px}
.bar{display:flex;gap:8px;margin-bottom:14px}
input,select,button{background:#1a212a;color:#d5dae1;border:1px solid #2c3540;border-radius:6px;padding:8px 10px;font:inherit}
input{flex:1}
button{cursor:pointer;border-color:#35506f}
button:hover{background:#22303f}
button.go{background:#1d4e89;border-color:#2f6fbd;color:#fff}
.card{border:1px solid #232a34;border-radius:8px;padding:12px;margin-bottom:10px;background:#131922}
.card h3{margin:0 0 4px;font-size:14px;color:#e8eef6}
.meta{font-size:12px;color:#8b95a3;margin-bottom:8px}
.badges span{display:inline-block;font-size:11px;padding:2px 7px;border-radius:99px;margin:0 4px 4px 0;background:#1f2a36;border:1px solid #2c3a49}
.b-source{color:#8fd6a0}.b-build{color:#ffd479}.b-video{color:#ff9ec4}.b-forum{color:#9fc4ff}.b-other{color:#8b95a3}
.links{margin-top:8px;border-top:1px solid #232a34;padding-top:8px}
.lnk{display:flex;gap:8px;align-items:center;font-size:12px;padding:2px 0}
.lnk a{color:#7fb3ff;text-decoration:none;word-break:break-all}
.prem{color:#ff8f8f;font-size:11px;border:1px solid #5a2b2b;border-radius:4px;padding:0 5px}
.watch{border:1px solid #232a34;border-radius:8px;padding:10px;margin-bottom:8px;background:#131922}
.watch b{display:block;color:#e8eef6;font-size:13px}
.watch small{color:#8b95a3;word-break:break-all}
.chk{font-size:12px;margin:3px 0}
.ok{color:#8fd6a0}.bad{color:#ff8f8f}
#log{font:12px ui-monospace,monospace;color:#8b95a3;white-space:pre-wrap;margin-top:12px}
.empty{color:#6b7480;padding:30px;text-align:center}
</style></head><body>
<header>
  <h1>crawler-webui</h1>
  <input id=proxy placeholder="proxy — http:// or socks5:// (blank = direct)"
         style="flex:0 0 320px;font-size:12px;padding:6px 9px">
  <button id=psave style="font-size:12px;padding:6px 10px">Save</button>
  <button id=ptest style="font-size:12px;padding:6px 10px">Test</button>
  <span id=jd>jd: checking…</span>
</header>
<main>
  <aside>
    <div class=bar><button class=go id=addbtn style="flex:1">+ Watch this URL</button></div>
    <div id=watches></div>
    <div id=log></div>
  </aside>
  <section>
    <div class=bar>
      <input id=url placeholder="Paste a URL — XDA thread, forum, article, or any RSS feed">
      <button class=go id=crawl>Crawl</button>
      <button id=test>Test</button>
    </div>
    <div class=bar>
      <button id=send>Send selected links to JDownloader</button><button id=rules>JD Rules</button>
      <span id=selcount style="align-self:center;color:#8b95a3;font-size:12px"></span>
    </div>
    <div id=out class=empty>Paste a URL and press Crawl.</div>
  </section>
</main>
<script>
const $=s=>document.querySelector(s), out=$('#out');
let items=[];
function log(m){ $('#log').textContent = (new Date().toLocaleTimeString()+'  '+m+'\n'+$('#log').textContent).slice(0,2000); }

async function jd(){ const r=await (await fetch('/api/jd')).json();
  $('#jd').textContent = 'jd: '+(r.ok?'folderwatch ready':'unavailable — '+r.detail);
  $('#jd').style.color = r.ok?'#8fd6a0':'#ff8f8f'; }

function badge(sum){ return Object.entries(sum||{}).map(([k,v])=>
  `<span class="b-${k}">${k} ${v}</span>`).join(''); }

function render(){
  if(!items.length){ out.className='empty'; out.textContent='No items found.'; return; }
  out.className='';
  out.innerHTML = items.map((it,i)=>`
    <div class=card>
      <h3>${esc(it.name||it.url)}</h3>
      <div class=meta>${esc(it.kind)} · ${esc(it.author||'unknown')} · ${esc(it.published||'')}
        ${it.url?` · <a href="${esc(it.url)}" target=_blank style="color:#7fb3ff">open</a>`:''}</div>
      <div class=badges>${badge(it.link_summary)}</div>
      ${it.links&&it.links.length?`<div class=links>${it.links.map((l,j)=>`
        <label class=lnk><input type=checkbox data-i="${i}" data-j="${j}">
        <span class="b-${l.bucket}">${esc(l.bucket)}</span>
        <a href="${esc(l.url)}" target=_blank>${esc(l.url)}</a>
        ${l.premium?'<span class=prem>premium host</span>':''}</label>`).join('')}</div>`:''}
    </div>`).join('');
  out.querySelectorAll('input[type=checkbox]').forEach(c=>c.onchange=count);
  count();
}
function esc(s){ return String(s??'').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c])); }
function selected(){ return [...out.querySelectorAll('input:checked')].map(c=>
  items[c.dataset.i].links[c.dataset.j].url); }
function count(){ const n=selected().length; $('#selcount').textContent = n?`${n} link(s) selected`:''; }

$('#crawl').onclick=async()=>{
  const url=$('#url').value.trim(); if(!url) return;
  out.className='empty'; out.textContent='Crawling…';
  const r=await fetch('/api/crawl',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url})});
  const d=await r.json();
  if(!r.ok){ out.textContent='Error: '+(d.detail||'failed'); return; }
  items=d.items; log(`crawled ${url} → ${d.count} items`); render();
};

$('#test').onclick=async()=>{
  const url=$('#url').value.trim(); if(!url) return;
  const r=await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url})});
  const d=await r.json();
  out.className=''; out.innerHTML='<div class=card><h3>Preflight</h3>'+
    d.checks.map(c=>`<div class=chk><span class="${c.ok?'ok':'bad'}">${c.ok?'PASS':'FAIL'}</span> ${esc(c.name)} — ${esc(c.detail)}</div>`).join('')+'</div>';
};

$('#addbtn').onclick=async()=>{
  const url=$('#url').value.trim(); if(!url) return;
  const r=await fetch('/api/watches',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url})});
  const d=await r.json();
  if(!r.ok){ log('add failed: '+(d.detail||'')); return; }
  log(`watching "${d.name}" — ${d.baselined} existing items baselined`); watches();
};

$('#send').onclick=async()=>{
  const urls=selected(); if(!urls.length){ log('nothing selected'); return; }
  const r=await fetch('/api/crawljob',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({urls,name:$('#url').value.trim()||'crawler-webui'})});
  const d=await r.json();
  log(r.ok?`wrote ${d.count} link(s) → ${d.path.split('/').pop()} (autoStart off)`
          :`crawljob failed: ${d.detail}`);
};

async function watches(){
  const d=await (await fetch('/api/watches')).json();
  $('#watches').innerHTML = d.watches.length? d.watches.map(w=>`
    <div class=watch><b>${esc(w.name)}</b><small>${esc(w.ref)}</small>
      <div style="margin-top:6px"><button data-t="${w.id}">Test</button>
      <button data-d="${w.id}">Remove</button></div></div>`).join('')
    : '<div style="color:#6b7480;font-size:12px">No watches yet.</div>';
  $('#watches').querySelectorAll('[data-d]').forEach(b=>b.onclick=async()=>{
    await fetch('/api/watches/'+b.dataset.d,{method:'DELETE'}); watches(); });
  $('#watches').querySelectorAll('[data-t]').forEach(b=>b.onclick=async()=>{
    const d=await (await fetch('/api/watches/'+b.dataset.t+'/test',{method:'POST'})).json();
    log(d.checks.map(c=>`${c.ok?'PASS':'FAIL'} ${c.name} — ${c.detail}`).join('\n')); });
}

$('#rules').onclick=async()=>{ const d=await (await fetch('/api/jd/rules')).json(); alert('JDownloader LinkCrawler rule types:\\n\\n'+d.types.join('\\n')+'\\n\\n'+d.note); };\nnew EventSource('/events').onmessage=e=>{ const m=JSON.parse(e.data);
  if(m.kind!=='hello') log('· '+m.kind+' '+JSON.stringify(m.payload)); };
async function loadSettings(){
  const d=await (await fetch('/api/settings')).json();
  $('#proxy').value = d.proxy||'';
  $('#proxy').style.borderColor = d.proxy? '#5a7f3a' : '#2c3540';
}
$('#psave').onclick=async()=>{
  const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({proxy:$('#proxy').value.trim()})});
  const d=await r.json();
  log(r.ok? `proxy set to ${d.proxy||'direct'} — ${d.note}` : `proxy rejected: ${d.detail}`);
  loadSettings();
};
$('#ptest').onclick=async()=>{
  log('testing egress…');
  const d=await (await fetch('/api/settings/proxy-test',{method:'POST'})).json();
  log(`direct: ${d.direct}\nproxied: ${d.proxied??'n/a'}\n${d.verdict}`);
};
jd(); watches(); loadSettings();
</script></body></html>"""
