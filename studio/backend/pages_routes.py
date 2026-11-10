"""HTTP API of the default pages (backend/pages/), requests to the administrators, and uploads in pieces.

/api/pages/...          the catalogue, picking a default for a description, making a page from it, and then per page
                        (kind shared = Task sharing and Subjective tests, collect = Data collection): chat, undo,
                        settings by hand, approve
/api/page-requests/...  requests to the administrators (answered within 12 hours)
/api/uploads/...        big files in pieces (Cloudflare refuses bodies over 100 MB), then unpacked into a task
Everything needs a signed-in user; a page belongs to its task's or project's owner (or an administrator).
"""
from fastapi import APIRouter, Body, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import collect_routes as CR
from . import shared_routes as SR
from . import uploads as UP
from .pages import agent
from .pages import catalogue as C
from .pages import requests as RQ
from .pages import service as SV
from .pages import settings as S

router = APIRouter()


def _user(request: Request) -> dict:
    return SR._user(request)


def _own(request: Request, kind: str, oid: str) -> dict:
    if kind == "shared":
        return SR._own(request, oid)
    if kind == "collect":
        return CR._own(request, oid)
    raise HTTPException(404, "No such kind of page")


def _out(request: Request, kind: str, oid: str, **extra) -> dict:
    """The page's state, its chat and its owner's requests, and the task or project as its builder shows it."""
    obj = (SR.ST.get(oid) if kind == "shared" else CR.C.get(oid))
    full = SR._full(request, obj) if kind == "shared" else CR._full(request, obj)
    return {"page": SV.state(obj) if obj.get("page") else None, "chat": obj.get("chat") or [],
            "requests": RQ.for_ref(kind, oid), "obj": full, **extra}


def _bad(e: Exception):
    raise HTTPException(400, str(e))


# ----------------------------------------------------------------------------- catalogue and picking
@router.get("/api/pages/catalogue")
def catalogue(request: Request, area: str = ""):
    _user(request)
    ts = [t for t in C._all().values() if not area or t["area"] == area]
    return {"areas": C.AREAS, "modalities": C.MODALITIES, "templates": [C.public(t) for t in ts], "online": agent.online()}


@router.post("/api/pages/pick")
async def pick(request: Request, body: dict = Body(...)):
    _user(request)
    area, modality, text = str(body.get("area") or ""), str(body.get("modality") or ""), str(body.get("text") or "").strip()
    if area not in C.AREAS or modality not in C.MODALITIES:
        raise HTTPException(400, "Choose the area and the kind of data")
    if len(text) < 8:
        raise HTTPException(400, "Describe the task in a sentence or two")
    try:
        out = await run_in_threadpool(agent.pick, area, modality, text)
    except agent.Offline as e:
        raise HTTPException(503, str(e))
    return {**out, "template": C.public(C.get(out["page"])) if out["page"] else None,
            "options": [C.public(t) for t in C.find(area, modality)]}


@router.post("/api/pages")
async def create(request: Request, body: dict = Body(...)):
    user = _user(request)
    try:
        out = await run_in_threadpool(SV.create, user, str(body.get("area") or ""), str(body.get("modality") or ""),
                                      str(body.get("template") or ""), str(body.get("text") or ""))
    except (KeyError, ValueError) as e:
        _bad(e)
    return out


# ----------------------------------------------------------------------------- one page
@router.get("/api/pages/{kind}/{oid}")
def page(kind: str, oid: str, request: Request):
    _own(request, kind, oid)
    return _out(request, kind, oid, online=agent.online())


@router.post("/api/pages/{kind}/{oid}/chat")
async def chat(kind: str, oid: str, request: Request, body: dict = Body(...)):
    _own(request, kind, oid)
    try:
        out = await run_in_threadpool(SV.chat, kind, oid, str(body.get("message") or ""))
    except agent.Offline as e:
        raise HTTPException(503, str(e))
    except (SV.NotPage, ValueError) as e:
        _bad(e)
    return _out(request, kind, oid, result=out)


@router.post("/api/pages/{kind}/{oid}/undo")
def undo(kind: str, oid: str, request: Request):
    _own(request, kind, oid)
    try:
        SV.undo(kind, oid)
    except (SV.NotPage, ValueError) as e:
        _bad(e)
    return _out(request, kind, oid)


@router.patch("/api/pages/{kind}/{oid}/settings")
def settings(kind: str, oid: str, request: Request, body: dict = Body(...)):
    _own(request, kind, oid)
    try:
        SV.set_settings(kind, oid, body.get("settings") or {})
    except (SV.NotPage, S.Bad, ValueError) as e:
        _bad(e)
    return _out(request, kind, oid)


@router.post("/api/pages/{kind}/{oid}/save")
def save_point(kind: str, oid: str, request: Request, body: dict = Body(default={})):
    _own(request, kind, oid)
    try:
        SV.save_point(kind, oid, str(body.get("name") or ""))
    except (SV.NotPage, ValueError) as e:
        _bad(e)
    return _out(request, kind, oid)


