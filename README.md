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

## Auto mode

Paste one or more links and press **⚡ Auto**. Every page is read, only posts
inside the date window count, each link is checked with its own host, nothing
already sent is sent again, and each release goes to JDownloader the moment it
is read (LinkGrabber; tick "start in JD" to download straight away). It is the
Crawl button with every refinement switched on:

    provider=auto  pages=0  since_months=<saved default>  lossless_only
    send_each      stop_when_caught_up                    include_seen=false

`provider=auto` reads SMF boards with the forum reader and every other site
with the `web` reader they always used.

## Date window ("only the last N months")

Old posts on a long-lived board link to files that died years ago (on
makinamania.net: 242 of 242 SwissTransfer and ~72% of MEGA links in posts
older than a few months). The window is one setting for **every** site:

- the UI box *only the last N months* (saved; `GET/POST /api/settings/since`),
  `CW_SINCE_MONTHS` as the env default, `since_months` per crawl; 0 = no limit
- posts and board rows provably older are skipped (`crawler/postdates.py`
  reads `<time datetime>`, data-time epochs, SMF `dd/mm/yy, hh:mm`, Hoy/Ayer,
  and es/en/fr/it/pt month names); a post whose date cannot be read is KEPT
- a newest-first listing stops paging at the first page that is all older
- an SMF thread is read from its **last page backwards** to the window start:
  RECOPILATORIOS 2000's is 1,268 pages, its last 18 months 183

Quoted dates ("Cita de: X en 03/07/18") never date a post, and a member's
join date in the profile box ("Registro: 22-08-18") is never read: a post
header date always carries a time.

## Link check: lossless proven, dead dropped

With *lossless only* on, every link is asked of its host before JD sees it
(`crawler/linkcheck.py`, no download): MEGA (file and folder names decrypted
with the key in the link, as mega.nz itself does), MediaFire, Pixeldrain,
SwissTransfer, Rapidgator. A link is dropped only when the host PROVES it
dead or its real filenames prove it MP3/AAC. Hosts that cannot be asked
(transfer.it, transfernow, WeTransfer, GoFile, OpenDrive...) pass, and JD's
own link check decides. Link shorteners (tinyurl & co) are followed one hop.

## Forums and file hosts

Forum software detected: XenForo, MyBB, phpBB, Invision, vBulletin, Drupal,
SMF and Forumotion (foroactivo / forumactif boards, `/t123-slug` topics,
`/f12p20-slug` offset pages).

SMF (makinamania.net, 2.0.19): topics and boards are an id plus an offset,
`index.php?topic=N.S` for a member and `index.php/topic,N.S.html` on the
cached guest copy the same session is sometimes served -- both are accepted
everywhere. SMF's `<link rel="next">` on a topic is the NEXT TOPIC
(`prev_next=next`), so SMF paging uses the offsets and never rel=next.
Signatures (SMF, phpBB, MyBB, vBulletin, XenForo, Invision) are stripped
before any link is read: one member's signature links otherwise appear in
every topic they reply to. Members-only boards need a session: export
cookies.txt from a logged-in browser and paste it into the Session panel.
The crawler never follows a URL whose query performs an action (`eval`,
`p_vote`, `watch`, `mark`, `logout`...) - Forumotion puts a GET vote link
under every post.

`CRAWLER_HOSTS` picks what is harvested: `rapidgator` (code default),
`cloud` (compose default: Rapidgator + MEGA, Google Drive, TeraBox, Dropbox,
OneDrive, pCloud, Yandex Disk, MediaFire, Pixeldrain, GoFile, Workupload,
KrakenFiles, Send.cm, WeTransfer, SwissTransfer, Transfer.it, TransferNow,
OpenDrive), `all`, or a comma list of keys from
`crawler/hosts.py`.

## WordPress sites and self-hosted tape packs

A WordPress site (detected from its generator tag or REST links, ahead of the
forum signatures, which misread WordPress uploads paths as Invision) is read
through its own REST API (`?rest_route=/wp/v2/posts`): every post, newest
first, plus its categories and the images attached to it in the media library.
`crawler/providers/wordpress.py`. One post = one release.

Built for **ravetapepacks.com** (182 packs, 1,825 sets, 2013-2023):

- **Names come from the site, not the upload.** Each file is titled from the
  caption shown above its player (`TAPE 3 - SWAN-E & VIBES & DOUGAL`), not its
  upload name (`Tape3SwanEandVibesandDougal.mp3`). ALL-CAPS captions are
  re-cased using the upload name's own spelling (`R-Type`, `Shy FX`, `DJ EZ`);
  a tape number only the upload knows is kept; confirmed caption typos are in
  `naming.CORRECTIONS`. `crawler/naming.py`, no network, fully tested.
