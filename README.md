# crawler-webui

Paste a URL, see what's on the page, pick the links you want.
FastAPI + SSE on **:8096**.

Built for watching XDA threads and forums, but the crawler is generic — it
takes any URL and prefers a feed if one exists.

## Run

    cd /home/media/crawler-webui
    python3 -m uvicorn crawler.server:app --host 0.0.0.0 --port 8096

Then http://192.168.0.147:8096

No venv: fastapi 0.138, uvicorn, httpx, bs4 and lxml are all system packages.

## How the crawl works

1. **Resolve** — sniffs whether the URL is a feed, a forum listing or an
   article. XenForo (which XDA runs) publishes RSS per forum and per thread, so
   it looks for that first and uses it if found. One small request, no HTML
   parsing, nothing that breaks when the site reskins.
2. **Conditional fetch** — ETag and Last-Modified are stored per URL in
   `http_cache` and replayed as If-None-Match / If-Modified-Since. A poll with
   nothing new costs one 304 and no body.
3. **Extract** — feed entries, or XenForo posts from HTML, or a single generic
   article as a last resort.
4. **Classify links** — every outbound link is bucketed: `source`
   (GitHub/GitLab), `build` (MEGA, MediaFire, AndroidFileHost, SourceForge,
   Drive...), `video`, `forum`, `other`. Premium-gated hosts are flagged so you
   know before you click.
5. **Baseline** — adding a watch marks everything currently present as seen, so
   pointing it at a ten-year-old thread doesn't fire 400 alerts. Only genuinely
   new items count after that.

## Politeness

All network traffic goes through `fetch.py`, which enforces robots.txt, a 2s
minimum gap per host, conditional GET, and an honest User-Agent. A 403 to an
honest client is reported as the host declining — there is no bot-challenge
workaround in here, by design.

## FlareSolverr

The crawler can optionally fall back to a local FlareSolverr instance when the
normal HTTP request gets a challenge/block response. The default address is
`http://192.168.0.182:8181/v1`, so no UI change is required.

The normal request is attempted first. By default, FlareSolverr is tried for
HTTP **403, 429, or 503** responses. Configure it with environment variables:

    FLARESOLVERR_ENABLED=1
    FLARESOLVERR_URL=http://192.168.0.182:8181/v1
    FLARESOLVERR_TIMEOUT=120
    FLARESOLVERR_ON=403,429,503

For example:

    export FLARESOLVERR_URL=http://192.168.0.182:8181/v1
    export FLARESOLVERR_ENABLED=1
    python3 -m uvicorn crawler.server:app --host 0.0.0.0 --port 8096

This keeps the existing RSS/HTML parsing and Rapidgator link extraction intact:
once FlareSolverr returns the page HTML, `web.py` passes it through the same
`links.extract()` code, which already classifies `rapidgator.net` as `build`
and marks it as a premium host.

Use this only for sites you are permitted to crawl, and continue to respect
their access rules and rate limits.

## Proxy

Box in the header. `http://`, `https://`, `socks5://` or `socks5h://`; blank is
direct. **Test** compares the IP the far end sees with and without the proxy —
if they match, the proxy isn't carrying your traffic, which is the failure
people usually miss. Changing it clears cached robots and rate-limit state,
since a different egress is a different conversation with the host.

Nothing is listening locally to proxy through yet. gluetun has a built-in HTTP
proxy but `HTTPPROXY=` is empty and port 8888 isn't published. Enabling it means
recreating gluetun, which bounces every container in its namespace
(qbittorrent, sabnzbd, jackett, bpdl-web, prowlarr, flaresolverr).

## JDownloader handoff

Tick links, press **Send selected links to JDownloader**. Writes a `.crawljob`
into JD's folderwatch dir with `autoStart=FALSE`, so it lands in JD and waits
for you. Only links you select are ever written; nothing auto-submits.

Override the path with `CW_FOLDERWATCH` if JD moves.

## Layout

    crawler/server.py          FastAPI, SSE, the page
    crawler/fetch.py           robots, throttle, conditional GET, proxy
    crawler/providers/base.py  provider protocol — one file per new source
    crawler/providers/web.py   the generic crawler
    crawler/providers/dryrun.py  offline fixture provider, no network
    crawler/links.py           link classification
    crawler/crawljob.py        JD folderwatch export
    crawler/db.py              SQLite: watches, seen, queue, settings, http_cache
    fixtures/                  canned data for dryrun

## API

    POST /api/crawl              {url}          crawl once, store nothing
    POST /api/check              {url}          preflight, per-check pass/fail
    GET  /api/watches
    POST /api/watches            {url}          add + baseline
    DELETE /api/watches/{id}
    POST /api/watches/{id}/test
    GET  /api/queue              [?status=]
    GET  /api/settings           current proxy
    POST /api/settings           {proxy}
    POST /api/settings/proxy-test
    GET  /api/jd                 folderwatch reachable?
    POST /api/crawljob           {urls, name}   write selected links to JD
    GET  /events                 SSE

## Not done yet

- **No poller.** Watches are stored and baselined but nothing polls them on a
  timer. Crawling is manual.
- **Notifiers not wired.** `notify-send` and the Gotify helper in
  `pager/sfrs.py:1624` are both available; neither is hooked up.
- **No systemd unit**, so it dies on reboot.


## Drupal release crawler

This build is intentionally Drupal-first for release/catalogue sites. The HTML
page is parsed before any RSS/Atom fallback because feeds often omit the
download links present on the actual release node.

The parser recognises common Drupal node/view/content structures, separates
distinct listing rows, preserves a single release page as one item, and passes
the resulting HTML plus absolute hrefs to `links.extract()`.

Rapidgator classification and JDownloader handoff remain downstream and are
not tied to the fetch mechanism.

FlareSolverr is also triggered when the origin returns a normal challenge page
with HTTP 200, not only on 403/429/503. Its documented API is `POST /v1` with
`cmd: request.get`, and the returned `solution.response` is fed directly back
into the same Drupal parser.


## JDownloader LinkCrawler rules

This build includes the `sergxerj/jdownloader2-crawler-rule-json-schema`
rule model and local JSON schemas under `jd_rules/`.

Supported types: `DEEPDECRYPT`, `REWRITE`, `DIRECTHTTP`, `FOLLOWREDIRECT`,
`SUBMITFORM`. The correct JDownloader spelling is `DEEPDECRYPT`, not
`DEEPCRYPT`.

Generate generic examples with `python3 jd_rules_cli.py` and validate a
multi-rule file with `python3 jd_rules_cli.py --validate FILE`.
The WebUI exposes `/api/jd/rules`, `/api/jd/rules/validate`, and
`/api/jd/rules/save`.
