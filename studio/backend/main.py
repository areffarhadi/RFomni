"""Omni Studio API + static frontend. Run with:  python -m backend  (or ./start.sh)"""
import io
import json
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth
from . import config
from . import formats
from . import media as media_mod
from . import tasks as task_reg
from .engine import get_engine
from . import jobs as jobs_mod
from . import share as share_mod
from . import speakers as spk_mod
from . import stats as stats_mod
from .jobs import MODES, JobManager
from .store import Conflict, NotFound, ProjectStore
from .util import new_id, now_iso, safe_rel_path, unique_path

store = ProjectStore(config.PROJECTS_DIR)
jobs = JobManager(store)
VERSION, CODENAME = "0.5.1", "Agora"
app = FastAPI(title="Omni Studio", version=VERSION)
share_mod.server.app = app  # the shared listener serves the same app behind its gate


# -----------------------------------------------------------------------------
# errors + caching headers
# -----------------------------------------------------------------------------
@app.exception_handler(NotFound)
async def _nf(_req, exc):
    return JSONResponse({"detail": f"Not found: {exc}"}, status_code=404)


@app.exception_handler(Conflict)
async def _conflict(_req, exc):
    return JSONResponse({"detail": str(exc), "current_rev": exc.current_rev}, status_code=409)


@app.exception_handler(ValueError)
async def _bad(_req, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if not p.startswith("/api/") or p.endswith("/annotations"):
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


# Sign-in (backend/auth.py): the API and the app page need a signed-in user; the sign-in and landing
# pages, scripts and styles do not. Requests through a share link were already let in by share.py.
ADMIN_ONLY = ("/api/fs", "/api/share-server/")   # these read the server's own disk or open it to the internet
PUBLIC_API = ("/api/auth/", "/api/info", "/api/agent/v1/models", "/api/t/", "/api/c/", "/api/public/")  # models: the chat lists the assistant before anyone signs in


def _admin_only(path: str, method: str) -> bool:
    return path.startswith(ADMIN_ONLY) or (method == "POST" and path.startswith("/api/projects/") and path.endswith("/media/link"))


@app.middleware("http")
async def require_sign_in(request: Request, call_next):
    path = request.url.path
    app_page = path in ("/", "/index.html")
    if _share(request) or not (path.startswith("/api/") or app_page):
        return await call_next(request)
    if not auth.ENABLED:
        request.state.user = {"id": os.environ.get("OMNI_AGENT_USER", "local"), "name": "", "email": "", "role": "admin"}
        return await call_next(request)
    try:
        user = await run_in_threadpool(auth.request_user, request.headers)
    except auth.AuthError as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status)
    request.state.user = user
    if path.startswith(PUBLIC_API):
        return await call_next(request)
    if not user:
        if app_page:
            return Response(status_code=303, headers={"location": "/login/?next=studio"})
        return JSONResponse({"detail": "Please sign in"}, status_code=401)
    if user["role"] == "pending":
        if app_page:
            return Response(status_code=303, headers={"location": "/login/?pending=1"})
        return JSONResponse({"detail": "Your account is waiting for an administrator's approval"}, status_code=403)
    if user["role"] != "admin" and _admin_only(path, request.method):
        return JSONResponse({"detail": "Only an administrator can do this"}, status_code=403)
    return await call_next(request)


def _set_session(resp: Response, request: Request, out: dict) -> None:
    # not HttpOnly: the chat page's own sign-in page reads this cookie to sign you in there too
    resp.set_cookie(auth.COOKIE, out["token"], path="/", samesite="lax", secure=request.url.scheme == "https",
                    max_age=max(60, int(out["expires_at"] - time.time())) if out.get("expires_at") else None)


@app.post("/api/auth/signin")
def sign_in(request: Request, body: dict = Body(...)):
    try:
        out = auth.sign_in(str(body.get("email", "")).strip(), str(body.get("password", "")))
    except auth.AuthError as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status)
    resp = JSONResponse({"user": {k: out.get(k) for k in ("id", "email", "name", "role")}})
    _set_session(resp, request, out)
    return resp


