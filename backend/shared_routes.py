"""HTTP API of shared tasks (backend/shared_tasks.py).

/api/shared/...   the owner's side (signed in): create, data, design (by hand or with the design agent), link,
                  results and exports
/api/t/<token>/.. the volunteers' side (no account, the link is the key): start or resume a session, get the next
                  item, its media, answer or skip
/t/<token>        the volunteers' page (frontend/task/)
The owner can open the page with ?preview=1 before it is live; answers given in a preview are not saved.
"""
import secrets

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from . import config
from . import media as media_mod
from . import share as share_mod
from . import shared_tasks as ST
from .util import slugify

router = APIRouter()
PAGE = config.FRONTEND_DIR / "task" / "index.html"


def _user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Please sign in")
    return user


def _own(request: Request, tid: str) -> dict:
    user = _user(request)
    try:
        task = ST.get(tid)
    except ST.NotFound:
        raise HTTPException(404, "This shared task does not exist")
    if task["owner"] != user["id"] and user.get("role") != "admin":
        raise HTTPException(403, "This shared task belongs to someone else")
    return task


def _links(request: Request, task: dict) -> dict:
    """Where volunteers open the page: this server's address (works on the network, https), and the internet
    address when the tunnel is on."""
    base = str(request.base_url).rstrip("/")
    out = {"network": f"{base}/t/{task['token']}"}
    tun = share_mod.server.tunnel
    if tun.running() and tun.url:
        out["internet"] = f"{tun.url}/t/{task['token']}"
    out["tunnel"] = {"available": share_mod.Tunnel.available(), "running": tun.running()}
    return out


def _full(request: Request, task: dict) -> dict:
    return {**task, "summary": ST.summary(task), "data": ST.data_summary(task["id"]), "links": _links(request, task)}


# ----------------------------------------------------------------------------- owner
@router.get("/api/shared")
def list_shared(request: Request, kind: str = ""):
    return ST.list_for(_user(request), kind or None)


@router.post("/api/shared")
def create_shared(request: Request, body: dict = Body(default={})):
    task = ST.create(_user(request), str(body.get("title") or ""), str(body.get("kind") or "labelling"))
    return _full(request, task)


@router.get("/api/shared/{tid}")
def get_shared(tid: str, request: Request):
    return _full(request, _own(request, tid))


@router.patch("/api/shared/{tid}")
def patch_shared(tid: str, request: Request, body: dict = Body(...)):
    _own(request, tid)
    task = ST.update(tid, {k: body[k] for k in ("design", "status", "new_link", "listed") if k in body})
    if task["status"] == "live":
        share_mod.server.sync()  # keeps the shared listener up for the internet tunnel
    return _full(request, task)


@router.delete("/api/shared/{tid}")
def delete_shared(tid: str, request: Request):
    _own(request, tid)
    ST.delete(tid)
    return {"ok": True}


@router.post("/api/shared/{tid}/upload")
async def upload_shared(tid: str, request: Request, file: UploadFile = File(...), rel_path: str = Form(None)):
    _own(request, tid)
    data = await file.read()
    n = await run_in_threadpool(ST.add_upload, tid, file.filename or "file", rel_path, data)
    return {"added": n}


@router.post("/api/shared/{tid}/link")
def link_shared(tid: str, request: Request, body: dict = Body(...)):
    _own(request, tid)
    if _user(request).get("role") != "admin":
        raise HTTPException(403, "Only an administrator can add data from server folders")
    return ST.link_folder(tid, str(body.get("path") or ""))


@router.get("/api/shared/{tid}/items")
def list_items(tid: str, request: Request, offset: int = 0, limit: int = 60):
    _own(request, tid)
    its = ST.items(tid)
    page = [{k: v for k, v in i.items() if k != "abs_path"} | ({"text": i["text"][:300]} if i["kind"] == "text" else {})
            for i in its[offset:offset + min(limit, 500)]]
    return {"total": len(its), "items": page}


@router.delete("/api/shared/{tid}/items")
def clear_items(tid: str, request: Request):
    _own(request, tid)
    ST.clear_items(tid)
    return {"ok": True}


@router.delete("/api/shared/{tid}/items/{iid}")
def remove_item(tid: str, iid: str, request: Request):
    _own(request, tid)
    ST.remove_item(tid, iid)
    return {"ok": True}


@router.get("/api/shared/{tid}/items/{iid}/thumb")
def item_thumb(tid: str, iid: str, request: Request):
    _own(request, tid)
    return _thumb(tid, iid)


