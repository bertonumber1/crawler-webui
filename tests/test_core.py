import json
import os
import tempfile
from pathlib import Path


def test_crawljob_atomic_and_dedup():
    with tempfile.TemporaryDirectory() as td:
        os.environ['CW_FOLDERWATCH'] = td
        os.environ['CW_DOWNLOAD_ROOT'] = '/output/_CRAWLER'
        from crawler import crawljob
        path = crawljob.write([
            'https://rapidgator.net/file/AAA',
            'https://rapidgator.net/file/AAA',
            'https://rapidgator.net/file/BBB',
        ], 'Test Release')
        p = Path(path)
        assert p.exists()
        assert not Path(str(p) + '.tmp').exists()
        data = json.loads(p.read_text())
        assert isinstance(data, list) and len(data) == 1
        job = data[0]
        assert job['autoStart'] == 'FALSE'
        assert job['autoConfirm'] == 'TRUE'
        assert job['text'].splitlines() == [
            'https://rapidgator.net/file/AAA',
            'https://rapidgator.net/file/BBB',
        ]
        assert job['downloadFolder'] == '/output/_CRAWLER/Test Release'


def test_health_imports():
    with tempfile.TemporaryDirectory() as td:
        os.environ['CW_DATA_DIR'] = td
        os.environ['CW_FOLDERWATCH'] = td
        from crawler.server import app
        routes = {r.path for r in app.routes}
        assert '/health' in routes
        assert '/api/crawljob' in routes
        assert '/api/resolve' in routes
