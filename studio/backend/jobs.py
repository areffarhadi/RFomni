"""Background runs: apply a set of tasks to a set of files, one file at a time."""
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import media as media_mod
from . import tasks as task_reg
from .engine import AnnotateContext, UnsupportedTask, get_engine
from .util import new_id, now_iso

MODES = {
    "missing": "Only fill tiers that are still empty",
    "unreviewed": "Re-run tiers that have no reviewed or hand-edited segments",
    "overwrite": "Replace everything (including reviewed segments)",
}


def order_tasks(task_list: list) -> list:
    """Parents before children (asr -> forced_alignment -> phoneme)."""
    by_id = {t["id"]: t for t in task_list}
    done, out = set(), []

    def visit(t, depth=0):
        if t["id"] in done or depth > 10:
            return
        p = t.get("parent")
        if p and p in by_id:
            visit(by_id[p], depth + 1)
        done.add(t["id"])
        out.append(t)

    for t in task_list:
        visit(t)
    return out


def tier_from_task(task: dict, engine) -> dict:
    return {
        "id": task["id"], "task_id": task["id"], "name": task["name"], "title": task.get("title", task["name"]),
        "color": task.get("color"), "output": task["output"], "granularity": task.get("granularity"),
        "labels": task.get("labels", []), "parent": task.get("parent"),
        "source": engine.source, "run": {"engine": engine.name, "at": now_iso()},
    }


def test_task(store, pid: str, task: dict, media_ids=None, n: int = 3, seed: int = 0) -> dict:
    """Dry run of one task on a few files of the project: nothing is written to the annotations.
    Parent tasks (e.g. ASR for word alignment) are run in memory first when the file has none."""
    import random
    items = [m for m in store.list_media(pid) if m["modality"] in task.get("modalities", [])]
    pool = len(items)
    if media_ids:
        wanted = set(media_ids)
        items = [m for m in items if m["id"] in wanted]
    else:
        items = random.Random(seed).sample(items, min(max(1, int(n)), len(items)))
    engine = get_engine()
    all_tasks = task_reg.tasks_by_id()
    results = []
    for item in items:
        path = store.media_path(pid, item)
        doc = store.load_doc(pid, item["id"])
        cache_dir = store.cache_dir(pid, item["id"])
        ctx = AnnotateContext(item, path, doc,
                              audio_loader=lambda p=path, c=cache_dir, i=item: media_mod.get_audio((pid, i["id"]), p, c),
                              settings=store.get_project(pid).get("settings", {}))
        chain, parent = [], task.get("parent")
        while parent and parent in all_tasks and not ctx.tier_segments(parent) and len(chain) < 4:
            chain.insert(0, all_tasks[parent])
            parent = all_tasks[parent].get("parent")
        try:
            for t in chain:
                ctx.results[t["id"]] = engine.annotate(t, ctx)
            segs = engine.annotate(task, ctx)
            err = None
        except Exception as e:  # show the error next to the file instead of failing the whole test
            traceback.print_exc()
            segs, err = [], str(e)
        results.append({
            "media": {k: item.get(k) for k in ("id", "name", "rel_path", "modality", "duration", "width", "height")},
            "segments": segs[:300], "count": len(segs), "error": err,
        })
    return {"task": task, "prompt": task_reg.build_prompt(task), "engine": engine.info(),
            "pool": pool, "results": results}


