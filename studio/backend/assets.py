"""The app's HTML pages with every script and stylesheet address stamped with a fingerprint of the file (?v=...).

Cloudflare in front of labelomni.com tells browsers to keep scripts and styles for 4 hours, whatever the server says,
so a changed file was not seen until then, and a page could run a new module next to an old one. With the
fingerprint a changed file has a new address and an unchanged one stays cached. Modules import each other by plain
relative paths ('./api.js'), so an import map gives each module under /js its stamped address as well.
"""
import hashlib
import json
import re
from pathlib import Path

from fastapi.responses import HTMLResponse

from . import config

ROOT = Path(config.FRONTEND_DIR)
_marks: dict = {}   # path -> (mtime, size, mark)
_LOCAL = re.compile(r'''((?:src|href)=")(/[^"?#]+\.(?:js|css))(?:\?v=[^"]*)?(")''')


def mark(rel: str) -> str:
    """A short fingerprint of a file under the frontend folder ('' when it is missing)."""
    p = ROOT / rel.lstrip("/")
    try:
        st = p.stat()
    except OSError:
        return ""
    hit = _marks.get(p)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    m = hashlib.sha1(p.read_bytes()).hexdigest()[:10]
    _marks[p] = (st.st_mtime_ns, st.st_size, m)
    return m


def _import_map() -> str:
    imports = {}
    for p in sorted((ROOT / "js").rglob("*.js")):
        rel = "/" + p.relative_to(ROOT).as_posix()
        imports[rel] = f"{rel}?v={mark(rel)}"
    return '<script type="importmap">' + json.dumps({"imports": imports}, separators=(",", ":")) + "</script>"


def page(path: Path) -> HTMLResponse:
    """An HTML page of the frontend with its own scripts and styles stamped, and the import map when it loads modules."""
    html = path.read_text(encoding="utf-8")
    html = _LOCAL.sub(lambda m: f"{m[1]}{m[2]}?v={mark(m[2])}{m[3]}" if mark(m[2]) else m[0], html)
    if '<script type="module"' in html:
        i = html.index('<script type="module"')
        html = html[:i] + _import_map() + "\n  " + html[i:]
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
