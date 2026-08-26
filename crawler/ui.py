"""The single-page front end.

Kept out of server.py so the API is readable on its own, and kept as one
inline document so the container ships one artefact with no static route and
no cache-busting to get wrong.

The layout answers the three questions the old page could not:
what did this crawl find, what have I already sent, and what does JD actually
have. Those are the three columns.
"""

PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>crawler-webui</title><style>
:root{
 --bg:#0b0e13; --panel:#111721; --panel2:#0d131b; --line:#1e2733; --line2:#2a3746;
 --ink:#dbe2ea; --dim:#7d8896; --dim2:#5b6673;
 --accent:#4c8dd8; --accent2:#2a5f9e;
 --ok:#57c98a; --warn:#e0b054; --bad:#e2716f; --new:#7fb3ff; --gone:#8b7fd8;
 --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);
 font:13.5px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
button,input,select{font:inherit;color:inherit}

/* ---------- masthead ---------- */
header{display:flex;align-items:center;gap:14px;padding:9px 16px;
 background:linear-gradient(180deg,#131a25,#0f151e);border-bottom:1px solid var(--line2)}
header h1{margin:0;font-size:12px;letter-spacing:.22em;text-transform:uppercase;
 color:var(--accent);font-weight:700}
.pill{font-size:11px;padding:2px 9px;border-radius:99px;border:1px solid var(--line2);
 background:#0e141d;color:var(--dim);white-space:nowrap}
.pill b{color:var(--ink);font-weight:600}
.pill.ok{border-color:#245c3d;color:var(--ok)} .pill.bad{border-color:#5e2b2b;color:var(--bad)}
.spacer{margin-left:auto}

/* ---------- shell ---------- */
main{display:grid;grid-template-columns:250px minmax(0,1fr) 300px;
 height:calc(100vh - 41px)}
aside,.side{background:var(--panel2);overflow:auto}
aside{border-right:1px solid var(--line)}
.side{border-left:1px solid var(--line)}
section{overflow:auto;background:var(--bg);min-width:0}
.pad{padding:12px}
h2{margin:0 0 9px;font-size:10.5px;letter-spacing:.18em;text-transform:uppercase;
 color:var(--dim2);font-weight:700}

/* ---------- controls ---------- */
input[type=text],input:not([type]){width:100%;background:#0a0f16;border:1px solid var(--line2);
 border-radius:5px;padding:8px 10px}
input:focus{outline:none;border-color:var(--accent2)}
button{background:#18202b;border:1px solid var(--line2);border-radius:5px;
 padding:7px 11px;cursor:pointer}
button:hover:not(:disabled){background:#1f2937;border-color:#3a4a5e}
button:disabled{opacity:.4;cursor:not-allowed}
button.go{background:var(--accent2);border-color:var(--accent);color:#fff}
button.go:hover:not(:disabled){background:#356fb5}
button.sm{padding:4px 8px;font-size:11.5px}
.row{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
label.ctl{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--dim)}
label.ctl input{accent-color:var(--accent)}

/* ---------- tabs ---------- */
.tabs{display:flex;gap:1px;border-bottom:1px solid var(--line);background:var(--panel2)}
.tab{padding:8px 13px;font-size:11.5px;color:var(--dim);cursor:pointer;
 border-bottom:2px solid transparent;letter-spacing:.05em}
.tab:hover{color:var(--ink)}
.tab.on{color:var(--accent);border-bottom-color:var(--accent);background:var(--bg)}
.tabpane{display:none}.tabpane.on{display:block}

/* ---------- results table ---------- */
table{width:100%;border-collapse:collapse}
thead th{position:sticky;top:0;z-index:2;background:#0f151e;text-align:left;
 font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim2);
 padding:7px 9px;border-bottom:1px solid var(--line2);font-weight:700;white-space:nowrap}
thead th.sortable{cursor:pointer}
thead th.sortable:hover{color:var(--ink)}
tbody td{padding:6px 9px;border-bottom:1px solid #161d27;vertical-align:top}
tbody tr:hover{background:#101722}
tbody tr.rel td{background:#0e141d;border-top:1px solid var(--line)}
tbody tr.rel td h3{margin:0;font-size:12.5px;color:#e9eff6;font-weight:600}
tbody tr.rel .sub{font-size:11px;color:var(--dim)}
td.pick{width:26px}
td.name{max-width:0;word-break:break-all;font:12px/1.5 var(--mono);color:#9fc0e6}
td.name a{color:inherit;text-decoration:none}
td.name a:hover{text-decoration:underline}
.tag{display:inline-block;font-size:10px;padding:1px 6px;border-radius:99px;
 border:1px solid var(--line2);background:#0d131b;color:var(--dim);white-space:nowrap}
.tag.host{color:var(--warn);border-color:#4a3c1e}
.tag.new{color:var(--new);border-color:#2b4467}
.tag.sent{color:var(--dim2)}
.tag.in_jd{color:var(--ok);border-color:#245c3d}
.tag.gone{color:var(--gone);border-color:#3d3562}
.tag.prem{color:var(--bad);border-color:#5e2b2b}
.tag.err{color:var(--bad);border-color:#5e2b2b}
.fid{font:11px var(--mono);color:var(--dim2)}

/* ---------- misc ---------- */
.empty{padding:44px 20px;text-align:center;color:var(--dim2)}
.empty b{display:block;color:var(--dim);font-size:14px;margin-bottom:5px}
.bar{height:5px;background:#0a0f16;border:1px solid var(--line2);border-radius:99px;overflow:hidden}
.bar>div{height:100%;width:0;background:var(--accent);transition:width .25s}
.mini{font-size:11px;color:var(--dim)}
.kv{display:flex;justify-content:space-between;gap:8px;font-size:11.5px;padding:3px 0;
 border-bottom:1px solid #151c26}
.kv span:last-child{color:var(--ink);font-family:var(--mono)}
.card{border:1px solid var(--line);border-radius:6px;background:var(--panel);
 padding:9px;margin-bottom:8px}
.card b{display:block;font-size:12px;color:#e9eff6}
.card small{color:var(--dim);word-break:break-all;font-size:11px}
#trace{font:11px/1.5 var(--mono);white-space:pre-wrap;word-break:break-word;
 color:var(--dim);max-height:100%;}
#trace .t-fetch{color:#6f95c4}#trace .t-resolve{color:var(--warn)}
#trace .t-handoff{color:var(--ok)}#trace .t-crawl{color:var(--new)}
#trace .t-probe{color:var(--gone)}#trace .err{color:var(--bad)}
.scroll{overflow:auto}
</style></head><body>

<header>
  <h1>Crawler</h1>
  <span class=pill id=pHosts>hosts: …</span>
  <span class=pill id=pJd>jd: …</span>
  <span class=pill id=pHist>sent: …</span>
  <span class=spacer></span>
  <span class=pill id=pTrace>trace: …</span>
</header>

<main>
  <!-- ============ left ============ -->
  <aside>
    <div class=pad>
      <h2>Source</h2>
      <input id=url placeholder="Forum thread, tag page or release URL">
      <div class=row style="margin-top:7px">
        <button class=go id=bCrawl>Crawl</button>
        <button id=bProbe>Probe</button>
        <button id=bWatch class=sm>Watch</button>
      </div>
      <div class=row style="margin-top:7px">
        <label class=ctl>pages
          <input id=pages type=number min=1 max=25 value=1
                 style="width:52px;padding:3px 5px;background:#0a0f16;border:1px solid var(--line2);border-radius:4px"></label>
        <label class=ctl><input type=checkbox id=includeSeen> show already sent</label>
      </div>
      <div id=prog style="display:none;margin-top:10px">
        <div class=mini style="display:flex;justify-content:space-between">
          <span id=progText>working…</span><span id=progPct>0%</span></div>
        <div class=bar style="margin-top:4px"><div id=progBar></div></div>
      </div>
    </div>

    <div class=pad style="border-top:1px solid var(--line)">
      <h2>Hosts harvested</h2>
      <div id=hostList class=mini>…</div>
    </div>

    <div class=pad style="border-top:1px solid var(--line)">
      <h2>Watches</h2><div id=watches class=mini>none</div>
    </div>

    <div class=pad style="border-top:1px solid var(--line)">
      <h2>Recent crawls</h2><div id=crawls class=mini>none</div>
    </div>
  </aside>

  <!-- ============ centre ============ -->
  <section>
    <div class=tabs>
      <div class="tab on" data-pane=results>Results</div>
      <div class=tab data-pane=probe>Probe</div>
      <div class=tab data-pane=history>History</div>
    </div>

    <div class="tabpane on" id=pane-results>
      <div class="row pad" id=actions style="display:none;border-bottom:1px solid var(--line)">
        <label class=ctl><input type=checkbox id=selAll> all</label>
        <button class=sm id=bNone>none</button>
        <button class=sm id=bFresh>new only</button>
        <span class=spacer></span>
        <span class=mini id=selCount></span>
        <label class=ctl><input type=checkbox id=autostart> start in JD</label>
        <button class="go sm" id=bSend disabled>Send to JDownloader</button>
      </div>
      <div id=results><div class=empty><b>Nothing crawled yet</b>
        Paste a URL and press Crawl. Probe first if the site is new.</div></div>
    </div>

    <div class=tabpane id=pane-probe>
      <div id=probeOut><div class=empty><b>No probe run</b>
        Probe identifies the forum software and shows what would be extracted,
        without crawling or queueing anything.</div></div>
    </div>

    <div class=tabpane id=pane-history>
      <div class="row pad" style="border-bottom:1px solid var(--line)">
        <button class=sm id=bReconcile>Reconcile with JD</button>
        <button class=sm id=bForget disabled>Forget selected</button>
        <span class=spacer></span><span class=mini id=histCount></span>
      </div>
      <div id=histOut><div class=empty><b>Nothing sent yet</b>
        Files handed to JDownloader are remembered here so they are never queued twice.</div></div>
    </div>
  </section>

  <!-- ============ right ============ -->
  <div class=side>
    <div class=pad style="border-bottom:1px solid var(--line)">
      <div class=row style="margin-bottom:7px">
        <h2 style="margin:0">JDownloader</h2><span class=spacer></span>
        <button class=sm id=bCleanup title="Remove links JD reports as dead, and forget them so the release can be found again">clear dead</button>
      </div>
      <div id=jdBox class=mini>…</div>
    </div>
    <div class=pad>
      <div class=row style="margin-bottom:7px">
        <h2 style="margin:0">Trace</h2><span class=spacer></span>
        <button class=sm id=bTraceClear>clear</button>
      </div>
      <div id=trace class=scroll>waiting…</div>
    </div>
  </div>
</main>

<script>
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??'').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
let items=[], hist=[], lastTrace=0;

/* ---------------- tabs ---------------- */
$$('.tab').forEach(t=>t.onclick=()=>{
  $$('.tab').forEach(x=>x.classList.toggle('on',x===t));
  $$('.tabpane').forEach(p=>p.classList.toggle('on',p.id==='pane-'+t.dataset.pane));
});

/* ---------------- results ---------------- */
// One row per link, grouped under its release. A release header is a row too,
// so the table stays one grid and the columns line up across groups.
function render(){
  const box=$('#results');
  if(!items.length){
    box.innerHTML='<div class=empty><b>No links found</b>'+
      'Nothing on this page resolved to a harvested host. Try Probe to see what is there.</div>';
    $('#actions').style.display='none'; count(); return;
  }
  $('#actions').style.display='flex';
  let html='<table><thead><tr><th class=pick></th><th>File</th><th>Host</th>'+
           '<th>State</th><th>ID</th></tr></thead><tbody>';
  items.forEach((it,i)=>{
    const links=it.links||[];
    html+=`<tr class=rel><td class=pick><input type=checkbox data-rel="${i}"></td>`+
          `<td colspan=4><h3>${esc(it.name||it.url)}</h3>`+
          `<div class=sub>${links.length} link(s)`+
          (it.url?` · <a href="${esc(it.url)}" target=_blank rel=noreferrer style="color:var(--dim)">source</a>`:'')+
          (it.summary?` · <span class="tag err">${esc(it.summary).slice(0,90)}</span>`:'')+
          `</div></td></tr>`;
    links.forEach((l,j)=>{
      const st=l.seen_before?(l.prior_state||'sent'):'new';
      html+=`<tr><td class=pick><input type=checkbox data-i="${i}" data-j="${j}"`+
            `${l.seen_before?'':' checked'}></td>`+
            `<td class=name><a href="${esc(l.url)}" target=_blank rel=noreferrer>${esc(fileName(l.url))}</a></td>`+
            `<td><span class="tag host">${esc(l.label||l.host||'')}</span>`+
            (l.premium?' <span class="tag prem">premium</span>':'')+`</td>`+
            `<td><span class="tag ${esc(st)}">${esc(st.replace('_',' '))}</span></td>`+
            `<td class=fid>${esc((l.file_key||'').split(':').pop().slice(0,14))}</td></tr>`;
    });
  });
  box.innerHTML=html+'</tbody></table>';
  box.querySelectorAll('input[data-i]').forEach(c=>c.onchange=count);
  box.querySelectorAll('input[data-rel]').forEach(c=>c.onchange=()=>{
    box.querySelectorAll(`input[data-i="${c.dataset.rel}"]`).forEach(x=>x.checked=c.checked);
    count();
  });
  count();
}
function fileName(u){
  try{const p=decodeURIComponent(new URL(u).pathname).split('/').filter(Boolean);
      return p[p.length-1]||u}catch(e){return u}
}
function picked(){
  return $$('#results input[data-i]:checked').map(c=>({
    link:items[c.dataset.i].links[c.dataset.j], rel:items[c.dataset.i]}));
}
function count(){
  const n=picked().length;
  $('#selCount').textContent=n?`${n} selected`:'';
  $('#bSend').disabled=!n;
}
$('#selAll').onchange=e=>{
  $$('#results input[type=checkbox]').forEach(c=>c.checked=e.target.checked); count();
};
$('#bNone').onclick=()=>{$$('#results input[type=checkbox]').forEach(c=>c.checked=false);
  $('#selAll').checked=false; count()};
$('#bFresh').onclick=()=>{
  items.forEach((it,i)=>(it.links||[]).forEach((l,j)=>{
    const c=document.querySelector(`#results input[data-i="${i}"][data-j="${j}"]`);
    if(c) c.checked=!l.seen_before;}));
  count();
};

/* ---------------- crawl ---------------- */
async function post(u,b){
  const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(b||{})});
  const d=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(d.detail||('HTTP '+r.status));
  return d;
}
$('#bCrawl').onclick=async()=>{
  const url=$('#url').value.trim(); if(!url) return;
  const b=$('#bCrawl'); b.disabled=true; b.textContent='Crawling…';
  $('#results').innerHTML='<div class=empty><b>Crawling</b>Following the site at its own pace.</div>';
  showProg(0,1,'starting');
  try{
    const d=await post('/api/crawl',{url,pages:+$('#pages').value||1,
                                     include_seen:$('#includeSeen').checked});
    items=d.items||[]; render();
    note(`${d.count} release(s) · ${d.fresh} new of ${d.links} link(s)`+
         (d.already_sent?` · ${d.already_sent} already sent`:'')+` · ${d.pages} page(s)`);
    loadSide();
  }catch(e){
    $('#results').innerHTML=`<div class=empty><b>Crawl failed</b>${esc(e.message)}</div>`;
  }finally{ b.disabled=false; b.textContent='Crawl'; hideProg(); }
};

/* ---------------- send ---------------- */
$('#bSend').onclick=async()=>{
  const sel=picked(); if(!sel.length) return;
  const b=$('#bSend'); b.disabled=true; b.textContent='Sending…';
  // One package per release, so JD shows the album not a heap of rars.
  const groups=new Map();
  sel.forEach(({link,rel})=>{
    const k=rel.name||rel.url||'crawler-webui';
    if(!groups.has(k)) groups.set(k,[]);
    groups.get(k).push(link.url);
  });
  let ok=0,fail=0;
  for(const [name,urls] of groups){
    try{ await post('/api/crawljob',{urls,name,auto_start:$('#autostart').checked}); ok++; }
    catch(e){ fail++; }
  }
  note(`${ok} package(s) queued${fail?`, ${fail} failed`:''}`);
  b.textContent='Send to JDownloader';
  // Re-crawl state is stale now: those links are sent.
  sel.forEach(({link})=>{link.seen_before=true;link.prior_state='sent'});
  render(); loadSide(); loadHistory();
};

/* ---------------- probe ---------------- */
$('#bProbe').onclick=async()=>{
  const url=$('#url').value.trim(); if(!url) return;
  $$('.tab').forEach(x=>x.classList.toggle('on',x.dataset.pane==='probe'));
  $$('.tabpane').forEach(p=>p.classList.toggle('on',p.id==='pane-probe'));
  $('#probeOut').innerHTML='<div class=empty><b>Probing</b>One request, nothing queued.</div>';
  try{
    const d=await post('/api/probe',{url});
    $('#probeOut').innerHTML=`<div class=pad>
      <div class=card><b>${esc(d.best||'software not recognised')}</b>
        <small>HTTP ${d.status} · ${d.bytes.toLocaleString()} bytes · parsed by ${esc(d.parser||'?')}</small></div>
      ${d.detections.map(x=>`<div class=kv><span>${esc(x.software)} <span class=mini>${esc(x.evidence.join(', '))}</span></span><span>${x.confidence}</span></div>`).join('')||'<div class=mini>no signatures matched</div>'}
      <div style="height:12px"></div>
      <div class=kv><span>items parsed</span><span>${d.items}</span></div>
      <div class=kv><span>harvestable links</span><span>${d.downloadable}</span></div>
      <div class=kv><span>next page</span><span>${d.next_page?esc(d.next_page.slice(-48)):'none'}</span></div>
      <div style="height:12px"></div>
      <h2>Hosts on the page</h2>
      ${d.hosts.map(([h,n])=>`<div class=kv><span>${esc(h||'(none)')}</span><span>${n}</span></div>`).join('')||'<div class=mini>none</div>'}
      <div style="height:12px"></div>
      <h2>Sample titles</h2>
      ${d.titles.map(t=>`<div class=mini style="padding:2px 0">${esc(t)}</div>`).join('')||'<div class=mini>none</div>'}
    </div>`;
  }catch(e){ $('#probeOut').innerHTML=`<div class=empty><b>Probe failed</b>${esc(e.message)}</div>`; }
};

/* ---------------- history ---------------- */
async function loadHistory(){
  try{
    const d=await (await fetch('/api/history?limit=300')).json();
    hist=d.items||[];
    const c=d.counts||{};
    $('#histCount').textContent=Object.entries(c).map(([k,v])=>`${k.replace('_',' ')} ${v}`).join(' · ');
    $('#pHist').innerHTML='sent: <b>'+(hist.length)+'</b>';
    if(!hist.length){ $('#histOut').innerHTML='<div class=empty><b>Nothing sent yet</b>Files handed to JDownloader are remembered here.</div>'; return; }
    $('#histOut').innerHTML='<table><thead><tr><th class=pick></th><th>File</th>'+
      '<th>Package</th><th>State</th><th>When</th></tr></thead><tbody>'+
      hist.map(h=>`<tr><td class=pick><input type=checkbox data-key="${esc(h.file_key)}"></td>
        <td class=name><a href="${esc(h.url)}" target=_blank rel=noreferrer>${esc(fileName(h.url))}</a></td>
        <td class=mini>${esc((h.package||'').slice(0,46))}</td>
        <td><span class="tag ${esc(h.state)}">${esc(String(h.state).replace('_',' '))}</span></td>
        <td class=mini>${esc((h.last_seen||'').replace('T',' ').slice(0,16))}</td></tr>`).join('')+
      '</tbody></table>';
    $('#histOut').querySelectorAll('input[data-key]').forEach(c=>c.onchange=()=>{
      $('#bForget').disabled=!$('#histOut').querySelector('input[data-key]:checked');
    });
  }catch(e){}
}
$('#bForget').onclick=async()=>{
  const keys=$$('#histOut input[data-key]:checked').map(c=>c.dataset.key);
  if(!keys.length) return;
  await post('/api/history/forget',{keys});
  note(`${keys.length} file(s) forgotten — they can be queued again`);
  $('#bForget').disabled=true; loadHistory();
};
$('#bReconcile').onclick=async()=>{
  const b=$('#bReconcile'); b.disabled=true; b.textContent='Reading JD…';
  try{
    const d=await post('/api/jd/reconcile');
    note(d.ok?`JD holds ${d.in_jd} file(s) · ${d.confirmed} confirmed · ${d.gone} gone`
             :`reconcile failed: ${d.detail}`);
    loadHistory(); loadJd();
  }catch(e){ note('reconcile failed: '+e.message); }
  finally{ b.disabled=false; b.textContent='Reconcile with JD'; }
};

/* ---------------- side panels ---------------- */
async function loadJd(){
  try{
    const [s,st]=await Promise.all([
      (await fetch('/api/jd')).json(), (await fetch('/api/jd/state')).json()]);
    $('#pJd').className='pill '+(s.ok?'ok':'bad');
    $('#pJd').innerHTML='jd: <b>'+(s.ok?'ready':'down')+'</b>';
    const api=st.api||{}, dead=(st.offline||[]).length;
    $('#bCleanup').disabled=!api.configured||!dead;
    $('#bCleanup').textContent=dead?`clear ${dead} dead`:'clear dead';
    $('#jdBox').innerHTML=
      `<div class=kv><span>link source</span><span>${esc(st.source||'-')}</span></div>`+
      `<div class=kv><span>folderwatch</span><span>${s.ok?'ready':'unavailable'}</span></div>`+
      (api.configured?`<div class=kv><span>API</span><span class="${api.ok?'':'bad'}">${api.ok?esc(api.device||'connected'):'down'}</span></div>`
                     :`<div class=kv><span>API</span><span>not configured</span></div>`)+
      (st.ok?`<div class=kv><span>LinkGrabber</span><span>${st.linkgrabber.length}</span></div>
       <div class=kv><span>Downloads</span><span>${st.downloads.length}</span></div>
       ${dead?`<div class=kv><span>offline</span><span class=bad>${dead}</span></div>`:''}
       <div style="height:8px"></div>
       ${(st.packages||[]).slice(0,12).map(p=>`<div class=card><b>${esc((p.name||'').slice(0,44))}</b><small>${esc(p.folder||'')}${p.children?' · '+p.children+' file(s)':''}${p.bytes?' · '+fmtBytes(p.bytes):''}</small></div>`).join('')||'<div class=mini>no packages</div>'}`
      :`<div class=mini>${esc(st.detail||'JD state unreadable')}</div>`);
  }catch(e){}
}
async function loadHosts(){
  try{
    const d=await (await fetch('/api/hosts')).json();
    const on=new Set(d.enabled);
    $('#pHosts').innerHTML='hosts: <b>'+d.enabled.length+'</b>';
    $('#hostList').innerHTML=d.known.map(h=>
      `<label class=ctl style="display:flex;padding:2px 0">
        <input type=checkbox data-host="${esc(h.key)}" ${on.has(h.key)?'checked':''}>
        <span>${esc(h.label)}</span></label>`).join('');
    $$('#hostList input[data-host]').forEach(c=>c.onchange=async()=>{
      const keys=$$('#hostList input[data-host]:checked').map(x=>x.dataset.host);
      if(!keys.length){ c.checked=true; note('at least one host must stay enabled'); return; }
      await post('/api/hosts',{keys}); note('harvesting: '+keys.join(', ')); loadHosts();
    });
  }catch(e){}
}
async function loadSide(){
  try{
    const w=await (await fetch('/api/watches')).json();
    $('#watches').innerHTML=(w.watches||[]).map(x=>
      `<div class=card><b>${esc(x.name||x.ref)}</b><small>${esc(x.ref)}</small></div>`).join('')||'none';
  }catch(e){}
  try{
    const c=await (await fetch('/api/crawls?limit=12')).json();
    $('#crawls').innerHTML=(c.crawls||[]).map(x=>
      `<div class=card><b>${esc((x.url||'').slice(-46))}</b>
       <small>${x.fresh} new / ${x.links} link(s) · ${x.pages}p · ${esc((x.ts||'').replace('T',' ').slice(5,16))}</small></div>`
      ).join('')||'none';
  }catch(e){}
}

/* ---------------- trace ---------------- */
async function loadTrace(){
  try{
    const d=await (await fetch('/api/trace?limit=150&since='+lastTrace)).json();
    const st=d.status||{};
    $('#pTrace').innerHTML='trace: <b>'+(st.enabled?st.held+'/'+st.capacity:'off')+'</b>';
    if(!d.events.length) return;
    lastTrace=d.events[d.events.length-1].n;
    const box=$('#trace');
    if(box.textContent==='waiting…') box.textContent='';
    const atBottom=box.scrollTop+box.clientHeight>=box.scrollHeight-30;
    d.events.forEach(e=>{
      const t=new Date(e.ts*1000).toLocaleTimeString();
      const extra=Object.entries(e).filter(([k])=>
        !['n','ts','stage','msg'].includes(k)).map(([k,v])=>`${k}=${v}`).join(' ');
      const div=document.createElement('div');
      div.className='t-'+e.stage+(e.error?' err':'');
      div.textContent=`${t} [${e.stage}] ${e.msg}${extra?' '+extra:''}`;
      box.appendChild(div);
    });
    while(box.childNodes.length>400) box.removeChild(box.firstChild);
    if(atBottom) box.scrollTop=box.scrollHeight;
  }catch(e){}
}
$('#bTraceClear').onclick=async()=>{
  await post('/api/trace/clear'); $('#trace').textContent=''; lastTrace=0;
};

/* ---------------- progress + misc ---------------- */
function fmtBytes(n){
  n=Number(n)||0; if(!n) return '';
  const u=['B','KB','MB','GB','TB']; let i=0;
  while(n>=1024&&i<u.length-1){n/=1024;i++}
  return n.toFixed(n<10&&i?1:0)+' '+u[i];
}
$('#bCleanup').onclick=async()=>{
  const b=$('#bCleanup'); b.disabled=true;
  try{
    const d=await post('/api/jd/cleanup');
    note(`removed ${d.removed} dead link(s); ${d.forgotten} forgotten so they can be found again`);
    loadJd(); loadHistory();
  }catch(e){ note('cleanup failed: '+e.message); b.disabled=false; }
};

function showProg(d,t,txt){ $('#prog').style.display='block';
  $('#progText').textContent=txt||''; $('#progPct').textContent=t?Math.round(d/t*100)+'%':'';
  $('#progBar').style.width=(t?d/t*100:0)+'%'; }
function hideProg(){ setTimeout(()=>$('#prog').style.display='none',600); }
function note(m){
  const box=$('#trace'); if(!box) return;
  const div=document.createElement('div'); div.className='t-crawl';
  div.textContent=new Date().toLocaleTimeString()+' [ui] '+m;
  box.appendChild(div); box.scrollTop=box.scrollHeight;
}
$('#bWatch').onclick=async()=>{
  const url=$('#url').value.trim(); if(!url) return;
  try{ const d=await post('/api/watches',{url}); note(`watching "${d.name}" — ${d.baselined} baselined`); loadSide(); }
  catch(e){ note('watch failed: '+e.message); }
};
$('#url').addEventListener('keydown',e=>{ if(e.key==='Enter') $('#bCrawl').click(); });

new EventSource('/events').onmessage=e=>{
  const m=JSON.parse(e.data), p=m.payload||{};
  if(m.kind==='resolve_progress') showProg(p.done,p.total,p.title||'');
  if(m.kind==='crawl') hideProg();
};

loadHosts(); loadJd(); loadSide(); loadHistory(); loadTrace();
setInterval(loadTrace,1500);
setInterval(loadJd,15000);
</script></body></html>
"""