class JobManager:
    def __init__(self, store):
        self.store = store
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omni-job")

    def start(self, pid: str, task_ids: list, media_ids, mode: str) -> dict:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {list(MODES)}")
        all_tasks = task_reg.tasks_by_id()
        tasks = [all_tasks[t] for t in task_ids if t in all_tasks]
        if not tasks:
            raise ValueError("No valid tasks selected")
        items = self.store.list_media(pid)
        if media_ids:
            wanted = set(media_ids)
            items = [m for m in items if m["id"] in wanted]
        if not items:
            raise ValueError("No files to annotate")
        engine = get_engine()
        job = {
            "id": new_id("j"), "project_id": pid, "status": "queued", "mode": mode,
            "engine": engine.info(), "task_ids": [t["id"] for t in tasks],
            "media_ids": [m["id"] for m in items], "total": len(items), "done": 0,
            "current": None, "errors": [], "skipped_tiers": 0, "written_tiers": 0,
            "updated_media": [], "created_at": now_iso(), "finished_at": None, "cancel": False,
        }
        with self.lock:
            self.jobs[job["id"]] = job
        self.pool.submit(self._run, job, tasks, engine)
        return self.public(job)

    @staticmethod
    def public(job: dict) -> dict:
        return {k: v for k, v in job.items() if k != "cancel"}

    def get(self, jid: str):
        job = self.jobs.get(jid)
        return self.public(job) if job else None

    def list(self, pid=None) -> list:
        jobs = [j for j in self.jobs.values() if pid is None or j["project_id"] == pid]
        jobs.sort(key=lambda j: j["created_at"], reverse=True)
        return [self.public(j) for j in jobs[:20]]

    def cancel(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if not job or job["status"] not in ("queued", "running"):
            return False
        job["cancel"] = True
        return True

    # -------------------------------------------------------------------------
    def _run(self, job: dict, tasks: list, engine) -> None:
        job["status"] = "running"
        pid = job["project_id"]
        tasks = order_tasks(tasks)
        try:
            settings = self.store.get_project(pid).get("settings", {})
            for mid in job["media_ids"]:
                if job["cancel"]:
                    job["status"] = "cancelled"
                    break
                try:
                    item = self.store.get_media(pid, mid)
                except Exception:
                    job["done"] += 1
                    continue
                job["current"] = item.get("name")
                try:
                    self._annotate_file(job, pid, item, tasks, engine, settings)
                except Exception as e:  # keep going with the next file
                    traceback.print_exc()
                    job["errors"].append({"media_id": mid, "name": item.get("name"), "error": str(e)})
                job["done"] += 1
            if job["status"] == "running":
                job["status"] = "done"
        except Exception as e:
            traceback.print_exc()
            job["status"] = "error"
            job["errors"].append({"error": str(e)})
        finally:
            job["current"] = None
            job["finished_at"] = now_iso()

    def _annotate_file(self, job, pid, item, tasks, engine, settings):
        mid = item["id"]
        path = self.store.media_path(pid, item)
        applicable = [t for t in tasks if item["modality"] in t.get("modalities", [])]
        if not applicable:
            return
        doc = self.store.load_doc(pid, mid)
        cache_dir = self.store.cache_dir(pid, mid)
        ctx = AnnotateContext(
            item, path, doc,
            audio_loader=lambda: media_mod.get_audio((pid, mid), path, cache_dir),
            settings=settings,
        )
        to_run = []
        for task in applicable:
            existing = [s for s in doc["segments"] if s["tier_id"] == task["id"]]
            if existing and job["mode"] == "missing":
                job["skipped_tiers"] += 1
                continue
            if existing and job["mode"] == "unreviewed" and any(
                    s.get("reviewed") or s.get("source") == "human" for s in existing):
                job["skipped_tiers"] += 1
                continue
            to_run.append(task)
        ctx.run_task_ids = {t["id"] for t in to_run}  # lets the engine answer several tasks in one call
        ctx.should_stop = lambda: job["cancel"]       # the engine stops between model calls
        new_tiers = {}
        for task in to_run:
            if job["cancel"]:
                break
            try:
                segs = engine.annotate(task, ctx)
            except UnsupportedTask as e:  # reported once per task, the other tasks still run
                if not any(err.get("task_id") == task["id"] for err in job["errors"]):
                    job["errors"].append({"task_id": task["id"], "error": str(e)})
                continue
            if job["cancel"]:  # a task cut short by Stop is not written
                break
            ctx.results[task["id"]] = segs
            new_tiers[task["id"]] = (task, segs)
        if not new_tiers:
            return
        # Merge under the file lock against the latest version (the editor may have saved meanwhile).
        with self.store.lock(("m", pid, mid)):
            doc = self.store.load_doc(pid, mid)
            tiers = list(doc["tiers"])
            tier_idx = {t["id"]: i for i, t in enumerate(tiers)}
            segments = [s for s in doc["segments"] if s["tier_id"] not in new_tiers]
            for tid, (task, segs) in new_tiers.items():
                tier = tier_from_task(task, engine)
                if tid in tier_idx:
                    tiers[tier_idx[tid]] = tier
                else:
                    tiers.append(tier)
                for s in segs:
                    segments.append({
                        "id": new_id("s"), "tier_id": tid, "start": s.get("start"), "end": s.get("end"),
                        "value": s.get("value", ""), "confidence": s.get("confidence"),
                        "source": engine.source, "reviewed": False, **({"meta": s["meta"]} if s.get("meta") else {}),
                        **({"geom": s["geom"]} if s.get("geom") else {}),
                    })
                job["written_tiers"] += 1
            doc["tiers"], doc["segments"] = tiers, segments
            self.store.save_doc(pid, mid, doc)
        job["updated_media"].append(mid)
        if engine.name == "mock":
            time.sleep(0.05)  # keeps the UI progress readable on tiny files with the mock engine
