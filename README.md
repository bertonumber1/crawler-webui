# crawler-webui

Paste a URL, see what's on the page, pick the links you want.
FastAPI + SSE on **:8096**.

Built for crawling Drupal release/tag/collection pages and handing resolved file-host links to JDownloader.
takes any URL and prefers a feed if one exists.

## Run

    cd /home/media/crawler-webui
    python3 -m uvicorn crawler.server:app --host 0.0.0.0 --port 8096

Then http://192.168.0.147:8096

No venv: fastapi 0.138, uvicorn, httpx, bs4 and lxml are all system packages.

## How the crawl works

1. **Resolve** — sniffs whether the URL is a feed, a forum listing or an
   article. Sites can expose RSS or HTML listing pages, so
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
the normal HTTP client is attempted first; configured FlareSolverr is used only
as the explicit fallback when a challenge/block response is detected.

## FlareSolverr

The crawler can optionally fall back to the local FlareSolverr instance. In the Gluetun network namespace the crawler and FlareSolverr share `127.0.0.1`, so the default address is `http://127.0.0.1:8191/v1`.

The normal request is attempted first. By default, FlareSolverr is tried for
HTTP **403, 429, or 503** responses. Configure it with environment variables:

    FLARESOLVERR_ENABLED=1
    FLARESOLVERR_URL=http://127.0.0.1:8191/v1
    FLARESOLVERR_TIMEOUT=120
    FLARESOLVERR_ON=403,429,503

For example:

    export FLARESOLVERR_URL=http://127.0.0.1:8191/v1
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

Tick links, press **Send selected links to JDownloader**. The app writes a JSON-format `.crawljob` into JD's Folder Watch directory. `autoConfirm=TRUE` puts the links into JD; the **start in JD** checkbox controls `autoStart`. Only links you select are written.

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

    POST /api/crawl              {url}          crawl + resolve once, store nothing
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

## Docker deployment

The supported deployment is Docker. The crawler stores its SQLite state in
`./data` and receives JDownloader Folder Watch through a bind mount. Inside the
crawler container the Folder Watch path is `/jdownloader/folderwatch`; the
`downloadFolder` written into each `.crawljob` remains `/output/_CRAWLER/...`,
which is the path JDownloader sees inside its own container.

The supplied compose file is already configured for the main Gluetun stack:
`crawler-webui` shares Gluetun's network namespace and uses FlareSolverr at
`127.0.0.1:8191`. Gluetun must publish `8096:8096` because the crawler cannot
publish ports of its own when using `network_mode: container:gluetun`.

The host Folder Watch directory defaults to:

    /home/media/docker/torrentvpn-app/jdownloader/config/folderwatch

Build and start:

    docker compose up -d --build

Check:

    docker compose ps
    docker compose logs --tail=200 crawler-webui
    curl http://127.0.0.1:8096/health

The container-side Folder Watch path is `/jdownloader/folderwatch`. Do not put
the host path into `CW_FOLDERWATCH` inside the container. The `downloadFolder`
in each `.crawljob` remains `/output/_CRAWLER/...`, which is the path JD sees
inside its own container.
