from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.web.pages import VersionedStaticFiles, asset_url, render_page


@pytest.mark.parametrize(
    'route',
    ['/', '/chat', '/keys', '/settings', '/research', '/roleplay',
     '/agents', '/skills', '/library', '/share/example'],
)
def test_workspace_routes_version_assets(route: str) -> None:
    client = TestClient(app)
    response = client.get(route)
    assert response.status_code == 200
    assert '?v=' in response.text
    assert response.text.count('/static/js/ui/dialog.js?v=') == 1
    assert response.text.count('/static/css/ui.css?v=') == 1
    fallback = 'stage' if route == '/roleplay' else 'dark'
    assert f'const fallback="{fallback}";' in response.text
    assert '{{default_theme}}' not in response.text
    assert response.headers['cache-control'] == 'no-cache'


def test_page_versions_local_assets(tmp_path: Path) -> None:
    (tmp_path / 'app.js').write_text('window.ready = true;', encoding='utf-8')
    (tmp_path / 'chat.html').write_text(
        '<script src="/static/app.js"></script><a href="/chat">Chat</a>',
        encoding='utf-8',
    )
    response = render_page('chat.html', tmp_path)
    assert asset_url('/static/app.js', tmp_path) in bytes(response.body).decode()
    assert '<a href="/chat">Chat</a>' in bytes(response.body).decode()
    assert response.headers['cache-control'] == 'no-cache'


def test_asset_version_changes_with_content(tmp_path: Path) -> None:
    path = tmp_path / 'app.js'
    path.write_text('first', encoding='utf-8')
    initial = asset_url('/static/app.js?mode=module#main', tmp_path)
    path.write_text('second version', encoding='utf-8')
    updated = asset_url('/static/app.js?mode=module#main', tmp_path)
    assert initial != updated
    assert urlsplit(updated).fragment == 'main'
    assert 'mode=module&v=' in updated


@pytest.mark.parametrize(
    'url', ['/static/../private.js', '/static/missing.js', '//example.com/app.js']
)
def test_asset_urls_outside_static_root_unchanged(tmp_path: Path, url: str) -> None:
    assert asset_url(url, tmp_path) == url


def test_page_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        render_page('../private.html', tmp_path)


def test_only_matching_asset_versions_are_immutable(tmp_path: Path) -> None:
    (tmp_path / 'app.js').write_text('window.ready = true;', encoding='utf-8')
    application = FastAPI()
    application.mount('/static', VersionedStaticFiles(directory=tmp_path))
    with TestClient(application) as client:
        for url in ('/static/app.js', '/static/app.js?v=stale'):
            assert client.get(url).headers['cache-control'] == 'no-cache'
        versioned = asset_url('/static/app.js', tmp_path)
        response = client.get(versioned)
        assert response.headers['cache-control'] == 'public, max-age=31536000, immutable'
        cached = client.get(versioned, headers={'If-None-Match': response.headers['etag']})
        assert cached.status_code == 304
        assert cached.headers['cache-control'] == response.headers['cache-control']