@app.post("/api/auth/signup")
def sign_up(request: Request, body: dict = Body(...)):
    try:
        out = auth.sign_up(str(body.get("name", "")).strip(), str(body.get("email", "")).strip(), str(body.get("password", "")))
    except auth.AuthError as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status)
    resp = JSONResponse({"user": {k: out.get(k) for k in ("id", "email", "name", "role")}})
    _set_session(resp, request, out)
    return resp


@app.post("/api/auth/signout")
def sign_out():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse({"detail": "Please sign in"}, status_code=401)
    return {"user": user, "auth": auth.ENABLED}


def _share(request: Request):
    """The share link a request came through (only on the shared listener), else None."""
    return request.scope.get("state", {}).get("share")


def _cached(body: bytes, media_type: str) -> Response:
    return Response(body, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


# -----------------------------------------------------------------------------
# meta
# -----------------------------------------------------------------------------
@app.get("/api/info")
def info(request: Request):
    sh = _share(request)
    out = {"app": "Omni Studio", "version": VERSION, "codename": CODENAME, "workspace": str(config.WORKSPACE),
           "engine": get_engine().info(), "run_modes": MODES}
    if sh:
        proj = store.get_project(sh["project_id"])
        out["workspace"] = None
        out["share"] = {"id": sh["id"], "label": sh["label"], "access": sh["access"], "allow_download": sh["allow_download"],
                        "ask_name": sh.get("ask_name", False),
                        "project_id": sh["project_id"], "project_name": proj["name"], "tasks": sh["tasks"],
                        "note": sh.get("note", ""), "expires_at": sh.get("expires_at")}
    return out


@app.get("/api/fs")
def browse(path: str = "~"):
    """Folder browser for linking local data (the server only listens on localhost)."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"Not a folder: {p}")
    dirs, n_media = [], 0
    try:
        for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if c.name.startswith("."):
                continue
            if c.is_dir():
                dirs.append({"name": c.name, "path": str(c)})
            elif c.suffix.lower() in config.MEDIA_EXT:
                n_media += 1
    except PermissionError:
        pass
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs,
            "media_here": n_media, "home": str(Path.home())}


# -----------------------------------------------------------------------------
# tasks
# -----------------------------------------------------------------------------
@app.get("/api/tasks")
def list_tasks(request: Request):
    sh = _share(request)
    if sh:  # a shared link only sees the tasks of its project
        keep = set(sh["tasks"] or store.get_project(sh["project_id"])["tasks"])
        return {"tasks": [t for t in task_reg.all_tasks() if t["id"] in keep], "categories": task_reg.categories(), "presets": []}
    return {"tasks": task_reg.all_tasks(), "categories": task_reg.categories(), "presets": task_reg.presets()}


@app.post("/api/tasks")
def create_task(body: dict = Body(...)):
    return task_reg.save_custom_task(body)


@app.put("/api/tasks/{task_id}")
def update_task(task_id: str, body: dict = Body(...)):
    """Custom tasks are replaced; built-in tasks keep their output type and store only the changed fields."""
    try:
        if task_reg.builtin_default(task_id):
            return task_reg.save_override(task_id, body)
        return task_reg.save_custom_task(body, task_id)
    except KeyError:
        raise HTTPException(404, "No such task")


@app.post("/api/tasks/{task_id}/reset")
def reset_task(task_id: str):
    """Bring a built-in task back to its default classes, prompt and names."""
    try:
        return task_reg.reset_override(task_id)
    except KeyError:
        raise HTTPException(404, "Only built-in tasks can be reset")


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    try:
        task_reg.delete_custom_task(task_id)
    except KeyError:
        raise HTTPException(404, "Only your own prompt tasks can be deleted")
    return {"ok": True}


@app.post("/api/tasks/preview")
def preview_prompt(body: dict = Body(...)):
    """Final prompt (instruction + structured output spec) exactly as a model would receive it."""
    t = {"prompt": body.get("prompt", ""), "output": body.get("output", "text"),
         "granularity": body.get("granularity", "file"), "labels": body.get("labels") or [],
         "score_min": body.get("score_min", 1), "score_max": body.get("score_max", 5)}
    return {"prompt": task_reg.build_prompt(t)}


# -----------------------------------------------------------------------------
# projects
# -----------------------------------------------------------------------------
def _folder_preview(items: list, n: int = 4) -> list:
    """A few files to show when a project folder opens on the Projects page, mixing the media types."""
    queues = {mod: [m for m in items if m.get("modality") == mod] for mod in ("video", "image", "audio")}
    out = []
    while len(out) < n and any(queues.values()):
        for mod in ("video", "image", "audio"):
            if queues[mod] and len(out) < n:
                out.append(queues[mod].pop(0))
    keep = ("id", "name", "modality", "duration", "width", "height")
    return [{k: m.get(k) for k in keep} for m in out]


@app.get("/api/projects")
def list_projects():
    projects = store.list_projects()
    for p in projects:   # each project is drawn as a folder: a cover picture and a few files to peek at
        items = store.list_media(p["id"])
        cover = next((m for m in items if m.get("modality") == "image"), None) \
            or next((m for m in items if m.get("modality") == "video"), None)
        p["cover"] = cover["id"] if cover else None
        p["preview"] = _folder_preview(items)
    return projects


@app.post("/api/projects")
def create_project(body: dict = Body(...)):
    return store.create_project(body.get("name", ""), body.get("description", ""), body.get("tasks"))


@app.get("/api/projects/{pid}")
def get_project(pid: str, request: Request):
    p = store.get_project(pid)
    p["stats"] = store.stats(pid)
    p["media"] = store.list_media(pid)
    sh = _share(request)
    if sh:
        if sh["tasks"]:
            p["tasks"] = [t for t in p["tasks"] if t in sh["tasks"]]
            p["tier_order"] = [t for t in p.get("tier_order") or [] if t in sh["tasks"]]
        p["jobs"] = []
        for m in p["media"]:
            m.pop("abs_path", None)
        return p
    p["jobs"] = jobs.list(pid)
    return p


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, body: dict = Body(...)):
    old_low = store.get_project(pid)["settings"].get("low_confidence")
    p = store.update_project(pid, body)
    if p["settings"].get("low_confidence") != old_low:
        store.refresh_summaries(pid)
    return p


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    media_mod.drop_caches((pid,))
    store.delete_project(pid)
    share_mod.delete_project_shares(pid)
    share_mod.server.sync()
    return {"ok": True}


# -----------------------------------------------------------------------------
# media
# -----------------------------------------------------------------------------
def _new_item(path: Path, rel_path: str, source: str) -> dict:
    meta = media_mod.probe(path)
    item = {"id": new_id("m", 5), "name": path.name, "rel_path": rel_path, "source": source,
            **meta, "added_at": now_iso(), "status": "new", "summary": None, "reviewed": False}
    if source == "linked":
        item["abs_path"] = str(path.resolve())
    return item


@app.post("/api/projects/{pid}/media/upload")
async def upload_media(pid: str, file: UploadFile = File(...), rel_path: str = Form(None)):
    pdir = store.pdir(pid)
    rel = safe_rel_path(rel_path or file.filename or "file")
    if Path(rel.name).suffix.lower() not in config.MEDIA_EXT:
        raise HTTPException(415, f"Unsupported file type: {rel.name}")
    dest = unique_path(pdir / "media" / Path(*rel.parts))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        while chunk := await file.read(4 << 20):
            f.write(chunk)
    try:
        item = await run_in_threadpool(_new_item, dest, str(dest.relative_to(pdir / "media")), "upload")
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(415, f"Could not read {rel.name}: {e}")
    store.add_media(pid, [item])
    return item


@app.post("/api/projects/{pid}/media/link")
def link_media(pid: str, body: dict = Body(...)):
    """Add files from a local folder without copying them."""
    store.pdir(pid)
    root = Path(str(body.get("path", ""))).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Path does not exist: {root}")
    recursive = bool(body.get("recursive", True))
    if root.is_file():
        files, base = [root], root.parent
    else:
        it = root.rglob("*") if recursive else root.iterdir()
        files = sorted(p for p in it if p.is_file() and p.suffix.lower() in config.MEDIA_EXT
                       and not any(part.startswith(".") for part in p.relative_to(root).parts))
        base = root.parent
    have = {m.get("abs_path") for m in store.list_media(pid)}
    todo = [p for p in files if str(p) not in have]
    if len(todo) > 20000:
        raise ValueError("More than 20,000 files; please link a smaller folder")

    def mk(p):
        try:
            return _new_item(p, str(p.relative_to(base)), "linked"), None
        except Exception as e:
            return None, {"file": str(p), "error": str(e)}

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(mk, todo))
    items = [r for r, _ in res if r]
    errors = [e for _, e in res if e]
    store.add_media(pid, items)
    return {"added": len(items), "skipped": len(files) - len(todo), "errors": errors[:50]}


@app.patch("/api/projects/{pid}/media/{mid}")
def patch_media(pid: str, mid: str, body: dict = Body(...)):
    patch = {k: body[k] for k in ("reviewed", "note") if k in body}
    return store.update_media(pid, mid, patch)


@app.delete("/api/projects/{pid}/media/{mid}")
def delete_media(pid: str, mid: str):
    media_mod.drop_caches((pid, mid))
    store.remove_media(pid, mid)
    return {"ok": True}


def _media(pid, mid):
    item = store.get_media(pid, mid)
    path = store.media_path(pid, item)
    if not path.exists():
        raise HTTPException(410, f"Media file is missing on disk: {path}")
    return item, path


@app.get("/api/projects/{pid}/media/{mid}/file")
def media_file(pid: str, mid: str):
    item, path = _media(pid, mid)
    return FileResponse(path, filename=item["name"], content_disposition_type="inline")


def _audio(pid, mid):
    item, path = _media(pid, mid)
    if item["modality"] == "image" or not item.get("has_audio", True):
        raise HTTPException(404, "No audio track")
    return item, media_mod.get_audio((pid, mid), path, store.cache_dir(pid, mid))


@app.get("/api/projects/{pid}/media/{mid}/audio.wav")
def media_audio_proxy(pid: str, mid: str):
    """16 kHz mono WAV, used when the browser cannot play the original."""
    _, x = _audio(pid, mid)
    return _cached(media_mod.wav_bytes(x), "audio/wav")


@app.get("/api/projects/{pid}/media/{mid}/peaks")
def media_peaks(pid: str, mid: str, start: float = 0, end: float = 0, n: int = Query(512, le=8192)):
    item, x = _audio(pid, mid)
    if end <= start:
        end = len(x) / config.SR
    out = media_mod.peaks(x, start, end, n)
    out["peak"] = media_mod.audio_peak(x)
    out["duration"] = len(x) / config.SR
    return JSONResponse(out, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/projects/{pid}/media/{mid}/spectrogram")
def media_spectrogram(pid: str, mid: str, start: float, end: float, w: int = 512, h: int = 160,
                      scale: str = "linear", cmap: str = "magma", fmax: float = 8000, drange: float = 70):
    _, x = _audio(pid, mid)
    png = media_mod.spectrogram_png((pid, mid), x, store.cache_dir(pid, mid), start, end, w, h,
                                    scale=scale, cmap=cmap, fmax=fmax, drange=drange)
    return _cached(png, "image/png")


@app.get("/api/projects/{pid}/media/{mid}/frame")
def media_frame(pid: str, mid: str, t: float = 0, h: int = Query(72, ge=16, le=720)):
    item, path = _media(pid, mid)
    if item["modality"] != "video":
        raise HTTPException(404, "Not a video")
    fdir = store.cache_dir(pid, mid) / "frames"
    fdir.mkdir(exist_ok=True)
    f = fdir / f"{int(round(t * 1000))}_{h}.jpg"
    if f.exists():
        return _cached(f.read_bytes(), "image/jpeg")
    data = media_mod.jpeg_bytes(media_mod.video_frame(path, t, h))
    f.write_bytes(data)
    return _cached(data, "image/jpeg")


@app.get("/api/projects/{pid}/media/{mid}/thumb")
def media_thumb(pid: str, mid: str):
    item, path = _media(pid, mid)
    if item["modality"] == "audio":
        raise HTTPException(404, "No thumbnail for audio")
    f = store.cache_dir(pid, mid) / "thumb.jpg"
    if not f.exists():
        f.write_bytes(media_mod.thumbnail(path, item["modality"], item.get("duration")))
    return _cached(f.read_bytes(), "image/jpeg")


# -----------------------------------------------------------------------------
# annotations
# -----------------------------------------------------------------------------
@app.get("/api/projects/{pid}/media/{mid}/annotations")
def get_annotations(pid: str, mid: str, request: Request):
    return share_mod.filter_doc(store.load_doc(pid, mid), _share(request))


@app.put("/api/projects/{pid}/media/{mid}/annotations")
def put_annotations(pid: str, mid: str, request: Request, body: dict = Body(...), force: bool = False):
    sh = _share(request)
    if sh:  # keep tiers the link does not show, and record who changed what
        person = share_mod.get_session(sh, request.headers.get("x-omni-session"))
        with store.lock(("m", pid, mid)):
            body, n = share_mod.merge_shared_save(store.load_doc(pid, mid), body, sh, person)
            doc = store.save_doc(pid, mid, body, expected_rev=None if force else body.get("rev"))
        share_mod.count_edits(sh, n, person)
    else:
        doc = store.save_doc(pid, mid, body, expected_rev=None if force else body.get("rev"))
    return {"rev": doc["rev"], "updated_at": doc["updated_at"], "media": store.get_media(pid, mid)}


@app.post("/api/projects/{pid}/media/{mid}/annotations/import")
async def import_annotations(pid: str, mid: str, file: UploadFile = File(...)):
    data = await file.read()
    imported = formats.import_any(file.filename or "", data)
    if not imported["segments"]:
        raise ValueError("No annotations found in that file")
    with store.lock(("m", pid, mid)):
        doc = formats.merge_import(store.load_doc(pid, mid), imported)
        doc = store.save_doc(pid, mid, doc)
    return {"doc": doc, "tiers": [t["name"] for t in imported["tiers"]], "segments": len(imported["segments"])}


EXPORTERS = {
    "json": (".json", "application/json"),
    "eaf": (".eaf", "application/xml"),
    "textgrid": (".TextGrid", "text/plain"),
    "srt": (".srt", "text/plain"),
    "vtt": (".vtt", "text/vtt"),
    "rttm": (".rttm", "text/plain"),
    "csv": (".csv", "text/csv"),
    "coco": (".coco.json", "application/json"),
}


def _project_order(doc, proj):
    """Tiers in the project's tier order (set by dragging channels in the editor)."""
    order = proj.get("tier_order") or proj.get("tasks") or []
    rank = {k: i for i, k in enumerate(order)}
    tiers = sorted(doc["tiers"], key=lambda t: (rank.get(t.get("task_id") or t["id"], len(rank)), doc["tiers"].index(t)))
    return {**doc, "tiers": tiers}


def _export_one(fmt, doc, item, path, proj=None):
    if proj:
        doc = _project_order(doc, proj)
    if fmt == "json":
        return formats.export_json(doc, item)
    if fmt == "eaf":
        return formats.export_eaf(doc, item, path) if item["modality"] != "image" else None
    if fmt == "textgrid":
        return formats.export_textgrid(doc, item) if item["modality"] != "image" else None
    if fmt == "srt":
        return formats.export_srt(doc, item)
    if fmt == "vtt":
        return formats.export_vtt(doc, item)
    if fmt == "rttm":
        return formats.export_rttm(doc, item)
    if fmt == "csv":
        return formats.export_csv([(item, doc)])
    if fmt == "coco":
        return formats.export_coco([(item, doc)]) if item["modality"] == "image" else None
    raise ValueError(f"unknown format {fmt}")


@app.get("/api/projects/{pid}/media/{mid}/export")
def export_file(pid: str, mid: str, request: Request, format: str = "eaf"):
    item = store.get_media(pid, mid)
    doc = share_mod.filter_doc(store.load_doc(pid, mid), _share(request))
    text = _export_one(format, doc, item, store.media_path(pid, item), store.get_project(pid))
    if text is None:
        raise HTTPException(404, f"Nothing to export as {format} for this file")
    ext, mt = EXPORTERS[format]
    name = Path(item["name"]).stem + ext
    return Response(text, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/projects/{pid}/export.zip")
def export_project(pid: str, request: Request, formats_: str = Query("json,eaf,csv,manifest", alias="formats"),
                   scope: str = "annotated", anchor: str = "asr", media: str = "", min_clip: float = 0.3):
    proj = store.get_project(pid)
    sh = _share(request)
    wanted = [f for f in formats_.split(",") if f]
    items = store.list_media(pid)
    if media:
        ids = set(media.split(","))
        items = [m for m in items if m["id"] in ids]
    if scope == "reviewed":
        items = [m for m in items if m.get("status") == "reviewed"]
    elif scope == "annotated":
        items = [m for m in items if m.get("status") != "new"]
    buf = io.BytesIO()
    pairs, manifest = [], []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        clip_rows = []
        for item in items:
            doc = share_mod.filter_doc(store.load_doc(pid, item["id"]), sh)
            path = store.media_path(pid, item)
            pairs.append((item, doc))
            stem = str(Path(item["rel_path"]).with_suffix(""))
            if "speaker_clips" in wanted:
                clip_rows += _speaker_clips(z, f"{proj['id']}/speaker_clips", pid, item, doc, stem, min_clip)
            for fmt in wanted:
                if fmt in ("csv", "manifest", "coco", "speaker_clips"):
                    continue
                text = _export_one(fmt, doc, item, path, proj)
                if text:
                    z.writestr(f"{proj['id']}/files/{stem}{EXPORTERS[fmt][0]}", text)
            if "manifest" in wanted:
                manifest += formats.manifest_records(doc, item, str(path), anchor)
        if clip_rows:
            head = "file,kind,speakers,start,end,duration,clip\n"
            z.writestr(f"{proj['id']}/speaker_clips/clips.csv", head + "".join(
                ",".join(_csv_cell(v) for v in r) + "\n" for r in clip_rows))
        if "csv" in wanted:
            z.writestr(f"{proj['id']}/annotations.csv", formats.export_csv(pairs))
        if "coco" in wanted and any(m["modality"] == "image" for m, _ in pairs):
            z.writestr(f"{proj['id']}/coco_annotations.json", formats.export_coco(pairs))
        if "manifest" in wanted:
            z.writestr(f"{proj['id']}/manifest.jsonl",
                       "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in manifest))
        z.writestr(f"{proj['id']}/project.json", json.dumps(
            {**proj, "exported_at": now_iso(), "files": len(items)}, ensure_ascii=False, indent=1))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{proj["id"]}-annotations.zip"'})


def _csv_cell(v) -> str:
    v = str(v)
    return '"' + v.replace('"', '""') + '"' if any(c in v for c in ',"\n') else v


def _speaker_clips(z, root, pid, item, doc, stem, min_clip) -> list:
    """WAV clips (16 kHz mono) of every speaker's pure speech and of every overlap region of one file."""
    if item["modality"] == "image" or not item.get("has_audio", True):
        return []
    r = spk_mod.regions_from_doc(doc)
    if not r or not (r["pure"] or r["overlap"]):
        return []
    x = media_mod.get_audio((pid, item["id"]), store.media_path(pid, item), store.cache_dir(pid, item["id"]))
    rows = []
    for kind, regs in (("pure", r["pure"]), ("overlap", r["overlap"])):
        for g in regs:
            a, b = g["start"], g["end"]
            if b - a < min_clip:
                continue
            who = g["value"] if kind == "pure" else "+".join(g["speakers"])
            safe = "".join(c if c.isalnum() or c in "-_+." else "_" for c in who) or "unknown"
            name = f"{stem}/{safe if kind == 'pure' else 'overlap/' + safe}/{a:09.3f}-{b:09.3f}.wav"
            z.writestr(f"{root}/{name}", media_mod.wav_bytes(x[int(a * config.SR): int(b * config.SR)]))
            rows.append((item["rel_path"], kind, " + ".join(g["speakers"]), f"{a:.3f}", f"{b:.3f}", f"{b - a:.3f}", name))
    return rows


# -----------------------------------------------------------------------------
# statistics
# -----------------------------------------------------------------------------
@app.get("/api/projects/{pid}/stats")
def project_stats(pid: str, request: Request):
    sh = _share(request)
    return stats_mod.project_stats(store, pid, (sh or {}).get("tasks") or None)


@app.get("/api/projects/{pid}/stats.csv")
def project_stats_csv(pid: str, request: Request):
    sh = _share(request)
    st = stats_mod.project_stats(store, pid, (sh or {}).get("tasks") or None)
    return Response(stats_mod.stats_csv(st), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{pid}-statistics.csv"'})


# -----------------------------------------------------------------------------
# share links (owner side; the shared listener itself lives in share.py)
# -----------------------------------------------------------------------------
@app.get("/api/projects/{pid}/shares")
def list_shares(pid: str):
    store.pdir(pid)
    return {"shares": share_mod.list_shares(pid), "server": share_mod.server.status(), "access": share_mod.ACCESS}


@app.post("/api/projects/{pid}/shares")
def create_share(pid: str, body: dict = Body(...)):
    sh = share_mod.create_share(store.get_project(pid), body)
    try:
        share_mod.server.start()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"share": sh, "server": share_mod.server.status()}


@app.patch("/api/shares/{sid}")
def patch_share(sid: str, body: dict = Body(...)):
    try:
        sh = share_mod.update_share(sid, body, store.get_project)
    except KeyError:
        raise HTTPException(404, "No such link")
    share_mod.server.sync()
    return {"share": sh, "server": share_mod.server.status()}


@app.delete("/api/shares/{sid}")
def delete_share(sid: str):
    try:
        share_mod.delete_share(sid)
    except KeyError:
        raise HTTPException(404, "No such link")
    share_mod.server.sync()
    return {"ok": True, "server": share_mod.server.status()}


@app.post("/api/share/session")
def share_session(request: Request, body: dict = Body(default={})):
    """Someone on an edit link starts a session (or resumes one by its ID); their edits are signed with it."""
    sh = _share(request)
    if not sh:
        raise HTTPException(404, "Only through a shared link")
    if body.get("resume"):
        person = share_mod.get_session(sh, str(body["resume"]))
        if not person:
            raise HTTPException(404, "No session with this ID. Check it, or start a new one.")
    else:
        person = share_mod.start_session(sh, str(body.get("name") or ""))
    return {"session": person}


@app.get("/api/shares")
def all_shares():
    """Every project's links, for the Shared tasks page: correction links with who took part."""
    names = {p["id"]: p["name"] for p in store.list_projects()}
    out = []
    for sh in share_mod.list_shares():
        if sh["project_id"] in names:
            people = sorted((sh.get("sessions") or {}).values(), key=lambda x: x["last_at"], reverse=True)
            out.append({**{k: v for k, v in sh.items() if k != "sessions"}, "project_name": names[sh["project_id"]],
                        "people": people})
    return {"shares": out, "server": share_mod.server.status()}


@app.post("/api/share-server/tunnel")
def start_tunnel():
    if not share_mod.server.running():
        raise HTTPException(409, "Create a share link first")
    try:
        share_mod.server.tunnel.start(share_mod.server.port)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return share_mod.server.status()


@app.delete("/api/share-server/tunnel")
def stop_tunnel():
    share_mod.server.tunnel.stop()
    return share_mod.server.status()


# -----------------------------------------------------------------------------
# review queue
# -----------------------------------------------------------------------------
@app.get("/api/projects/{pid}/review")
def review_queue(pid: str, request: Request, threshold: float | None = None, task: str = "", limit: int = 500):
    proj = store.get_project(pid)
    sh = _share(request)
    thr = proj["settings"].get("low_confidence", 0.6) if threshold is None else threshold
    out = []
    for item in store.list_media(pid):
        sm = item.get("summary") or {}
        if not sm.get("n_segments"):
            continue
        doc = share_mod.filter_doc(store.load_doc(pid, item["id"]), sh)
        tiers = {t["id"]: t for t in doc["tiers"]}
        for s in doc["segments"]:
            t = tiers[s["tier_id"]]
            if task and t.get("task_id") != task and t["id"] != task:
                continue
            if s["reviewed"] or s["confidence"] is None or s["confidence"] >= thr:
                continue
            out.append({"media_id": item["id"], "media_name": item["name"], "modality": item["modality"],
                        "tier_id": t["id"], "tier_name": t["name"], "color": t.get("color"),
                        "segment_id": s["id"], "start": s["start"], "end": s["end"],
                        "value": formats.val_str(s["value"])[:200], "confidence": s["confidence"],
                        "source": s["source"]})
    out.sort(key=lambda r: r["confidence"])
    return {"threshold": thr, "total": len(out), "items": out[:limit]}


# -----------------------------------------------------------------------------
# runs
# -----------------------------------------------------------------------------
@app.post("/api/projects/{pid}/test-task")
def test_task(pid: str, body: dict = Body(...)):
    """Try a task (saved, or an unsaved draft) on a few files without saving anything."""
    store.pdir(pid)
    task = body.get("task")
    if task:
        base = task_reg.get_task(task.get("id")) if task.get("id") else None
        if base and base.get("builtin"):
            task = {**base, **{k: task[k] for k in task_reg.OVERRIDABLE if k in task}}
        else:
            task = {**task_reg.validate_task(task), "id": task.get("id") or "draft"}
    else:
        task = task_reg.get_task(body.get("task_id", ""))
        if not task:
            raise HTTPException(404, "No such task")
    n = max(1, min(int(body.get("n", 3)), 10))
    return jobs_mod.test_task(store, pid, task, body.get("media_ids"), n, int(body.get("seed", 0)))
@app.post("/api/projects/{pid}/run")
def run_tasks(pid: str, body: dict = Body(...)):
    store.pdir(pid)
    return jobs.start(pid, body.get("task_ids") or [], body.get("media_ids"), body.get("mode", "missing"))


@app.get("/api/jobs")
def list_jobs(project: str | None = None):
    return jobs.list(project)


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "No such job")
    return j


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    return {"ok": jobs.cancel(jid)}


# -----------------------------------------------------------------------------
# assistant
# -----------------------------------------------------------------------------
from .agent import router as agent_router, voice_router  # noqa: E402  (its actions use store/jobs above)
from .shared_routes import router as shared_router  # noqa: E402
from .collect_routes import router as collect_router  # noqa: E402

app.include_router(agent_router)
app.include_router(voice_router)
app.include_router(shared_router)
app.include_router(collect_router)


# -----------------------------------------------------------------------------
# frontend
# -----------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")


def main():
    import uvicorn
    host = os.environ.get("OMNI_STUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("OMNI_STUDIO_PORT", "8710"))
    print(f"\n  Omni Studio {VERSION} ({CODENAME})  ->  {'https' if os.environ.get('OMNI_STUDIO_SSL_CERT') else 'http'}://{host}:{port}\n  workspace: {config.WORKSPACE}\n")
    share_mod.server.sync()  # shared links stay reachable after a restart
    if share_mod.server.running():
        print(f"  shared links on port {share_mod.server.port}\n")
    # https (OMNI_STUDIO_SSL_CERT/KEY) lets a browser on another machine use the microphone
    cert, key = os.environ.get("OMNI_STUDIO_SSL_CERT"), os.environ.get("OMNI_STUDIO_SSL_KEY")
    tls = {"ssl_certfile": cert, "ssl_keyfile": key} if cert and key else {}
    uvicorn.run(app, host=host, port=port, log_level="warning", **tls)
