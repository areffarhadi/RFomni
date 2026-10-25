"""Sign-in for Omni Studio, shared with the chat page (Open WebUI).

The chat page owns the accounts. Signing in here posts to the chat's own sign-in and stores its
session token in the `token` cookie, the same cookie the chat page uses. A cookie belongs to a
host, not a port, so with both apps on one address, signing in to either signs you in to both.

Who a request comes from, in order:
  - the `token` cookie or an `Authorization: Bearer` header: a chat session, checked with the chat;
  - `X-OpenWebUI-User-Jwt`: the chat page talking to the `omni-studio` model through the proxy,
    signed with the secret the chat and the studio share (OMNI_FORWARD_JWT_SECRET);
  - `X-Omni-Auth`: the voice agent, carrying a short pass the studio signed when the voice
    session started, so a spoken turn acts for the person who opened the session.

OMNI_AUTH=off turns all of this off (one local user, as before).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import ssl
import time
import urllib.error
import urllib.request

from . import config

ENABLED = os.environ.get("OMNI_AUTH", "on").lower() not in ("0", "off", "false", "no")
CHAT_URL = os.environ.get("OMNI_CHAT_URL", "https://127.0.0.1:8080").rstrip("/")
FORWARD_SECRET = os.environ.get("OMNI_FORWARD_JWT_SECRET", "").strip()
COOKIE = "token"

_SSL = ssl.create_default_context()
_SSL.check_hostname = False          # the chat page uses the self-signed LAN certificate
_SSL.verify_mode = ssl.CERT_NONE


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _chat(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{CHAT_URL}{path}", method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("detail") or e.reason
        except Exception:
            detail = e.reason
        raise AuthError(e.code, str(detail))
    except OSError as e:
        raise AuthError(503, f"The sign-in service (chat page) is not reachable: {e}")


def _user(d: dict) -> dict:
    return {"id": d["id"], "email": d.get("email", ""), "name": d.get("name", ""), "role": d.get("role", "user")}


# ----------------------------------------------------------------------------- chat sessions
_seen: dict = {}  # sha256(token) -> (checked at, user); a session is re-checked with the chat every minute


def session_user(token: str) -> dict | None:
    key = hashlib.sha256(token.encode()).hexdigest()
    hit = _seen.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        user = _user(_chat("GET", "/api/v1/auths/", token=token))
    except AuthError as e:
        if e.status in (401, 403):
            _seen.pop(key, None)
            return None
        if hit:                 # the chat is restarting: keep a session we already knew a little longer
            return hit[1]
        raise
    if len(_seen) > 500:
        _seen.clear()
    _seen[key] = (time.time(), user)
    return user


def sign_in(email: str, password: str) -> dict:
    return _chat("POST", "/api/v1/auths/signin", {"email": email, "password": password})


def sign_up(name: str, email: str, password: str) -> dict:
    return _chat("POST", "/api/v1/auths/signup", {"name": name, "email": email, "password": password})


# ----------------------------------------------------------------------------- signed passes
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret() -> bytes:
    f = config.WORKSPACE / ".auth_secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return f.read_text().strip().encode()


def mint_pass(user: dict, ttl: int = 12 * 3600) -> str:
    """A pass the voice agent sends back with each spoken turn of a session the user opened."""
    body = _b64(json.dumps({**user, "exp": int(time.time()) + ttl}).encode())
    return f"{body}.{_b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())}"


def _check_pass(tok: str) -> dict | None:
    body, _, sig = tok.partition(".")
    if not hmac.compare_digest(_b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest()), sig):
        return None
    d = json.loads(_unb64(body))
    return _user(d) if d.get("exp", 0) > time.time() else None


def _check_forward_jwt(tok: str) -> dict | None:
    """HS256 token from the chat page (FORWARD_USER_INFO_HEADER_JWT_SECRET on its side)."""
    if not FORWARD_SECRET:
        return None
    try:
        head, body, sig = tok.split(".")
        want = hmac.new(FORWARD_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
        if json.loads(_unb64(head)).get("alg") != "HS256" or not hmac.compare_digest(want, _unb64(sig)):
            return None
        d = json.loads(_unb64(body))
    except ValueError:
        return None
    if d.get("exp", 0) < time.time():
        return None
    return {"id": d["sub"], "email": d.get("email", ""), "name": d.get("name", ""), "role": d.get("role", "user")}


# ----------------------------------------------------------------------------- one request
def cookie_token(headers) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        name, _, val = part.strip().partition("=")
        if name == COOKIE and val:
            return val
    return None


def request_user(headers) -> dict | None:
    """The signed-in user of a request (headers: any case-insensitive mapping), or None."""
    if tok := headers.get("x-omni-auth"):
        return _check_pass(tok)
    if tok := headers.get("x-openwebui-user-jwt"):
        return _check_forward_jwt(tok)
    auth = headers.get("authorization", "")
    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else cookie_token(headers)
    return session_user(tok) if tok else None