- **Folders follow the site's "Packs by Event" menu:**
  `Rave Tape Packs/<Event>/<Pack (Year)>/NN - <title>.mp3` plus `cover.jpg`
  (and `artwork N - ...` for alternate covers). A pack tagged with two events
  files under the one its title names first.
- A file shown in two posts (the WELCOME random player) stays with its pack.
- **Fetched politely by the crawler itself, not JDownloader.** Hosts marked
  `direct` in `hosts.py` go to `crawler/direct.py`: one file at a time, one
  connection, `CW_DIRECT_GAP` seconds between files (default 3-8), the post as
  Referer (the site's hotlink guard), `.part` + Range resume, backoff doubling
  to 30 min on refusals. JD's 20 downloads x 3 connections got the VPN address
  firewalled within minutes. Queue: `data/direct_queue.json`; status
  `GET /api/direct`, `POST /api/direct/retry|stop|start`. Files land under
  `CW_DOWNLOAD_ROOT` (JD's own `/output`, mounted) owned by `CW_OUTPUT_UID/GID`.
- A whole-site crawl ignores the pager; set the date window to 0 for an
  archive (this one stopped posting in 2023).

For other sites sent to JD with proper names, `jdapi.add_named()` adds the
package paused, renames each link once JD has collected it, then starts it
(pending renames survive a restart: `data/jd_pending_names.json`).

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
    GET  /api/settings/since     date window: {months, since}
    POST /api/settings/since     {months}       0 = no limit
    GET  /api/settings           current proxy
    POST /api/settings           {proxy}
    POST /api/settings/proxy-test
    GET  /api/jd                 folderwatch reachable?
    POST /api/crawljob           {urls, name}   write selected links to JD
    GET  /events                 SSE

## The thanks gate

dark-decade, and most foroactivo boards, hide a topic's download box behind a
thanks gate: the links are not in the page HTML until the reading account has
replied to that topic. A topic that resolves to zero links on that board is
usually gated, not blocked — the cookies, the browser UA and the pacing are
all working, and none of them can help, because there is nothing there to
read. Getting at the links means posting a reply.

That is a different kind of act from everything else here. Every other request
reads; this one writes to your account, under your name, where the board's
members and staff can see it. So it is built to be difficult to fire by
accident, and there are four things between a fresh checkout and a reply:

1. `CW_THANKS_ENABLED=1` in the environment. Unset, the API reports the
   feature as disabled, `start` refuses, and the UI panel does not render.
2. Topics queued by hand, or in one press from the topics the last crawl
   opened and found nothing in. A URL that is not a plain topic on a host in
   `CW_THANKS_HOSTS` is rejected as it is pasted.
3. The runner started from the UI. A crawl never starts it; nothing starts on
   boot.
4. Dry run turned off. Until then it walks the whole queue, reads each topic,
   finds the reply form and reports exactly what it would submit — without
   sending any of it.

Pacing is three posts, then quiet for 25 minutes, each post 40–90 seconds
after the last, under a daily ceiling. A dry run is not paced, because it
sends nothing; its speed is just the crawler's normal per-page throttle.

Nothing about the submission is invented. The reply form is read off the topic
page and its fields are carried through as they came — `tid` is a per-session
token and `lt` a post timer, and a made-up value for either is rejected. The
post carries `Referer` and `Origin`, which forum software checks, and on a
stealth host it goes out with the same browser identity the page reads use: a
reply arriving with the crawler's own User-Agent would stand out far more than
the reply itself.

A topic is thanked once, ever. The database holds a `UNIQUE` url, and the page
is re-read immediately before posting so a reply you left by hand in the
browser counts too. If the session has lapsed to guest, or the topic has no
reply form, it is skipped and said so rather than guessed at.

    GET  /api/thanks               state, pacing, queue
    POST /api/thanks/targets       queue topics          {"urls": [...]}
    POST /api/thanks/from-crawl    queue the last crawl's empty topics
    POST /api/thanks/preview       read one topic, report, write nothing
    POST /api/thanks/post-one      thank one topic now (still obeys dry run)
    POST /api/thanks/settings      {"dry_run": false, "messages": "..."}
    POST /api/thanks/start         start the runner  (refuses when disabled)
    POST /api/thanks/stop          stop it, immediately, even mid-wait

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
