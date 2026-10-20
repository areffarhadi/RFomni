"""Share links: give other people access to one project without giving them the app.

The owner app stays on 127.0.0.1 (only this computer). Shared links are served by a second,
separate listener (default port 8711, all network interfaces) that only answers requests carrying
a valid share token, and only for that project: viewing files, waveforms, labels and statistics,
plus saving labels when the link allows editing. A Cloudflare quick tunnel can put that second
listener on the internet; the owner app is never exposed.
"""
import atexit
import html
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config
from .util import new_id, now_iso, read_json, write_json

SHARES_FILE = config.WORKSPACE / "shares.json"
COOKIE = "omni_share"
ACCESS = {"view": "View only", "edit": "Can review and edit labels"}
_lock = threading.Lock()


# -----------------------------------------------------------------------------
# share records
# -----------------------------------------------------------------------------
def _load() -> list:
    return read_json(SHARES_FILE, {"shares": []}).get("shares", [])


def _save(items: list) -> None:
    write_json(SHARES_FILE, {"shares": items})


def _expired(s) -> bool:
    exp = s.get("expires_at")
    return bool(exp) and datetime.fromisoformat(exp) < datetime.now(timezone.utc)


def is_active(s) -> bool:
    return not s.get("revoked") and not _expired(s)


def public(s) -> dict:
    """Owner view of a share (includes the token, which is part of the link)."""
    return {**s, "active": is_active(s), "expired": _expired(s)}


def list_shares(pid: str | None = None) -> list:
    return [public(s) for s in _load() if pid is None or s["project_id"] == pid]


def _clean(body: dict, project: dict, cur: dict | None = None) -> dict:
    out = {}
    if "label" in body or cur is None:
        out["label"] = str(body.get("label") or "").strip()[:60] or "Shared link"
    if "access" in body or cur is None:
        acc = body.get("access", "view")
        if acc not in ACCESS:
            raise ValueError("access must be 'view' or 'edit'")
        out["access"] = acc
    if "tasks" in body or cur is None:
        tasks = [t for t in (body.get("tasks") or []) if t in project.get("tasks", [])]
        out["tasks"] = tasks  # empty = every tier of the project
    if "allow_download" in body or cur is None:
        out["allow_download"] = bool(body.get("allow_download", False))
    if "expires_days" in body:
        d = body.get("expires_days")
        out["expires_at"] = None if not d else (datetime.now(timezone.utc) + timedelta(days=max(1, min(365, int(d))))).isoformat(timespec="seconds")
    elif cur is None:
        out["expires_at"] = None
    if "note" in body or cur is None:
        out["note"] = str(body.get("note") or "").strip()[:300]
    if "revoked" in body:
        out["revoked"] = bool(body["revoked"])
    return out


def create_share(project: dict, body: dict) -> dict:
    s = {"id": new_id("sh", 4), "token": secrets.token_urlsafe(18), "project_id": project["id"],
         "created_at": now_iso(), "revoked": False, "views": 0, "last_seen": None, "edits": 0,
         **_clean(body, project)}
    with _lock:
        items = _load()
        items.append(s)
        _save(items)
    return public(s)


def update_share(sid: str, body: dict, project_lookup) -> dict:
    with _lock:
        items = _load()
        for i, s in enumerate(items):
            if s["id"] == sid:
                items[i] = {**s, **_clean(body, project_lookup(s["project_id"]), s)}
                _save(items)
                return public(items[i])
    raise KeyError(sid)


def delete_share(sid: str) -> None:
    with _lock:
        items = _load()
        rest = [s for s in items if s["id"] != sid]
        if len(rest) == len(items):
            raise KeyError(sid)
        _save(rest)


def delete_project_shares(pid: str) -> None:
    with _lock:
        _save([s for s in _load() if s["project_id"] != pid])


def find_valid(token: str | None) -> dict | None:
    if not token:
        return None
    for s in _load():
        if secrets.compare_digest(s["token"], token) and is_active(s):
            return s
    return None


def _bump(sid: str, **fields) -> None:
    with _lock:
        items = _load()
        for s in items:
            if s["id"] == sid:
                for k, v in fields.items():
                    s[k] = s.get(k, 0) + v if isinstance(v, int) else v
                _save(items)
                return


def touch(share: dict) -> None:
    _bump(share["id"], views=1, last_seen=now_iso())


def count_edits(share: dict, n: int) -> None:
    if n:
        _bump(share["id"], edits=n, last_seen=now_iso())


