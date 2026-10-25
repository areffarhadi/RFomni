"""HTTP API of data collection (backend/collect.py).

/api/collect/...   the owner's side: create, design, link, recordings (listen, accept, reject), export
/api/c/<token>/..  the contributors' side (no account): the project, start or resume a session, the next
                   sentence and condition, upload a recording, skip
/c/<token>         the recording page (frontend/rec/); it needs https (or localhost) for the microphone
/api/public/collect  the public Data collection page (frontend/collect/)
"""
import json

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from . import collect as C
from . import config
from .util import slugify

router = APIRouter()
PAGE = config.FRONTEND_DIR / "rec" / "index.html"


def _user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Please sign in")
    return user


def _own(request: Request, cid: str) -> dict:
    user = _user(request)
    try:
        p = C.get(cid)
    except C.NotFound:
        raise HTTPException(404, "This data collection does not exist")
    if p["owner"] != user["id"] and user.get("role") != "admin":
        raise HTTPException(403, "This data collection belongs to someone else")
    return p


def _full(request: Request, p: dict) -> dict:
    return {**p, "summary": C.summary(p), "link": f"{str(request.base_url).rstrip('/')}/c/{p['token']}"}


# ----------------------------------------------------------------------------- owner
@router.get("/api/collect")
def list_collect(request: Request):
    return C.list_for(_user(request))


@router.post("/api/collect")
def create_collect(request: Request, body: dict = Body(default={})):
    return _full(request, C.create(_user(request), str(body.get("title") or "")))


@router.get("/api/collect/{cid}")
def get_collect(cid: str, request: Request):
    return _full(request, _own(request, cid))


@router.patch("/api/collect/{cid}")
def patch_collect(cid: str, request: Request, body: dict = Body(...)):
    _own(request, cid)
    return _full(request, C.update(cid, {k: body[k] for k in ("design", "status", "listed", "new_link") if k in body}))


@router.delete("/api/collect/{cid}")
def delete_collect(cid: str, request: Request):
    _own(request, cid)
    C.delete(cid)
    return {"ok": True}


@router.get("/api/collect/{cid}/overview")
def collect_overview(cid: str, request: Request):
    return C.overview(_own(request, cid))


@router.get("/api/collect/{cid}/recordings")
def collect_recordings(cid: str, request: Request, lang: str = "", condition: str = "", status: str = "", limit: int = 200):
    p = _own(request, cid)
    recs = [r for r in C.recordings(cid) if (not lang or r["lang"] == lang) and (not condition or r["condition"] == condition)
            and (not status or r.get("status", "new") == status)]
    recs.sort(key=lambda r: r["at"], reverse=True)
    return {"total": len(recs), "recordings": recs[:max(1, min(limit, 2000))]}


@router.patch("/api/collect/{cid}/recordings/{rid}")
def review_recording(cid: str, rid: str, request: Request, body: dict = Body(...)):
    p = _own(request, cid)
    try:
        return C.set_status(p, rid, str(body.get("status") or ""))
    except C.NotFound:
        raise HTTPException(404, "No such recording")


@router.get("/api/collect/{cid}/recordings/{rid}/audio")
def recording_audio(cid: str, rid: str, request: Request):
    p = _own(request, cid)
    try:
        return FileResponse(C.audio_path(p, C.find(p, rid)), media_type="audio/wav")
    except C.NotFound:
        raise HTTPException(404, "No such recording")


@router.get("/api/collect/{cid}/export.zip")
def collect_export(cid: str, request: Request, accepted: bool = False):
    p = _own(request, cid)
    data = C.export_zip(p, accepted)
    name = slugify(p["design"]["title"], 40) or cid
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}{"-accepted" if accepted else ""}.zip"'})


# ----------------------------------------------------------------------------- contributors
def _public(request: Request, token: str, preview: bool) -> tuple[dict, bool]:
    p = C.by_token(token)
    if not p:
        raise HTTPException(404, "This link does not work. Ask the person who shared it for a new one.")
    user = getattr(request.state, "user", None)
    if preview and user and (user["id"] == p["owner"] or user.get("role") == "admin"):
        return p, True
    if p["status"] != "live":
        msg = {"draft": "This project is not open yet.", "paused": "This project is paused for now. Try again later.",
               "closed": "This project is closed. Thank you for your interest!"}[p["status"]]
        raise HTTPException(403, msg)
    return p, False


def _sid(v) -> str:
    return str(v or "").strip().upper()


@router.get("/api/public/collect")
def public_collect():
    return {"projects": C.public_list()}


@router.get("/c/{token}")
def rec_page(token: str):
    return FileResponse(PAGE, headers={"Cache-Control": "no-cache"})


@router.get("/api/c/{token}")
def public_project(token: str, request: Request, preview: bool = False):
    p, prev = _public(request, token, preview)
    d = p["design"]
    return {"design": d, "preview": prev, "info_fields": {k: C.INFO_FIELDS[k] for k in d["settings"]["info"]},
            "choices": {"gender": C.GENDERS, "age": C.AGE_RANGES}}


@router.post("/api/c/{token}/session")
def public_session(token: str, request: Request, body: dict = Body(default={}), preview: bool = False):
    p, prev = _public(request, token, preview)
    if prev:
        return {"session": {"id": "PREVIEW"}}
    if body.get("resume"):
        s = C.session(p, _sid(body["resume"]))
        if not s:
            raise HTTPException(404, "No session with this ID. Check it, or start a new one.")
    else:
        if not body.get("consent"):
            raise HTTPException(400, "Please agree to the consent first")
        s = C.start_session(p, body.get("info") or {})
    return {"session": {"id": s["id"]}}


@router.get("/api/c/{token}/next")
def public_next(token: str, request: Request, lang: str, session: str = "", step: int = 0, preview: bool = False):
    p, prev = _public(request, token, preview)
    try:
        if prev:  # the owner's preview walks the pairs in order without saving anything
            pairs = C._pairs(p, lang, "PREVIEW")
            return {"pair": pairs[step] if step < len(pairs) else None, "done": min(step, len(pairs)), "total": len(pairs)}
        return C.next_pair(p, _sid(session), lang)
    except C.NotFound:
        raise HTTPException(404, "This session or language was not found. Start again.")


@router.post("/api/c/{token}/record")
async def public_record(token: str, request: Request, file: UploadFile = File(...), meta: str = Form("{}"), preview: bool = False):
    p, prev = _public(request, token, preview)
    m = json.loads(meta or "{}")
    data = await file.read()
    if prev:
        return {"ok": True, "preview": True}
    rec = await run_in_threadpool(C.save_recording, p, _sid(m.get("session")), str(m.get("lang") or ""),
                                  str(m.get("prompt") or ""), str(m.get("condition") or ""), data, m)
    return {"ok": True, "seconds": rec["seconds"]}


@router.post("/api/c/{token}/skip")
def public_skip(token: str, request: Request, body: dict = Body(...), preview: bool = False):
    p, prev = _public(request, token, preview)
    if not prev:
        C.skip(p, _sid(body.get("session")), str(body.get("lang") or ""), str(body.get("prompt") or ""), str(body.get("condition") or ""))
    return {"ok": True}
