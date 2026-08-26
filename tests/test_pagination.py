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
