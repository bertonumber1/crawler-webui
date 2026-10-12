"""WordPress tape-pack sites (ravetapepacks.com): reading posts, naming the
files after the site's own captions, and handing JD the names."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from crawler import crawljob, hosts, links as linkmod, naming
from crawler.providers import wordpress
from crawler.providers.wordpress import assign_shared_files, parse_post

@pytest.fixture(autouse=True)
def _site_host_enabled():
    before = [h.key for h in hosts.enabled()]
    hosts.set_enabled(before + ["ravetapepacks"])
    yield
    hosts.set_enabled(before)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_DB_PATH", str(tmp_path / "c.db"))
    monkeypatch.setenv("CW_FOLDERWATCH", str(tmp_path))
    from crawler import db as db_mod, history as hist_mod
    importlib.reload(db_mod)
    importlib.reload(hist_mod)
    from crawler import server as srv
    importlib.reload(srv)
    yield TestClient(srv.app)
    importlib.reload(db_mod)
    importlib.reload(hist_mod)


BASE = "https://www.ravetapepacks.com/"
M = BASE + "music/"


def post(pid, title, body, date="2023-02-23T23:34:27"):
    return {"id": pid, "date": date, "link": f"{BASE}?p={pid}",
            "title": {"rendered": title}, "content": {"rendered": body}}


def player(caption, url):
    return (f'<p>{caption}</p>\n<figure class="wp-block-audio"><audio controls '
            f'src="{url}"></audio></figure>\n<div class="wp-block-spacer"></div>\n')


JUBILEE = post(2687, "The Best Of British &#8211; The Jubilee Jam",
    '<figure><img src="' + BASE + 'wp-content/uploads/2023/02/cover-4.jpg" '
    'srcset="' + BASE + 'wp-content/uploads/2023/02/cover-4-212x300.jpg 212w"></figure>'
    "<p>YEAR: 2002</p><p>MCS: DET, SKIBADEE</p><p>MP3S:</p>"
    + player("TAPE 1 (SIDE A) &#8211; SWAN-E", M + "Jubilee/Tape_1_(Side_A)_-_Swan-E.mp3")
    + '<script>(adsbygoogle = window.adsbygoogle || []).push({});</script>'
    + player("TAPE 7 (SIDE A) &#8211; R-TYPE B2B PLAYA", M + "Jubilee/Tape_7_(Side_A)_-_R-Type_B2B_Playa.mp3")
    + player("SIDE 7 &#8211; ECHO / KROME", M + "Jubilee/Side_7_-_Echo_&amp;_Krome.mp3")
    + player("SHY FX", M + "Jubilee/Shy_FX.mp3"))


# ------------------------------------------------------------------ naming

def test_shouting_caption_takes_the_upload_names_spelling():
    assert naming.track_title("TAPE 7 (SIDE A) – R-TYPE B2B PLAYA",
                              M + "x/Tape_7_(Side_A)_-_R-Type_B2B_Playa.mp3") \
        == "Tape 7 (Side A) - R-Type B2B Playa"
    assert naming.track_title("SHY FX", M + "x/Shy_FX.mp3") == "Shy FX"


def test_single_letters_stay_capital():
    assert naming.track_title("JASON KAYE (SIDE A)", M + "x/jk.mp3") == "Jason Kaye (Side A)"


def test_slash_between_djs_becomes_ampersand():
    assert naming.track_title("SIDE 7 – ECHO / KROME", M + "x/s7.mp3") == "Side 7 - Echo & Krome"


def test_tape_number_only_in_upload_name_is_kept():
    assert naming.track_title("Jumping Jack Frost (Side B)",
                              M + "x/Tape_5_(Side_B)_-_Jumping_Jack_Frost.mp3") \
        == "Tape 5 - Jumping Jack Frost (Side B)"


def test_confirmed_caption_typo_is_corrected():
    assert naming.track_title("TAPE 6 – BILLY HUNTER & MR HYDE",
                              M + "x/Tape_6_-_Billy_Bunter_&_Mr_Hyde.mp3") \
        == "Tape 6 - Billy Bunter & Mr Hyde"


def test_mixed_case_caption_is_left_as_written_bar_double_capitals():
    assert naming.track_title("PIlgrim & Donovan Badboy Smith", M + "x/p.mp3") \
        == "Pilgrim & Donovan Badboy Smith"


def test_side_a_and_side_1_are_the_same_side():
    assert naming.disagreement("Andy C (Side A)", M + "x/Andy_C_(Side_1).mp3") == ""
    assert "side" in naming.disagreement("Andy C (Side A)", M + "x/Andy_C_(Side_2).mp3")


def test_folder_gets_year_once_and_loses_shouting():
    assert naming.folder_name("Hysteria 9", "1995") == "Hysteria 9 (1995)"
    assert naming.folder_name("Amnesia House @ The Edge NYE 1993", "1993") \
        == "Amnesia House @ The Edge NYE 1993"
    assert naming.folder_name("ACCELERATED CULTURE 17 &#8211; ENERGY FLUX", "2004") \
        == "Accelerated Culture 17 - Energy Flux (2004)"


def test_names_are_safe_on_windows():
    assert naming.safe_component('A: "B" <C>?') == "A - 'B' C"


# ------------------------------------------------------------------ posts

def test_post_becomes_one_named_release_with_cover():
    it = parse_post(JUBILEE, BASE)
    assert it.name == "The Best Of British - The Jubilee Jam (2002)"
    names = [l["filename"] for l in it.links]
    assert names == ["01 - Tape 1 (Side A) - Swan-E.mp3",
                     "02 - Tape 7 (Side A) - R-Type B2B Playa.mp3",
                     "03 - Side 7 - Echo & Krome.mp3",
                     "04 - Shy FX.mp3",
                     "cover.jpg"]
    assert all(l["folder"] == it.name for l in it.links)
    assert all(l["source"] == f"{BASE}?p=2687" for l in it.links)
    # The full-size cover, not the srcset thumbnail.
    assert it.links[-1]["url"].endswith("/cover-4.jpg")


def test_attached_alternate_cover_gets_its_own_name():
    it = parse_post(JUBILEE, BASE, [BASE + "wp-content/uploads/2023/02/cover.jpg"])
    imgs = [l["filename"] for l in it.links if l["role"] == "image"]
    assert imgs == ["cover.jpg", "artwork 1 - cover.jpg"]


def test_post_without_players_has_no_links():
    it = parse_post(post(150, "Amnesia House &#8211; The Book of Love",
                         "<p>YEAR: 1992</p><p>MP3 FILE LINKS REMOVED AT REQUEST OF DISTRIBUTOR</p>"), BASE)
    assert it.links == [] and "REMOVED" in it.summary


def test_random_player_post_does_not_steal_a_packs_file():
    pack = parse_post(post(1, "Hysteria 20", "<p>YEAR: 1998</p>"
                           + player("Brockie", M + "Hysteria20/Brockie.mp3")
                           + player("Darren Jay (Side A)", M + "Hysteria20/Darren_Jay_(Side_A).mp3")), BASE)
    welcome = parse_post(post(2, "WELCOME", '<img src="' + BASE + 'wp-content/uploads/logo.jpg">'
                              + player("Darren Jay (Side A)", M + "Hysteria20/Darren_Jay_(Side_A).mp3")), BASE)
    out = assign_shared_files([welcome, pack])
    assert out[0].links == []
    assert len([l for l in out[1].links if l["role"] == "track"]) == 2


# ------------------------------------------------------------------ hosts

def test_only_the_sites_files_are_downloads():
    assert linkmod.is_download_host(M + "PlanetLondonVol1/Side_1_-_Hype.mp3")
    assert linkmod.is_download_host(BASE + "wp-content/uploads/2023/02/cover2.jpg")
    assert not linkmod.is_download_host(BASE + "?p=2609")
    assert not linkmod.is_download_host(BASE + "?cat=10")


def test_http_and_bare_domain_reduce_to_one_file():
    a = linkmod.canonical_download_url("http://www.ravetapepacks.com/music/D19/Brisk_@_Dreamscape_19.mp3")
    b = linkmod.canonical_download_url("https://ravetapepacks.com/music/D19/Brisk_@_Dreamscape_19.mp3")
    assert a == b == "https://www.ravetapepacks.com/music/D19/Brisk_@_Dreamscape_19.mp3"


def test_detects_wordpress():
    assert wordpress.is_wordpress('<meta name="generator" content="WordPress 6.7.10" />')
    assert not wordpress.is_wordpress("<html><body>phpBB</body></html>")


# ------------------------------------------------------------------ handoff

def test_crawljob_carries_one_file_name_per_link(tmp_path, monkeypatch):
    monkeypatch.setenv("CW_FOLDERWATCH", str(tmp_path))
    u1, u2 = M + "P/Side_1_-_Hype.mp3", BASE + "wp-content/uploads/2023/02/cover2.jpg"
    path = crawljob.write([u1, u2], "Planet London Vol 1 (1997)",
                          names={u1: "01 - Side 1 - DJ Hype.mp3", u2: "cover.jpg"})
    jobs = json.load(open(path))
    assert [(j["text"], j["filename"]) for j in jobs] == [
        (u1, "01 - Side 1 - DJ Hype.mp3"), (u2, "cover.jpg")]
    assert {j["packageName"] for j in jobs} == {"Planet London Vol 1 (1997)"}


def test_whole_site_crawl_sends_each_pack_with_its_names(client, monkeypatch):
    import crawler.server as srv

    class FakeWP:
        name = "wordpress"
        reads_whole_site = True

        def poll(self, url, limit=0, since=None):
            return [parse_post(JUBILEE, BASE)]

    monkeypatch.setattr(srv.base, "get", lambda name: FakeWP())
    monkeypatch.setattr(srv.jdapi, "configured", lambda: False)
    sent = []
    monkeypatch.setattr(srv, "_queue", lambda urls, name, subfolder="", auto_start=False,
                        names=None, source_url="": sent.append((name, names, source_url)) or "x")
    r = client.post("/api/crawl", json={"url": BASE, "provider": "wordpress",
                                       "send_each": True, "since_months": 0})
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    name, names, src = sent[0]
    assert name == "The Best Of British - The Jubilee Jam (2002)"
    assert "04 - Shy FX.mp3" in names.values() and "cover.jpg" in names.values()
    assert src == f"{BASE}?p=2687"


def test_auto_picks_wordpress_even_when_a_forum_signature_matches(monkeypatch):
    import crawler.server as srv
    page = ('<meta name="generator" content="WordPress 6.7.10" />'
            '<link rel="https://api.w.org/" href="https://x/wp-json/" />'
            '<div class="ipsType_richText">a theme borrowing forum class names</div>')
    monkeypatch.setattr(srv.fetch, "get", lambda url, conditional=False: (200, page, {}))
    monkeypatch.setattr(srv, "detect_software",
                        lambda body: [type("D", (), {"software": "Invision Community"})()])
    assert srv._pick_provider("https://www.ravetapepacks.com/") == "wordpress"


def test_event_is_the_category_under_the_event_group():
    from crawler.providers.wordpress import event_of
    cats = {52: {"id": 52, "name": "PACKS BY RAVE", "parent": 0},
            68: {"id": 68, "name": "PACKS BY DJ", "parent": 0},
            7: {"id": 7, "name": "Quest", "parent": 52},
            9: {"id": 9, "name": "Fibre Optic", "parent": 52},
            70: {"id": 70, "name": "Carl Cox", "parent": 68}}
    p = {"title": {"rendered": "Quest &#038; Fibre Optic &#8211; End Of Summer"},
         "categories": [70, 9, 7]}
    assert event_of(p, cats) == "Quest"
    assert event_of({"title": {"rendered": "x"}, "categories": [70]}, cats) == ""


def test_nested_folder_parts_are_each_made_safe():
    it = parse_post(JUBILEE, BASE, prefix="Rave Tape Packs/The Best Of British")
    assert it.links[0]["folder"] == ("Rave Tape Packs/The Best Of British/"
                                     "The Best Of British - The Jubilee Jam (2002)")
    assert it.name == "The Best Of British - The Jubilee Jam (2002)"
