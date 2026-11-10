"""Big uploads in pieces, and unpacking archives into a shared task.

Behind labelomni.com, Cloudflare refuses a request body over 100 MB, so the browser sends a large file in pieces
(16 MB each): start -> pieces in order -> finish. A finished upload is then added to a shared task: an archive
(.zip, .tar, .tar.gz / .tgz, .tar.bz2, .tar.xz) is unpacked there, any other data file is added as it is. That runs in
the background (an archive can hold thousands of files); the studio asks for its progress.

Unpacking is careful: only data files (shared_tasks.DATA_EXT) are kept; links, devices, hidden files, macOS
leftovers and paths that leave the folder are skipped; the total size and the number of files are capped.
Pieces live in workspace/uploads/<id>/ and are removed after the import, or after a day when abandoned.
"""
import shutil
import tarfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import config
from . import shared_tasks as ST
from .util import new_id, read_json, safe_rel_path, unique_path, write_json

ROOT = config.WORKSPACE / "uploads"
PIECE = 16 << 20                 # the browser sends pieces of this size (the last may be smaller)
MAX_FILE = 50 << 30              # one upload
MAX_UNPACKED = 100 << 30         # all files unpacked from one archive
MAX_FILES = 50000
ARCHIVES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
_lock = threading.Lock()
_jobs: dict = {}                 # upload id -> {"state": running | done | error, "added", "skipped", "seen", "error"}


def _dir(uid: str) -> Path:
    if not uid.startswith("up") or not uid[2:].isalnum():
        raise KeyError(uid)
    return ROOT / uid


def is_archive(name: str) -> bool:
    return name.lower().endswith(ARCHIVES)


def _cleanup() -> None:
    for d in ROOT.glob("up*"):
        if d.is_dir() and time.time() - d.stat().st_mtime > 86400:
            shutil.rmtree(d, ignore_errors=True)


def start(user: dict, name: str, size: int) -> dict:
    name = PurePosixPath(str(name or "upload").replace("\\", "/")).name[:200] or "upload"
    size = int(size)
    if not 0 < size <= MAX_FILE:
        raise ValueError(f"A file can be at most {MAX_FILE >> 30} GB")
    ROOT.mkdir(parents=True, exist_ok=True)
    _cleanup()
    free = shutil.disk_usage(ROOT).free
    if free < size * (3 if is_archive(name) else 1.2):   # an archive needs room for itself and what it holds
        raise ValueError("There is not enough free disk space on the server for this file")
    uid = new_id("up", 6)
    d = ROOT / uid
    d.mkdir(parents=True)
    (d / "data").touch()
    write_json(d / "meta.json", {"user": user["id"], "name": name, "size": size, "at": time.time()})
    return {"id": uid, "piece": PIECE}


def _meta(user: dict, uid: str) -> dict:
    try:
        m = read_json(_dir(uid) / "meta.json")
    except KeyError:
        m = None
    if not m or m["user"] != user["id"]:
        raise KeyError(uid)
    return m


def piece(user: dict, uid: str, offset: int, data: bytes) -> dict:
    m = _meta(user, uid)
    f = _dir(uid) / "data"
    with _lock:
        have = f.stat().st_size
        if offset != have:   # a piece sent twice, or one missing: the browser goes on from here
            return {"received": have}
        if len(data) > PIECE or have + len(data) > m["size"]:
            raise ValueError("This piece does not fit the file")
        with open(f, "ab") as fh:
            fh.write(data)
        return {"received": have + len(data)}


def _finished(user: dict, uid: str) -> tuple[Path, dict]:
    m = _meta(user, uid)
    f = _dir(uid) / "data"
    if f.stat().st_size != m["size"]:
        raise ValueError("The upload is not complete")
    return f, m