@router.post("/api/pages/{kind}/{oid}/restore")
def restore(kind: str, oid: str, request: Request, body: dict = Body(...)):
    _own(request, kind, oid)
    try:
        SV.restore(kind, oid, int(body.get("version") or 0))
    except (SV.NotPage, ValueError, TypeError) as e:
        _bad(e)
    return _out(request, kind, oid)


@router.post("/api/pages/{kind}/{oid}/approve")
def approve(kind: str, oid: str, request: Request):
    _own(request, kind, oid)
    try:
        out = SV.approve(kind, oid)
    except (SV.NotPage, ValueError) as e:
        _bad(e)
    if out["obj"].get("status") == "live" and kind == "shared":
        SR.share_mod.server.sync()
    return _out(request, kind, oid, note=out["note"])


# ----------------------------------------------------------------------------- requests to the administrators
@router.post("/api/page-requests")
def add_request(request: Request, body: dict = Body(...)):
    user = _user(request)
    ref = body.get("ref") if isinstance(body.get("ref"), dict) else None
    if ref:
        _own(request, str(ref.get("kind")), str(ref.get("id")))
        ref = {"kind": ref["kind"], "id": ref["id"]}
    try:
        return RQ.add(user, str(body.get("text") or ""), str(body.get("area") or ""), str(body.get("modality") or ""),
                      ref, str(body.get("contact") or ""))
    except ValueError as e:
        _bad(e)


@router.get("/api/page-requests")
def list_requests(request: Request):
    user = _user(request)
    return {"requests": RQ.for_user(user), "admin": user.get("role") == "admin", "open": RQ.open_count()}


@router.post("/api/page-requests/{rid}")
def answer_request(rid: str, request: Request, body: dict = Body(...)):
    if _user(request).get("role") != "admin":
        raise HTTPException(403, "Only an administrator can answer requests")
    try:
        return RQ.answer(rid, str(body.get("answer") or ""), str(body.get("status") or "answered"))
    except KeyError:
        raise HTTPException(404, "No such request")
    except ValueError as e:
        _bad(e)


# ----------------------------------------------------------------------------- uploads in pieces
@router.post("/api/uploads")
def upload_start(request: Request, body: dict = Body(...)):
    try:
        return UP.start(_user(request), str(body.get("name") or ""), int(body.get("size") or 0))
    except (TypeError, ValueError) as e:
        _bad(e)


@router.put("/api/uploads/{uid}")
async def upload_piece(uid: str, request: Request, offset: int = 0):
    user = _user(request)
    data = await request.body()
    try:
        return await run_in_threadpool(UP.piece, user, uid, offset, data)
    except KeyError:
        raise HTTPException(404, "This upload was not found; start again")
    except ValueError as e:
        _bad(e)


@router.post("/api/uploads/{uid}/import")
def upload_import(uid: str, request: Request, body: dict = Body(...)):
    """Add a finished upload to a shared task (an archive is unpacked); then GET this to follow it."""
    tid = str(body.get("task") or "")
    SR._own(request, tid)
    try:
        return UP.import_to_task(_user(request), uid, tid)
    except KeyError:
        raise HTTPException(404, "This upload was not found; start again")
    except ValueError as e:
        _bad(e)


@router.get("/api/uploads/{uid}/import")
def upload_job(uid: str, request: Request):
    _user(request)
    try:
        return UP.job(uid)
    except KeyError:
        raise HTTPException(404, "No such import")


@router.post("/api/pages/collect/{cid}/prompts")
def prompts(cid: str, request: Request, body: dict = Body(...)):
    """The prompts of a Data collection page from a text, or from an uploaded .txt / .csv file: one per line."""
    _own(request, "collect", cid)
    text, name = str(body.get("text") or ""), str(body.get("name") or "prompts.txt")
    if body.get("upload"):
        try:
            raw, name = UP.take(_user(request), str(body["upload"]))
        except KeyError:
            raise HTTPException(404, "This upload was not found")
        except ValueError as e:
            _bad(e)
        text = raw.decode("utf-8-sig", errors="replace")
    try:
        rows = SR.ST._text_items(name if name.lower().endswith((".txt", ".csv", ".tsv", ".json", ".jsonl")) else "prompts.txt",
                                 text.encode())
        lines = [r["text"] for r in rows if r["text"].strip()]
        if len(rows) == 1 and "\n" in rows[0]["text"]:   # one paragraph per line, read as a single item
            lines = [ln.strip() for ln in rows[0]["text"].splitlines() if ln.strip()]
        SV.set_settings("collect", cid, {"prompts": lines})
    except (SV.NotPage, S.Bad, ValueError) as e:
        _bad(e)
    return _out(request, "collect", cid)
