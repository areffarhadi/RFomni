#!/usr/bin/env python3
"""
Name-only gate in front of Open WebUI.

A colleague on the LAN opens the gate, types their name once, and lands in the
workspace with their own persistent chat history -- no password, no login form.
The gate is the only thing listening on the LAN; Open WebUI itself binds to
localhost and trusts the identity header this proxy injects
(WEBUI_AUTH_TRUSTED_EMAIL_HEADER), which is why the two must be started together.

    GATE_PORT=8443 GATE_UPSTREAM=http://127.0.0.1:8080 python owui_gate.py

The name is slugged into a stable address (John Cena -> john-cena@lan.local),
so the same name always reopens the same history -- that is what makes the
personal link on the confirmation page worth bookmarking.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import ssl
import sys
import time
import unicodedata
from html import escape
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, TCPConnector, WSMsgType, web

UPSTREAM = os.environ.get("GATE_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
PORT = int(os.environ.get("GATE_PORT", "8443"))
HOST = os.environ.get("GATE_HOST", "0.0.0.0")
EMAIL_DOMAIN = os.environ.get("GATE_EMAIL_DOMAIN", "lan.local")
COOKIE = "owui_gate"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
TITLE = os.environ.get("GATE_TITLE", "RFomni Playground")

# Names that map onto an account that already exists, so the owner keeps their
# admin rights and their old chats instead of being handed a fresh identity.
# Format: "slug=email:role,slug=email:role"
_DEFAULT_RESERVED = "aref=areffarhadi@gmail.com:admin"
RESERVED: dict[str, tuple[str, str]] = {}
for _item in os.environ.get("GATE_RESERVED", _DEFAULT_RESERVED).split(","):
    _item = _item.strip()
    if not _item or "=" not in _item:
        continue
    _slug, _, _rest = _item.partition("=")
    _email, _, _role = _rest.partition(":")
    RESERVED[_slug.strip().lower()] = (_email.strip().lower(), (_role or "user").strip())

# Headers the gate asserts. Anything a client sends under these names is dropped
# before the request is forwarded -- otherwise the header would be a free pass.
H_EMAIL = "X-Auth-Email"
H_NAME = "X-Auth-Name"
H_ROLE = "X-Auth-Role"

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}
STRIP_FROM_CLIENT = HOP_BY_HOP | {h.lower() for h in (H_EMAIL, H_NAME, H_ROLE)} | {"host"}


def _secret() -> bytes:
    env = os.environ.get("GATE_SECRET")
    if env:
        return env.encode()
    path = Path(os.environ.get("GATE_SECRET_FILE", Path.home() / ".owui_gate_secret"))
    if not path.exists():
        path.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
        path.chmod(0o600)
    return path.read_bytes()


SECRET = _secret()


def slugify(name: str) -> str:
    norm = unicodedata.normalize("NFKD", name)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return re.sub(r"-{2,}", "-", slug)


def _pad(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=")
    mac = hmac.new(SECRET, raw, hashlib.sha256).digest()
    return raw.decode() + "." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def unsign(token: str) -> dict | None:
    try:
        raw, _, mac = token.partition(".")
        if not raw or not mac:
            return None
        expect = hmac.new(SECRET, raw.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(base64.urlsafe_b64decode(_pad(mac)), expect):
            return None
        data = json.loads(base64.urlsafe_b64decode(_pad(raw)))
        if not isinstance(data, dict) or "e" not in data or "n" not in data:
            return None
        return data
    except Exception:
        return None


def identity_for(name: str) -> dict:
    slug = slugify(name)
    if slug in RESERVED:
        email, role = RESERVED[slug]
    else:
        email, role = f"{slug}@{EMAIL_DOMAIN}", "user"
    return {"n": name.strip()[:60], "e": email, "r": role, "t": int(time.time())}


# ---------------------------------------------------------------- the name page

PAGE_CSS = """
:root{color-scheme:dark;--bg:#0d1317;--card:#141d22;--ink:#e6eef0;--soft:#a3b4ba;
--faint:#77878d;--rule:#26343a;--accent:#3db8c5}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);
color:var(--ink);font:400 16px/1.6 ui-sans-serif,system-ui,-apple-system,sans-serif;
padding-block:48px;padding-inline:20px}
.card{width:100%;max-width:440px;background:var(--card);border:1px solid var(--rule);
border-radius:6px;padding:32px 28px}
.eyebrow{font:500 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.14em;
text-transform:uppercase;color:var(--accent);margin-bottom:14px}
h1{font-size:23px;line-height:1.2;margin:0 0 8px;text-wrap:balance}
p{color:var(--soft);margin:0 0 22px;font-size:14.5px}
label{display:block;font:500 11px/1 ui-monospace,Menlo,monospace;letter-spacing:.09em;
text-transform:uppercase;color:var(--faint);margin-bottom:8px}
input{width:100%;padding:11px 13px;font:400 16px/1.4 inherit;color:var(--ink);
background:var(--bg);border:1px solid var(--rule);border-radius:4px}
input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
button{margin-top:16px;width:100%;padding:11px 14px;font:500 14px/1 inherit;
color:#07252a;background:var(--accent);border:0;border-radius:4px;cursor:pointer}
button:hover{filter:brightness(1.08)}
button:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.err{color:#e0796a;font-size:13.5px;margin:0 0 16px}
.link{margin-top:24px;padding-top:18px;border-top:1px solid var(--rule)}
.link code{display:block;word-break:break-all;font:400 12.5px/1.5 ui-monospace,Menlo,monospace;
color:var(--soft);background:var(--bg);border:1px solid var(--rule);border-radius:4px;
padding:9px 11px;margin-top:8px}
.who{font-size:14.5px;color:var(--soft);margin:0 0 6px}
.who b{color:var(--ink)}
a.ghost{display:block;text-align:center;margin-top:14px;color:var(--faint);font-size:13px}
"""


def page(body: str, title: str, status: int = 200) -> web.Response:
    html = (
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{PAGE_CSS}</style></head>"
        f"<body><main class=card>{body}</main></body></html>"
    )
    return web.Response(text=html, content_type="text/html", status=status)


def name_form(error: str = "", nxt: str = "/") -> web.Response:
    err = f"<p class=err>{escape(error)}</p>" if error else ""
    return page(
        f"<div class=eyebrow>{escape(TITLE)}</div>"
        f"<h1>What should we call you?</h1>"
        f"<p>Your name is all we need. It keeps your chats separate from everyone "
        f"else's, and brings them back the next time you open this page.</p>"
        f"{err}"
        f"<form method=post action='/__gate/enter'>"
        f"<input type=hidden name=next value='{escape(nxt)}'>"
        f"<label for=gate-name>Your name</label>"
        f"<input id=gate-name name=name autocomplete=name autofocus required "
        f"maxlength=60 placeholder='John Cena'>"
        f"<button type=submit>Open the workspace</button></form>",
        f"Sign in - {TITLE}",
    )


def welcome(ident: dict, base: str, nxt: str) -> web.Response:
    link = f"{base}/?u={sign(ident)}"
    return page(
        f"<div class=eyebrow>{escape(TITLE)}</div>"
        f"<h1>You're in, {escape(ident['n'])}.</h1>"
        f"<p class=who>This browser will remember you. Your chats are saved under "
        f"<b>{escape(ident['e'])}</b>.</p>"
        f"<form method=get action='{escape(nxt)}'><button type=submit>Continue</button></form>"
        f"<div class=link><label for=gate-link>Your personal link - open it on a phone or "
        f"another laptop to pick up the same history</label>"
        f"<code id=gate-link>{escape(link)}</code></div>"
        f"<a class=ghost href='/__gate/leave'>Not you? Use a different name</a>",
        f"Welcome - {TITLE}",
    )


# ------------------------------------------------------------------- handlers


def _set_identity(resp: web.StreamResponse, request: web.Request, ident: dict) -> None:
    resp.set_cookie(
        COOKIE,
        sign(ident),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=request.scheme == "https",
        path="/",
    )


def _safe_next(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


async def gate_enter(request: web.Request) -> web.StreamResponse:
    form = await request.post()
    name = str(form.get("name", "")).strip()
    nxt = _safe_next(str(form.get("next", "/")) or "/")
    if len(name) < 2:
        return name_form("Please enter at least two characters.", nxt)
    if not slugify(name):
        return name_form("That name has no letters or digits in it - try another.", nxt)

    ident = identity_for(name)
    resp = welcome(ident, f"{request.scheme}://{request.host}", nxt)
    _set_identity(resp, request, ident)
    return resp


async def gate_leave(request: web.Request) -> web.StreamResponse:
    resp = name_form()
    resp.del_cookie(COOKIE, path="/")
    return resp


def identity_of(request: web.Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    return unsign(token) if token else None


def upstream_headers(request: web.Request, ident: dict) -> dict:
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in STRIP_FROM_CLIENT
    }
    headers[H_EMAIL] = ident["e"]
    headers[H_NAME] = ident["n"]
    headers[H_ROLE] = ident.get("r", "user")
    headers["X-Forwarded-Proto"] = request.scheme
    headers["X-Forwarded-Host"] = request.host
    if request.remote:
        headers["X-Forwarded-For"] = request.remote
    return headers


async def proxy_ws(request: web.Request, ident: dict) -> web.StreamResponse:
    client_ws = web.WebSocketResponse(autoping=False, max_msg_size=0)
    await client_ws.prepare(request)

    session: ClientSession = request.app["session"]
    headers = {
        k: v
        for k, v in upstream_headers(request, ident).items()
        if not k.lower().startswith("sec-websocket") and k.lower() != "connection"
    }

    async def pump(src, dst):
        async for msg in src:
            if msg.type == WSMsgType.TEXT:
                await dst.send_str(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await dst.send_bytes(msg.data)
            elif msg.type == WSMsgType.PING:
                await dst.ping(msg.data)
            elif msg.type == WSMsgType.PONG:
                await dst.pong(msg.data)
            else:
                break
        if not dst.closed:
            await dst.close()

    async with session.ws_connect(
        UPSTREAM + request.rel_url.path_qs,
        headers=headers,
        autoping=False,
        max_msg_size=0,
    ) as up_ws:
        await asyncio.gather(pump(client_ws, up_ws), pump(up_ws, client_ws))
    return client_ws


async def proxy(request: web.Request) -> web.StreamResponse:
    path = request.rel_url.path

    if path == "/__gate/enter" and request.method == "POST":
        return await gate_enter(request)
    if path == "/__gate/leave":
        return await gate_leave(request)

    # A personal link hands over identity, then drops the token from the address
    # bar so it does not end up in every screenshot and copied URL.
    handed = request.query.get("u")
    if handed:
        ident = unsign(handed)
        if ident:
            clean = request.rel_url.with_query(
                {k: v for k, v in request.query.items() if k != "u"}
            )
            resp = web.HTTPFound(str(clean))
            _set_identity(resp, request, ident)
            return resp

    ident = identity_of(request)
    if ident is None:
        if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
            return name_form(nxt=request.rel_url.path_qs)
        return web.Response(status=401, text="Not signed in")

    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await proxy_ws(request, ident)

    session: ClientSession = request.app["session"]
    body = request.content if request.can_read_body else None
    async with session.request(
        request.method,
        UPSTREAM + request.rel_url.path_qs,
        headers=upstream_headers(request, ident),
        data=body,
        allow_redirects=False,
    ) as up:
        resp = web.StreamResponse(status=up.status, reason=up.reason)
        for k, v in up.headers.items():
            if k.lower() in HOP_BY_HOP or k.lower() == "content-length":
                continue
            resp.headers.add(k, v)
        await resp.prepare(request)
        async for chunk in up.content.iter_chunked(65536):
            await resp.write(chunk)
        await resp.write_eof()
        return resp


async def make_app() -> web.Application:
    app = web.Application(client_max_size=1024**3)
    app["session"] = ClientSession(
        timeout=ClientTimeout(total=None, sock_connect=15, sock_read=None),
        connector=TCPConnector(limit=0),
        auto_decompress=False,
    )
    app.router.add_route("*", "/{tail:.*}", proxy)

    async def close(_app):
        await _app["session"].close()

    app.on_cleanup.append(close)
    return app


def main() -> None:
    cert = os.environ.get("GATE_CERTFILE")
    key = os.environ.get("GATE_KEYFILE")
    ctx = None
    if cert and key:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(cert, key)
    scheme = "https" if ctx else "http"
    print(f"[gate] {scheme}://{HOST}:{PORT} -> {UPSTREAM}", flush=True)
    print(f"[gate] identity header {H_EMAIL}; reserved: {sorted(RESERVED)}", flush=True)
    if "127.0.0.1" not in UPSTREAM and "localhost" not in UPSTREAM:
        print("[gate] WARNING: upstream is not localhost - bind Open WebUI to 127.0.0.1 "
              "or the identity header can be forged from the LAN", flush=True)
    web.run_app(make_app(), host=HOST, port=PORT, ssl_context=ctx, print=None)


if __name__ == "__main__":
    sys.exit(main())
