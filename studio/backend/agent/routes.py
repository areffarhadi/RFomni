"""HTTP API of the assistant. The studio panel, the chat page and the voice agent all use it.

The user is the signed-in one (backend/auth.py: the chat session cookie, the chat's signed
header through the proxy, or the voice agent's pass); each user has one plan. Voice approvals come from the voice agent with speaker_ok set by its speaker check.
"""
import json
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import brain
from . import drafter
from . import plan as P

router = APIRouter(prefix="/api/agent", tags=["agent"])


# With sign-in off (OMNI_AUTH=off) every caller is this one user, so all pages share one plan.
DEFAULT_USER = os.environ.get("OMNI_AGENT_USER", "local")


_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_ui = {"at": 0.0, "v": ""}


def ui_version() -> str:
    """Newest change to the studio's scripts and styles: an open page with an older one should reload."""
    if time.time() - _ui["at"] > 3:
        files = [*_FRONTEND.glob("js/**/*.js"), *_FRONTEND.glob("css/*.css")]
        _ui.update(at=time.time(), v=str(int(max((f.stat().st_mtime for f in files), default=0))))
    return _ui["v"]


def _v(out: dict) -> dict:
    return {**out, "ui_version": ui_version()}


def _user(request: Request, body: dict | None = None) -> str:
    """The signed-in user (set by the studio's sign-in check), so the studio, the chat and voice share a plan."""
    user = getattr(request.state, "user", None)
    return user["id"] if user else DEFAULT_USER


# The page each user has open in the studio ({"hash", "parts"}), sent by the panel. Spoken turns come
# from the voice agent, which cannot see the page, so they use the last one the panel reported.
PAGES: dict = {}


def _page(user: str, page) -> dict | None:
    if isinstance(page, dict) and isinstance(page.get("hash"), str):
        parts = [str(x)[:40] for x in page.get("parts") or []][:40]
        PAGES[user] = {"hash": page["hash"][:300], "parts": parts}
    return PAGES.get(user)


@router.post("/page")
def agent_page(request: Request, body: dict = Body(...)):
    _page(_user(request), body)
    return {"ok": True}


@router.post("/draft_task")
async def draft_task(body: dict = Body(...)):
    """The "New prompt task" form: write a task from a description, or improve the one being edited."""
    draft = body.get("draft") if isinstance(body.get("draft"), dict) else None
    try:
        return await run_in_threadpool(drafter.write, str(body.get("request") or ""), draft, "")
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)


@router.post("/turn")
async def agent_turn(request: Request, body: dict = Body(...)):
    user = _user(request, body)
    return _v(await run_in_threadpool(brain.turn, user, str(body.get("text", "")), body.get("via", "text"),
                                      body.get("speaker_ok"), _page(user, body.get("page"))))


@router.post("/approve")
async def agent_approve(request: Request, body: dict = Body(...)):
    return _v(await run_in_threadpool(brain.approve, _user(request, body), str(body.get("proposal_id", "")),
                                      body.get("via", "click"), body.get("speaker_ok")))


@router.post("/reject")
def agent_reject(request: Request, body: dict = Body(...)):
    return _v(brain.reject(_user(request, body), str(body.get("proposal_id", ""))))


@router.post("/stop")
def agent_stop(request: Request, body: dict = Body(default={})):
    return _v(brain.stop(_user(request, body)))


@router.get("/plan")
def agent_plan(request: Request):
    plan = P.load(_user(request))
    return _v({**P.public(plan), "history": plan["history"]})


@router.delete("/plan")
def agent_reset(request: Request):
    return P.public(P.reset(_user(request)))


# ----------------------------------------------------------------------------- chat page
# OpenAI-style endpoint, so the chat page (Open WebUI, through its proxy on 8401) can offer the
# assistant as a model. Only the newest user message is used; the plan holds the conversation.
MODEL_ID = "omni-studio"


def _last_user_text(messages: list) -> str:
    for m in reversed(messages or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
        return str(c or "").strip()
    return ""


def _as_chat_text(r: dict) -> str:
    text = r.get("say", "")
    pr = r.get("proposal")
    if pr:
        text += (f"\n\n**Needs your approval:** {pr['summary']}\n\n"
                 "Reply **yes** to approve or **no** to cancel, or use the buttons in the Omni Studio panel.")
    return text


@router.get("/v1/models")
def oa_models():
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "omni-studio",
                                        "name": "Omni Studio Assistant"}]}


@router.post("/v1/chat/completions")
async def oa_chat(request: Request, body: dict = Body(...)):
    text = _last_user_text(body.get("messages"))
    user = _user(request)  # from the chat page: the studio page this user last had open still counts
    r = await run_in_threadpool(brain.turn, user, text, "text", None, PAGES.get(user))
    content, cid, now = _as_chat_text(r), f"chatcmpl-{uuid.uuid4().hex[:24]}", int(time.time())
    if not body.get("stream"):
        return {"id": cid, "object": "chat.completion", "created": now, "model": MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}

    def chunk(delta, finish=None):
        return "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "created": now, "model": MODEL_ID,
                                      "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"
    sse = chunk({"role": "assistant", "content": content}) + chunk({}, "stop") + "data: [DONE]\n\n"
    return StreamingResponse(iter([sse]), media_type="text/event-stream")
