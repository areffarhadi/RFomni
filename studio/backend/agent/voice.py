"""Voice for the assistant panel: the studio relays WebRTC signalling to the voice agent.

The voice agent (Pipecat, the same one the chat page uses) listens, checks the speaker,
transcribes and speaks. In its "studio" mode it hands each transcript to /api/agent/turn,
so voice and text share one brain and one plan. Only /start and the SDP/ICE exchange pass
through here; the audio itself goes peer to peer between the browser and the agent.
"""
import json
import os
import ssl
import urllib.error
import urllib.request

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import auth

VOICE_URL = os.environ.get("OMNI_VOICE_URL", "https://127.0.0.1:7912").rstrip("/")

router = APIRouter(prefix="/api/voice", tags=["voice"])

_SSL = ssl.create_default_context()
_SSL.check_hostname = False          # the agent uses the self-signed LAN certificate
_SSL.verify_mode = ssl.CERT_NONE


def _forward(method: str, path: str, body: bytes | None, timeout: float) -> tuple[int, bytes, str]:
    req = urllib.request.Request(f"{VOICE_URL}{path}", data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return r.status, r.read(), r.headers.get("content-type", "application/json")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("content-type", "text/plain")


@router.get("/status")
async def voice_status():
    try:
        code, _, _ = await run_in_threadpool(_forward, "GET", "/status", None, 4)
        return {"ready": code == 200, "upstream": VOICE_URL}
    except Exception as e:
        return {"ready": False, "upstream": VOICE_URL, "error": str(e)}


@router.post("/start")
@router.api_route("/sessions/{session_id}/api/offer", methods=["POST", "PATCH"])
async def voice_signal(request: Request, session_id: str | None = None):
    path = "/start" if session_id is None else f"/sessions/{session_id}/api/offer"
    body = await request.body()
    if session_id is None:
        # the agent sends this pass back with each spoken turn, so they act for the person who started it
        start = json.loads(body or b"{}")
        user = getattr(request.state, "user", None)
        start["body"] = {**(start.get("body") or {}), "mode": "studio",
                         "studio_auth": auth.mint_pass(user) if user and auth.ENABLED else ""}
        body = json.dumps(start).encode()
    try:
        code, content, ctype = await run_in_threadpool(_forward, request.method, path, body or None, 60)
    except Exception as e:
        return JSONResponse({"detail": f"Voice agent unreachable: {e}"}, status_code=502)
    return Response(content=content, status_code=code, media_type=ctype)