@router.post("/api/shared/{tid}/design/chat")
async def design_chat(tid: str, request: Request, body: dict = Body(...)):
    """The design agent: the owner describes the task, the page is redesigned."""
    from .agent import designer
    task = _own(request, tid)
    msg = str(body.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "Say what the page should be like")
    may_link = _user(request).get("role") == "admin"
    out = await run_in_threadpool(designer.chat, task, msg, may_link)
    return {**out, "task": _full(request, ST.get(tid))}


@router.get("/api/shared/{tid}/results")
def shared_results(tid: str, request: Request, where: str = ""):
    """where=native:yes,device:headphones keeps only people who answered the listener questions that way."""
    cond = dict(x.split(":", 1) for x in where.split(",") if ":" in x) if where else None
    return ST.results(_own(request, tid), cond)


@router.post("/api/shared/{tid}/key")
def shared_key(tid: str, request: Request, body: dict = Body(...)):
    """The blind key of a listening test (clip,system per line); kept private, used for results per system."""
    _own(request, tid)
    return ST.set_key(tid, str(body.get("csv") or ""))


@router.get("/api/shared/{tid}/export.{fmt}")
def shared_export(tid: str, fmt: str, request: Request):
    task = _own(request, tid)
    name = slugify(task["design"]["title"], 40) or tid
    if fmt == "csv":
        body, mime = ST.export_csv(task), "text/csv"
    elif fmt == "jsonl":
        body, mime = ST.export_jsonl(task), "application/x-ndjson"
    else:
        raise HTTPException(404, "Export as csv or jsonl")
    return Response(body, media_type=f"{mime}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})


# ----------------------------------------------------------------------------- volunteers
def _public(request: Request, token: str, preview: bool) -> tuple[dict, bool]:
    """The task behind a link, and whether this is the owner's preview. Closed and draft tasks only open
    in the owner's preview."""
    task = ST.by_token(token)
    if not task:
        raise HTTPException(404, "This link does not work. Ask the person who shared it for a new one.")
    user = getattr(request.state, "user", None)
    is_owner = bool(user and (user["id"] == task["owner"] or user.get("role") == "admin"))
    if preview and is_owner:
        return task, True
    if task["status"] != "live":
        msg = {"draft": "This task is not open yet.", "paused": "This task is paused for now. Try again later.",
               "closed": "This task is closed. Thank you for your interest!"}[task["status"]]
        raise HTTPException(403, msg)
    return task, False


def _sid(value) -> str:
    return str(value or "").strip().upper()


def _item_out(token: str, it: dict, preview: bool) -> dict:
    # never the system behind a clip (blind tests): only what the person needs to see
    out = {"id": it["id"], "kind": it["kind"], "name": it["name"], "group": it.get("group"),
           **({"practice": True} if it.get("practice") else {}), **({"text": it["text"]} if it.get("text") else {})}
    if it["kind"] == "text":
        out.update(text=it["text"], info=it.get("info") or {})
    elif it.get("pending"):  # listed before its media arrived: the page says so instead of playing anything
        out["pending"] = True
    else:
        out["src"] = f"/api/t/{token}/item/{it['id']}/media" + ("?preview=1" if preview else "")
        out.update(duration=it.get("duration"), playable=it.get("playable", True))
    return out


@router.get("/api/public/tasks")
def public_tasks(kind: str = "labelling"):
    """The public pages: live tasks of a kind (labelling, or listening tests) their owners listed. Anyone can open them."""
    return {"tasks": ST.public_list(kind if kind in ST.KINDS else "labelling")}


@router.get("/t/{token}")
def task_page(token: str):
    return FileResponse(PAGE, headers={"Cache-Control": "no-cache"})


@router.get("/api/t/{token}")
def public_task(token: str, request: Request, preview: bool = False):
    task, prev = _public(request, token, preview)
    d = task["design"]
    return {"design": d, "preview": prev, "progress": ST.progress(task), "kind": task.get("kind", "labelling"),
            "kinds": sorted(ST.data_summary(task["id"])["by_kind"]), "groups": ST.group_progress(task),
            "practice": sum(1 for i in ST.items(task["id"]) if i.get("practice"))}


@router.post("/api/t/{token}/session")
def public_session(token: str, request: Request, body: dict = Body(default={}), preview: bool = False):
    """Start a session, or resume one by its ID."""
    task, prev = _public(request, token, preview)
    if prev:
        return {"session": {"id": "PREVIEW", "name": "", "done": 0}, "progress": ST.progress(task),
                "groups": ST.group_progress(task)}
    if body.get("resume"):
        s = ST.session(task, _sid(body["resume"]))
        if not s:
            raise HTTPException(404, "No session with this ID. Check it, or start a new one.")
    else:
        s = ST.start_session(task, str(body.get("name") or ""), body.get("info") or {})
    return {"session": {k: s[k] for k in ("id", "name", "done")}, "history": s.get("history") or [],
            "progress": ST.progress(task, s["id"]),
            "groups": ST.group_progress(task, s["id"])}


@router.get("/api/t/{token}/next")
def public_next(token: str, request: Request, session: str = "", group: str = "", preview: bool = False):
    task, prev = _public(request, token, preview)
    if prev:
        its = [i for i in ST.items(task["id"]) if not group or i.get("group") == group]
        it = secrets.choice(its) if its else None
        return {"item": _item_out(token, it, True) if it else None, "progress": ST.progress(task, None, group or None)}
    it = ST.next_item(task, _sid(session), group or None)
    if it is None and not ST.session(task, _sid(session)):
        raise HTTPException(404, "This session was not found. Start a new one.")
    return {"item": _item_out(token, it, False) if it else None, "progress": ST.progress(task, _sid(session), group or None)}


@router.get("/api/t/{token}/groups")
def public_groups(token: str, request: Request, session: str = "", preview: bool = False):
    task, _ = _public(request, token, preview)
    return {"groups": ST.group_progress(task, _sid(session) or None)}


@router.post("/api/t/{token}/answer")
def public_answer(token: str, request: Request, body: dict = Body(...), preview: bool = False):
    task, prev = _public(request, token, preview)
    if prev:
        ST._check_values(task, body.get("values") or {})  # still tells the owner when a required answer is missing
        return {"ok": True, "preview": True, "progress": ST.progress(task)}
    try:
        s = ST.answer(task, _sid(body.get("session")), str(body.get("item") or ""), body.get("values") or {}, body.get("ms"),
                      body.get("plays"))
    except ST.NotFound:
        raise HTTPException(404, "This item is no longer part of the task")
    return {"ok": True, "done": s["done"], "history": s.get("history") or [], "progress": ST.progress(task, s["id"])}


@router.get("/api/t/{token}/answered/{iid}")
def public_answered(token: str, iid: str, request: Request, session: str = "", preview: bool = False):
    """An item this person already answered, with their answer: going back to change it."""
    task, prev = _public(request, token, preview)
    if not task["design"]["settings"].get("allow_back"):
        raise HTTPException(403, "Going back is turned off for this task")
    try:
        r = ST.previous(task, _sid(session), iid)
    except ST.NotFound:
        raise HTTPException(404, "Not answered yet")
    return {"item": _item_out(token, r["item"], prev), "values": r["values"]}


@router.post("/api/t/{token}/comment")
def public_comment(token: str, request: Request, body: dict = Body(...), preview: bool = False):
    task, prev = _public(request, token, preview)
    if not prev:
        ST.save_comment(task, _sid(body.get("session")), str(body.get("text") or ""))
    return {"ok": True}


@router.post("/api/t/{token}/skip")
def public_skip(token: str, request: Request, body: dict = Body(...), preview: bool = False):
    task, prev = _public(request, token, preview)
    if not prev:
        ST.skip(task, _sid(body.get("session")), str(body.get("item") or ""))
    return {"ok": True}


@router.get("/api/t/{token}/item/{iid}/media")
def public_media(token: str, iid: str, request: Request, preview: bool = False):
    task, _ = _public(request, token, preview)
    try:
        it = ST.item(task["id"], iid)
    except ST.NotFound:
        raise HTTPException(404, "No such item")
    if it["kind"] == "text" or it.get("pending"):
        raise HTTPException(404, "This item has no media yet")
    path = ST.item_path(task["id"], it)
    if it["kind"] == "audio" and not it.get("playable", True):
        f = ST.cache_dir(task["id"], iid) / "audio.wav"  # formats browsers cannot play are converted once
        if not f.exists():
            x = media_mod.get_audio((task["id"], iid), path, ST.cache_dir(task["id"], iid))
            f.write_bytes(media_mod.wav_bytes(x))
        return FileResponse(f, media_type="audio/wav")
    return FileResponse(path)


def _thumb(tid: str, iid: str):
    try:
        it = ST.item(tid, iid)
    except ST.NotFound:
        raise HTTPException(404, "No such item")
    if it["kind"] not in ("image", "video"):
        raise HTTPException(404, "No thumbnail")
    f = ST.cache_dir(tid, iid) / "thumb.jpg"
    if not f.exists():
        f.write_bytes(media_mod.thumbnail(ST.item_path(tid, it), it["kind"], it.get("duration")))
    return Response(f.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