# -----------------------------------------------------------------------------
# what a shared link may do
# -----------------------------------------------------------------------------
_PROJECT = re.compile(r"^/api/projects/([^/]+)(/.*)?$")
_RULES = [  # (methods, path after /api/projects/<pid>, needs)
    ({"GET", "HEAD"}, r"", None),
    ({"GET", "HEAD"}, r"/stats(\.csv)?", None),
    ({"GET", "HEAD"}, r"/review", None),
    ({"GET", "HEAD"}, r"/media/[^/]+/(file|audio\.wav|peaks|spectrogram|frame|thumb|annotations)", None),
    ({"GET", "HEAD"}, r"/media/[^/]+/export", "download"),
    ({"GET", "HEAD"}, r"/export\.zip", "download"),
    ({"PUT"}, r"/media/[^/]+/annotations", "edit"),
    ({"PATCH"}, r"/media/[^/]+", "edit"),
]


def allowed(share: dict, method: str, path: str) -> bool:
    if path in ("/api/info", "/api/tasks"):
        return method in ("GET", "HEAD")
    m = _PROJECT.match(path)
    if not m or m.group(1) != share["project_id"]:
        return False
    rest = m.group(2) or ""
    for methods, pat, need in _RULES:
        if method in methods and re.fullmatch(pat, rest):
            if need == "edit":
                return share.get("access") == "edit"
            if need == "download":
                return bool(share.get("allow_download"))
            return True
    return False


def filter_doc(doc: dict, share: dict | None) -> dict:
    """Only the tiers a share includes (all when the share names no tasks)."""
    if not share or not share.get("tasks"):
        return doc
    keep = set(share["tasks"])
    tiers = [t for t in doc["tiers"] if (t.get("task_id") or t["id"]) in keep]
    ids = {t["id"] for t in tiers}
    return {**doc, "tiers": tiers, "segments": [s for s in doc["segments"] if s["tier_id"] in ids]}


def merge_shared_save(stored: dict, incoming: dict, share: dict) -> tuple[dict, int]:
    """A save through an edit link: tiers outside the share are kept as stored, and every segment
    the reviewer added or changed is stamped with the link's name (meta.edited_by)."""
    who = share.get("label") or "shared link"
    old = {s["id"]: s for s in stored["segments"]}
    keys = ("start", "end", "value", "reviewed", "geom")
    segs, n = [], 0
    for s in incoming.get("segments", []):
        prev = old.get(s.get("id"))
        if prev is None or any(prev.get(k) != s.get(k) for k in keys):
            s = {**s, "meta": {**(s.get("meta") or {}), "edited_by": who}}
            n += 1
        segs.append(s)
    if share.get("tasks"):
        keep = set(share["tasks"])
        hidden = [t for t in stored["tiers"] if (t.get("task_id") or t["id"]) not in keep]
        hid = {t["id"] for t in hidden}
        vis_ids = {t["id"] for t in incoming.get("tiers", [])}
        tiers = [t for t in incoming.get("tiers", []) if t["id"] not in hid] + [t for t in hidden if t["id"] not in vis_ids]
        segs = [s for s in segs if s.get("tier_id") not in hid] + [s for s in stored["segments"] if s["tier_id"] in hid]
    else:
        tiers = incoming.get("tiers", [])
    return {**incoming, "tiers": tiers, "segments": segs}, n


