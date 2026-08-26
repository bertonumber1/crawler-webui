"""Shortened display URLs must never become a file identity.

Forums print a shortened URL as the visible text of a link whose href holds
the real one. The danger is not that the short form fails -- it is that it
succeeds: /file/abc...part1.rar parses as file id "abc", a plausible identity
for a file that does not exist, which would be written to the sent-history and
suppress the real file from then on.
"""
from crawler import hosts
from crawler.links import extract

REAL = "https://rapidgator.net/file/abc123def/VA-Real-(CAT01)-CD-FLAC-1994.rar.html"
SHORT = "https://rapidgator.net/file/abc...part1.rar"


def test_display_text_is_dropped_and_the_href_survives():
    got = [l["url"] for l in extract(f'<a href="{REAL}">{SHORT}</a>', [REAL])]
    assert got == [REAL]


def test_truncated_url_is_dropped_from_plain_text_too():
    # The structural guard only covers anchor text; this one covers the rest.
    assert [l["url"] for l in extract(f"see {SHORT} for the file")] == []


def test_truncated_url_is_dropped_from_a_data_attribute():
    html = f'<div data-url="{SHORT}"></div>'
    assert SHORT not in [l["url"] for l in extract(html)]


def test_the_reducer_refuses_a_truncated_url_outright():
    """The identity boundary must refuse it, not just the extractor.

    Left to itself the reducer does not error on /file/abc...part1.rar -- it
    succeeds, returning file id "abc". That is the whole danger: a plausible
    identity for a file that does not exist, which the sent-history would
    record as downloaded and use to suppress the real file for good. Anything
    reaching the reducer directly, such as an API caller, must be refused here
    because it never passed through extract().
    """
    assert hosts.reduce(SHORT) == (None, None)
    assert hosts.reduce(REAL)[0] == "rapidgator:abc123def"
