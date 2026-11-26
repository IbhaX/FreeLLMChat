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
     '/agents', '/skills', '/library', '/memory', '/share/example'],
)
def test_workspace_routes_version_assets(route: str) -> None:
    client = TestClient(app)
    response = client.get(route)
    assert response.status_code == 200
    assert '?v=' in response.text
    assert response.text.count('/static/js/ui/dialog.js?v=') == 1
    assert response.text.count('/static/css/ui.css?v=') == 1
    assert 'const fallback="dark";' in response.text
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


def test_sitemap_lists_public_pages() -> None:
    from xml.etree import ElementTree

    client = TestClient(app)
    response = client.get('/sitemap.xml', headers={'host': 'ichat.test'})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/xml')
    ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    root = ElementTree.fromstring(response.content)
    locs = [el.text for el in root.findall('s:url/s:loc', ns)]
    assert locs == ['http://ichat.test/', 'http://ichat.test/login']


def test_sitemap_respects_forwarded_proto() -> None:
    client = TestClient(app)
    response = client.get('/sitemap.xml', headers={
        'host': 'ichat.test', 'x-forwarded-proto': 'https'})
    assert '<loc>https://ichat.test/</loc>' in response.text


def test_robots_points_to_sitemap() -> None:
    client = TestClient(app)
    response = client.get('/robots.txt', headers={'host': 'ichat.test'})
    assert response.status_code == 200
    assert 'Sitemap: http://ichat.test/sitemap.xml' in response.text
    assert 'Disallow: /chat' in response.text
    assert 'Disallow: /v1/' in response.text


def test_landing_has_full_seo_block() -> None:
    client = TestClient(app)
    response = client.get('/', headers={'host': 'ichat.test', 'x-forwarded-proto': 'https'})
    html = response.text
    assert html.count('<title>') == 1
    assert html.count('name="description"') == 1
    assert '<link rel="canonical" href="https://ichat.test/">' in html
    assert 'property="og:image" content="https://ichat.test/static/img/logo.png"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert 'application/ld+json' in html
    assert 'content="index, follow' in html
    assert 'x-robots-tag' not in response.headers


@pytest.mark.parametrize('route', ['/chat', '/settings', '/share/example', '/pay'])
def test_private_pages_are_noindex(route: str) -> None:
    client = TestClient(app)
    response = client.get(route)
    assert response.headers['x-robots-tag'] == 'noindex, nofollow'
    assert 'content="noindex, nofollow"' in response.text
    assert 'rel="canonical"' not in response.text


def test_web_manifest() -> None:
    client = TestClient(app)
    response = client.get('/site.webmanifest')
    assert response.status_code == 200
    assert response.json()['name'] == 'IbhaX'


def test_pages_are_gzipped_but_api_is_not() -> None:
    client = TestClient(app)
    page = client.get('/', headers={'accept-encoding': 'gzip'})
    assert page.headers.get('content-encoding') == 'gzip'
    health = client.get('/healthz', headers={'accept-encoding': 'gzip'})
    assert 'content-encoding' not in health.headers
