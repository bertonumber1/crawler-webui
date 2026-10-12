"""Following the pager, without inventing URLs that loop back to page one."""
from crawler import pagination


def test_rel_next_is_preferred():
    html = '<html><head><link rel="next" href="/threads/x/page-2"></head><body></body></html>'
    assert pagination.next_page(html, "https://f.test/threads/x") == "https://f.test/threads/x/page-2"


def test_numbered_pager_link_is_used_when_no_rel_next():
    html = ('<html><body><a href="/t/9?page=2">2</a>'
            '<a href="/t/9?page=17">Last</a></body></html>')
    assert pagination.next_page(html, "https://f.test/t/9?page=1") == "https://f.test/t/9?page=2"


def test_last_page_reports_no_next():
    assert pagination.next_page("<html><body>end</body></html>", "https://f.test/t/9") is None


def test_page_number_reads_query_and_path():
    assert pagination.page_number("https://f.test/t/9?page=4") == 4
    assert pagination.page_number("https://f.test/threads/a/page-3") == 3
    assert pagination.page_number("https://f.test/t/9") is None


def test_offsite_pager_links_are_ignored():
    html = '<html><body><a href="https://other.test/t/9?page=2">2</a></body></html>'
    assert pagination.next_page(html, "https://f.test/t/9?page=1") is None


def test_walk_stops_at_max_pages_and_does_not_revisit():
    pages = {
        "https://f.test/t/1": '<link rel="next" href="/t/2">',
        "https://f.test/t/2": '<link rel="next" href="/t/1">',   # a loop
    }
    seen = [u for u, _ in pagination.walk(
        "https://f.test/t/1", lambda u: (200, pages.get(u, ""), {}), max_pages=5)]
    assert seen == ["https://f.test/t/1", "https://f.test/t/2"]


def test_forumotion_offset_pager():
    # No rel=next; the number in /f31p20- is an offset, not a page.
    from crawler.pagination import next_page
    html = ('<a href="/f31p40-x">3</a><a href="/f31p20-x">2</a>'
            '<a href="/f32p20-other">other board</a><a href="/f31p20-x" class="pag-img">Next</a>')
    assert next_page(html, "https://x.foroactivo.com/f31-x") == "https://x.foroactivo.com/f31p20-x"
    assert next_page(html, "https://x.foroactivo.com/f31p20-x") == "https://x.foroactivo.com/f31p40-x"
    assert next_page(html, "https://x.foroactivo.com/f31p40-x") is None


def test_zero_indexed_pager_follows_next_label_not_number():
    # Drupal (flacattack.net): the button showing "2" is ?page=1.
    html = ('<ul class="pager"><li class="pager-current">1</li>'
            '<li class="pager-item"><a title="Go to page 2" href="/genre/hh?page=1">2</a></li>'
            '<li class="pager-item"><a title="Go to page 3" href="/genre/hh?page=2">3</a></li>'
            '<li class="pager-next"><a title="Go to next page" href="/genre/hh?page=1">next ›</a></li>'
            '<li class="pager-last"><a title="Go to last page" href="/genre/hh?page=130">last »</a></li></ul>')
    assert pagination.next_page(html, "https://f.test/genre/hh") == "https://f.test/genre/hh?page=1"
    assert pagination.next_page(html.replace("?page=1\">next", "?page=2\">next"),
                                "https://f.test/genre/hh?page=1") == "https://f.test/genre/hh?page=2"


def test_last_link_is_not_mistaken_for_next():
    html = '<ul><li class="pager-last"><a href="/g?page=130">last »</a></li></ul>'
    assert pagination.next_page(html, "https://f.test/g?page=130") is None


def test_walk_zero_means_all_pages():
    pages = {f"https://f.test/p{i}": f'<a href="/p{i+1}">next ›</a>' for i in range(30)}
    got = [u for u, _ in pagination.walk("https://f.test/p0", lambda u: (200, pages.get(u, ""), {}), max_pages=0)]
    assert len(got) == 30