# -----------------------------------------------------------------------------
# the gate in front of the shared listener
# -----------------------------------------------------------------------------
def _page(title: str, text: str) -> bytes:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Omni Studio</title><style>
:root{{color-scheme:light dark;--bg:#0f1115;--fg:#e6e8ee;--mut:#9aa3b2;--acc:#2dd4bf}}
@media (prefers-color-scheme: light){{:root{{--bg:#f6f7f9;--fg:#14161b;--mut:#5b6472}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;padding:0 16px}}
div{{max-width:440px;text-align:center}} h1{{font-size:20px;margin:0 0 8px}} p{{color:var(--mut);margin:0}} b{{color:var(--acc)}}
</style></head><body><div><p><b>Omni Studio</b></p><h1>{html.escape(title)}</h1><p>{html.escape(text)}</p></div></body></html>""".encode()


async def _send(send, status: int, body: bytes, ctype: str, headers=()):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", ctype.encode()), (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store"), *headers]})
    await send({"type": "http.response.body", "body": body})


def _cookie(scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                name, _, val = part.strip().partition("=")
                if name == COOKIE:
                    return val
    return None


class ShareGate:
    """ASGI wrapper: only share-token requests reach the app, and only the allowed routes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        path, method = scope["path"], scope["method"]
        if path.startswith("/s/"):
            share = find_valid(path[3:].strip("/"))
            if not share:
                return await _send(send, 404, _page("This link does not work", "It was removed, has expired, or was copied incompletely. Ask the person who shared it for a new link."), "text/html; charset=utf-8")
            touch(share)
            cookie = f"{COOKIE}={share['token']}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
            return await _send(send, 303, b"", "text/plain", [(b"location", f"/#/p/{share['project_id']}".encode()),
                                                             (b"set-cookie", cookie.encode())])
        share = find_valid(_cookie(scope))
        if path.startswith("/api/"):
            if not share:
                return await _send(send, 401, json.dumps({"detail": "This shared link is no longer valid"}).encode(), "application/json")
            if not allowed(share, method, path):
                return await _send(send, 403, json.dumps({"detail": "Not available through a shared link"}).encode(), "application/json")
            scope.setdefault("state", {})["share"] = share
            return await self.app(scope, receive, send)
        if method in ("GET", "HEAD") and (path.startswith(("/css/", "/js/"))):
            return await self.app(scope, receive, send)
        if method in ("GET", "HEAD") and path in ("/", "/index.html"):
            if not share:
                return await _send(send, 401, _page("Open your shared link", "This address only works through a shared link (it ends in /s/…). Ask the owner of the project for one."), "text/html; charset=utf-8")
            return await self.app(scope, receive, send)
        return await _send(send, 404, b"Not found", "text/plain")


# -----------------------------------------------------------------------------
# the shared listener + optional internet tunnel
# -----------------------------------------------------------------------------
def lan_addresses() -> list:
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; picks the interface with the default route
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            out.append(ip)
    except OSError:
        pass
    host = socket.gethostname()
    if host and host.endswith(".local"):
        out.append(host)
    return out


class Tunnel:
    URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def __init__(self):
        self.proc = None
        self.url = None
        self.error = None

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("cloudflared"))

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, port: int, timeout: float = 30.0) -> str:
        if self.running() and self.url:
            return self.url
        exe = shutil.which("cloudflared")
        if not exe:
            raise RuntimeError("cloudflared is not installed (macOS: brew install cloudflared)")
        self.stop()
        self.url, self.error = None, None
        self.proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        log = []

        def read(proc=self.proc):
            for line in proc.stdout:
                log.append(line)
                m = self.URL_RE.search(line)
                if m and not self.url:
                    self.url = m.group(0)
        threading.Thread(target=read, daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < timeout and not self.url and self.running():
            time.sleep(0.2)
        if not self.url:
            self.stop()
            self.error = "".join(log[-5:]).strip() or "cloudflared did not report an address"
            raise RuntimeError(f"Could not open the internet link: {self.error[:300]}")
        return self.url

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.url = None


class ShareServer:
    def __init__(self):
        self.app = None
        self.server = None
        self.thread = None
        self.error = None
        self.host = os.environ.get("OMNI_SHARE_HOST", "0.0.0.0")
        self.port = int(os.environ.get("OMNI_SHARE_PORT", "8711"))
        self.tunnel = Tunnel()
        self._lock = threading.Lock()

    def running(self) -> bool:
        return bool(self.server and self.thread and self.thread.is_alive() and self.server.started)

    def start(self) -> None:
        import uvicorn
        with self._lock:
            if self.running():
                return
            self.error = None
            cfg = uvicorn.Config(ShareGate(self.app), host=self.host, port=self.port, log_level="warning",
                                 lifespan="off", access_log=False)
            self.server = uvicorn.Server(cfg)
            self.thread = threading.Thread(target=self.server.run, name="omni-share", daemon=True)
            self.thread.start()
            t0 = time.time()
            while time.time() - t0 < 4 and not self.server.started and self.thread.is_alive():
                time.sleep(0.05)
            if not self.server.started:
                self.error = f"Could not open port {self.port} for shared links (is another program using it?)"
                self.server.should_exit = True
                raise RuntimeError(self.error)

    def stop(self) -> None:
        with self._lock:
            self.tunnel.stop()
            if self.server:
                self.server.should_exit = True
            if self.thread:
                self.thread.join(5)
            self.server = self.thread = None

    def sync(self) -> None:
        """Run the shared listener while at least one link is active."""
        if any(is_active(s) for s in _load()):
            try:
                self.start()
            except RuntimeError:
                pass
        elif self.running():
            self.stop()

    def status(self) -> dict:
        local = self.host in ("127.0.0.1", "localhost")
        bases = [f"http://127.0.0.1:{self.port}"] if local else [f"http://{a}:{self.port}" for a in lan_addresses()]
        return {"running": self.running(), "port": self.port, "lan": not local, "bases": bases,
                "local_base": f"http://127.0.0.1:{self.port}", "error": self.error,
                "tunnel": {"available": Tunnel.available(), "running": self.tunnel.running(), "url": self.tunnel.url}}


server = ShareServer()
atexit.register(server.tunnel.stop)
