"""The page is one inline document, so nothing type-checks it. These do.

A stray escape once made the whole <script> block fail to parse, which bound
no handlers at all: every button on the page was dead while the API behind it
worked perfectly. Nothing caught it, because nothing looked at the page.
"""
import json
import re

import pytest

from crawler.ui import PAGE


@pytest.fixture(scope="module")
def script():
    m = re.search(r"<script>(.*?)</script>", PAGE, re.S)
    assert m, "the page has no script block"
    return m.group(1)


def test_every_element_the_script_reaches_for_exists(script):
    ids = set(re.findall(r"\bid=([A-Za-z][\w-]*)", PAGE))
    ids |= set(re.findall(r'\bid="([^"]+)"', PAGE))
    referenced = set(re.findall(r"\$\('#([\w-]+)'\)", script))
    missing = sorted(referenced - ids)
    assert not missing, f"script binds to elements that do not exist: {missing}"


def test_no_stray_escape_outside_a_string(script):
    # The exact shape of the bug: a literal backslash-n sitting in code.
    for line in script.splitlines():
        code = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", "", line)
        assert "\\n" not in code, f"escape outside a string literal: {line.strip()[:80]}"


def test_script_parses_as_javascript(script):
    """Authoritative syntax check when node is available.

    A hand-rolled bracket counter gives false positives on regex literals, so
    this defers to a real parser and skips rather than guessing when there
    isn't one.
    """
    import shutil, subprocess, tempfile, os

    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("node not available")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert r.returncode == 0, f"page script does not parse:\n{r.stderr[:400]}"
    finally:
        os.unlink(path)


def test_every_endpoint_the_ui_calls_is_served(script):
    from crawler.server import app

    served = {r.path for r in app.routes if hasattr(r, "path")}
    called = set(re.findall(r"fetch\('(/[^'?]+)", script))
    called |= set(re.findall(r"post\('(/[^'?]+)", script))
    missing = sorted(p for p in called if p not in served)
    assert not missing, f"the page calls endpoints that do not exist: {missing}"


def test_page_declares_a_title_and_charset():
    assert "<title>" in PAGE and "charset=utf-8" in PAGE
