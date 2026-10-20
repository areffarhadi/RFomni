"""Omni Studio API + static frontend. Run with:  python -m backend  (or ./start.sh)"""
import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config
from . import formats
from . import media as media_mod
from . import tasks as task_reg
from .engine import get_engine
from . import jobs as jobs_mod
from .jobs import MODES, JobManager
from .store import Conflict, NotFound, ProjectStore
from .util import new_id, now_iso, safe_rel_path, unique_path

store = ProjectStore(config.PROJECTS_DIR)
jobs = JobManager(store)
VERSION, CODENAME = "0.2.0", "Mosaic"
app = FastAPI(title="Omni Studio", version=VERSION)


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


def _cached(body: bytes, media_type: str) -> Response:
    return Response(body, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


# -----------------------------------------------------------------------------
# meta
# -----------------------------------------------------------------------------
@app.get("/api/info")
def info():
    return {"app": "Omni Studio", "version": VERSION, "codename": CODENAME, "workspace": str(config.WORKSPACE),
            "engine": get_engine().info(), "run_modes": MODES}


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
def list_tasks():
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
@app.get("/api/projects")
def list_projects():
    return store.list_projects()


@app.post("/api/projects")
def create_project(body: dict = Body(...)):
    return store.create_project(body.get("name", ""), body.get("description", ""), body.get("tasks"))


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = store.get_project(pid)
    p["stats"] = store.stats(pid)
    p["media"] = store.list_media(pid)
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
def get_annotations(pid: str, mid: str):
    return store.load_doc(pid, mid)


@app.put("/api/projects/{pid}/media/{mid}/annotations")
def put_annotations(pid: str, mid: str, body: dict = Body(...), force: bool = False):
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
def export_file(pid: str, mid: str, format: str = "eaf"):
    item = store.get_media(pid, mid)
    doc = store.load_doc(pid, mid)
    text = _export_one(format, doc, item, store.media_path(pid, item), store.get_project(pid))
    if text is None:
        raise HTTPException(404, f"Nothing to export as {format} for this file")
    ext, mt = EXPORTERS[format]
    name = Path(item["name"]).stem + ext
    return Response(text, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/projects/{pid}/export.zip")
def export_project(pid: str, formats_: str = Query("json,eaf,csv,manifest", alias="formats"),
                   scope: str = "annotated", anchor: str = "asr", media: str = ""):
    proj = store.get_project(pid)
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
        for item in items:
            doc = store.load_doc(pid, item["id"])
            path = store.media_path(pid, item)
            pairs.append((item, doc))
            stem = str(Path(item["rel_path"]).with_suffix(""))
            for fmt in wanted:
                if fmt in ("csv", "manifest", "coco"):
                    continue
                text = _export_one(fmt, doc, item, path, proj)
                if text:
                    z.writestr(f"{proj['id']}/files/{stem}{EXPORTERS[fmt][0]}", text)
            if "manifest" in wanted:
                manifest += formats.manifest_records(doc, item, str(path), anchor)
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


# -----------------------------------------------------------------------------
# review queue
# -----------------------------------------------------------------------------
@app.get("/api/projects/{pid}/review")
def review_queue(pid: str, threshold: float | None = None, task: str = "", limit: int = 500):
    proj = store.get_project(pid)
    thr = proj["settings"].get("low_confidence", 0.6) if threshold is None else threshold
    out = []
    for item in store.list_media(pid):
        sm = item.get("summary") or {}
        if not sm.get("n_segments"):
            continue
        doc = store.load_doc(pid, item["id"])
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
# frontend
# -----------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")


def main():
    import uvicorn
    host = os.environ.get("OMNI_STUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("OMNI_STUDIO_PORT", "8710"))
    print(f"\n  Omni Studio {VERSION} ({CODENAME})  ->  http://{host}:{port}\n  workspace: {config.WORKSPACE}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