# ----------------------------------------------------------------------------- into a shared task
def _members(path: Path, name: str):
    """(relative name, size, open()) for each regular file in an archive."""
    if name.lower().endswith(".zip"):
        z = zipfile.ZipFile(path)
        for info in z.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if info.is_dir() or mode == 0o120000:   # folders and symbolic links
                continue
            yield info.filename, info.file_size, (lambda i=info: z.open(i))
    else:
        t = tarfile.open(path, "r:*")
        for info in t:
            if info.isfile():
                yield info.name, info.size, (lambda i=info: t.extractfile(i))


def _keep(rel: str) -> PurePosixPath | None:
    try:
        p = safe_rel_path(rel)
    except ValueError:
        return None
    if any(part.startswith(".") or part == "__MACOSX" for part in p.parts):
        return None
    return p if p.suffix.lower() in ST.DATA_EXT else None


def _import(tid: str, uid: str, path: Path, name: str) -> None:
    job = _jobs[uid]
    media = ST._dir(tid) / "media"
    new = []
    try:
        if not is_archive(name):
            rel = _keep(name)
            if not rel:
                raise ValueError(f"Unsupported file type: {name}")
            if rel.suffix.lower() in ST.TEXT_EXT:
                new += ST._text_items(name, path.read_bytes())
            else:
                dest = unique_path(media / rel.name)
                shutil.move(path, dest)
                new.append(ST._media_item(dest, str(dest.relative_to(media)), "upload"))
            job.update(seen=1, added=len(new))
        else:
            stem = PurePosixPath(name).name
            for ext in sorted(ARCHIVES, key=len, reverse=True):
                if stem.lower().endswith(ext):
                    stem = stem[: -len(ext)]
                    break
            base = unique_path(media / (stem or "archive"))
            total = 0
            for rel_name, size, opener in _members(path, name):
                job["seen"] += 1
                rel = _keep(rel_name)
                if not rel:
                    job["skipped"] += 1
                    continue
                total += size
                if total > MAX_UNPACKED or job["added"] >= MAX_FILES:
                    raise ValueError(f"The archive holds more than {MAX_FILES} files or {MAX_UNPACKED >> 30} GB; split it")
                if rel.suffix.lower() in ST.TEXT_EXT:
                    with opener() as fh:
                        got = ST._text_items(rel.name, fh.read())
                    new += got
                    job["added"] += len(got)
                    continue
                parts = rel.parts[1:] if len(rel.parts) > 1 and rel.parts[0] == base.name else rel.parts  # zip of a folder
                dest = unique_path(base / Path(*parts))
                dest.parent.mkdir(parents=True, exist_ok=True)
                with opener() as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                try:
                    new.append(ST._media_item(dest, str(dest.relative_to(media)), "upload"))
                    job["added"] += 1
                except Exception:
                    dest.unlink(missing_ok=True)
                    job["skipped"] += 1
                if len(new) >= 500:   # long archives: the items appear while it runs
                    ST._add(tid, new)
                    new = []
        ST._add(tid, new)
        job["state"] = "done"
    except Exception as e:
        ST._add(tid, new)
        job.update(state="error", error=str(e)[:300])
    finally:
        shutil.rmtree(_dir(uid), ignore_errors=True)


def import_to_task(user: dict, uid: str, tid: str) -> dict:
    path, m = _finished(user, uid)
    if uid in _jobs:
        return _jobs[uid]
    _jobs[uid] = {"state": "running", "added": 0, "skipped": 0, "seen": 0, "error": "", "name": m["name"], "task": tid}
    threading.Thread(target=_import, args=(tid, uid, path, m["name"]), daemon=True).start()
    return _jobs[uid]


def job(uid: str) -> dict:
    if uid not in _jobs:
        raise KeyError(uid)
    return _jobs[uid]


def take(user: dict, uid: str) -> tuple[bytes, str]:
    """A small finished upload's bytes and name, removed from the upload folder (e.g. a prompts file)."""
    path, m = _finished(user, uid)
    if m["size"] > 20 << 20:
        raise ValueError("This file is too big for that")
    data = path.read_bytes()
    shutil.rmtree(_dir(uid), ignore_errors=True)
    return data, m["name"]
