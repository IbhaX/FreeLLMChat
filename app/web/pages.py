from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from starlette.responses import HTMLResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_ASSET_ATTRIBUTE = re.compile(
    r'''(?P<prefix>\b(?:src|href)\s*=\s*)(?P<quote>["'])'''
    r'''(?P<url>/static/[^"']+)(?P=quote)'''
)


_THEME_BOOTSTRAP = re.compile(
    r"<script>document\.documentElement\.dataset\.theme="
    r"localStorage\.getItem\('ui-theme'\)\|\|'(?:dark|stage)';?</script>"
)


@lru_cache(maxsize=512)
def _digest(path: Path, modified_ns: int, size: int) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:20]


def asset_version(path: Path) -> str:
    stat = path.stat()
    return _digest(path, stat.st_mtime_ns, stat.st_size)


def asset_url(url: str, static_dir: Path) -> str:
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path.startswith('/static/'):
        return url
    path = (static_dir / parts.path.removeprefix('/static/')).resolve()
    if not path.is_relative_to(static_dir.resolve()) or not path.is_file():
        return url
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != 'v']
    query.append(('v', asset_version(path)))
    return urlunsplit(('', '', parts.path, urlencode(query), parts.fragment))


def render_page(name: str, static_dir: Path) -> HTMLResponse:
    path = (static_dir / name).resolve()
    if not path.is_relative_to(static_dir.resolve()) or path.suffix != '.html':
        raise ValueError('Invalid page path')
    markup = path.read_text(encoding='utf-8')
    theme_partial = static_dir / 'partials' / 'theme.html'
    if theme_partial.is_file():
        bootstrap = theme_partial.read_text(encoding='utf-8').replace(
            '{{default_theme}}', json.dumps('dark'),
        )
        markup = _THEME_BOOTSTRAP.sub(lambda match: bootstrap, markup)
    partial = static_dir / 'partials' / 'ui.html'
    if partial.is_file():
        markup = markup.replace('</head>', partial.read_text(encoding='utf-8') + '</head>', 1)
    markup = markup.replace(
        '<link rel="stylesheet" href="/static/css/theme.css">',
        '<link rel="stylesheet" href="/static/css/themes.css">'
        '<link rel="stylesheet" href="/static/css/ui.css">',
    )
    markup = _ASSET_ATTRIBUTE.sub(
        lambda match: (
            f"{match['prefix']}{match['quote']}"
            f"{asset_url(match['url'], static_dir)}{match['quote']}"
        ),
        markup,
    )
    return HTMLResponse(markup, headers={'Cache-Control': 'no-cache'})


class VersionedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers['Cache-Control'] = 'no-cache'
        query = dict(parse_qsl(scope.get('query_string', b'').decode('latin-1')))
        if self.directory is not None and response.status_code in (200, 304):
            full_path = (Path(self.directory) / path).resolve()
            if (
                full_path.is_relative_to(Path(self.directory).resolve())
                and full_path.is_file()
                and query.get('v') == asset_version(full_path)
            ):
                response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response
