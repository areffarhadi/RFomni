"""Project storage. Everything lives in plain folders so it is easy to inspect, back up and version:

workspace/projects/<project_id>/
    project.json                 name, description, selected task ids, settings
    media.json                   media items (uploaded copies or linked originals)
    media/<relative path>        uploaded files (folder structure preserved)
    annotations/<media_id>.json  tiers + segments for one file
    cache/<media_id>/            decoded audio, spectrogram, thumbnails (safe to delete)
"""
import re
import shutil
import threading
from collections import defaultdict
from pathlib import Path

from .util import new_id, now_iso, read_json, slugify, write_json

PID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
MID_RE = re.compile(r"^m[0-9a-f]{6,16}$")

DEFAULT_SETTINGS = {"low_confidence": 0.6}


class NotFound(Exception):
    pass


class Conflict(Exception):
    def __init__(self, current_rev):
        super().__init__(f"revision conflict (current rev {current_rev})")
        self.current_rev = current_rev


def empty_doc(mid: str) -> dict:
    return {"media_id": mid, "rev": 0, "tiers": [], "segments": [], "updated_at": None}


def _num(v):
    if v is None:
        return None
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def _geom(g):
    """Spatial annotation on an image, in coordinates normalised to the image size (0..1).
    box: {type, x, y, w, h} (top-left + size); keypoints: {type, points: [[x, y, v], ...], skeleton, box?}."""
    if not isinstance(g, dict):
        return None

    def f(v):
        return round(min(1.5, max(-0.5, float(v))), 5)

    try:
        if g.get("type") == "box":
            return {"type": "box", "x": f(g["x"]), "y": f(g["y"]), "w": max(0.0, f(g["w"])), "h": max(0.0, f(g["h"]))}
        if g.get("type") == "keypoints":
            pts = [[f(p[0]), f(p[1]), int(p[2]) if len(p) > 2 else 2] for p in g.get("points", [])]
            out = {"type": "keypoints", "points": pts, "skeleton": g.get("skeleton") or "coco17"}
            if g.get("box"):
                out["box"] = [f(v) for v in g["box"][:4]]
            if isinstance(g.get("names"), list):  # custom names (extra points beyond COCO-17)
                out["names"] = [str(x or "")[:40] for x in g["names"][: len(pts)]]
            if isinstance(g.get("edges"), list):  # connections edited by hand (else: COCO skeleton)
                seen, edges = set(), []
                for e in g["edges"]:
                    a, b = int(e[0]), int(e[1])
                    key = (min(a, b), max(a, b))
                    if a != b and 0 <= a < len(pts) and 0 <= b < len(pts) and key not in seen:
                        seen.add(key)
                        edges.append([a, b])
                out["edges"] = edges
            return out
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return None


def normalize_doc(doc: dict, mid: str) -> dict:
    tiers, seen = [], set()
    for t in doc.get("tiers", []):
        tid = str(t.get("id") or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        tiers.append({
            "id": tid,
            "task_id": t.get("task_id"),
            "name": str(t.get("name") or tid)[:60],
            "title": str(t.get("title") or t.get("name") or tid)[:120],
            "color": t.get("color") or "#94a3b8",
            "output": t.get("output") if t.get("output") in ("text", "label", "tags", "score", "boxes", "keypoints") else "text",
            "granularity": t.get("granularity") or "segment",
            "labels": [str(x) for x in (t.get("labels") or [])],
            "parent": t.get("parent"),
            "source": t.get("source"),
            "run": t.get("run"),
        })
    segs = []
    for s in doc.get("segments", []):
        if s.get("tier_id") not in seen:
            continue
        val = s.get("value")
        if isinstance(val, list):
            val = [str(x) for x in val]
        elif val is None:
            val = ""
        else:
            val = str(val)
        start, end = _num(s.get("start")), _num(s.get("end"))
        if start is not None and end is not None and end < start:
            start, end = end, start
        conf = s.get("confidence")
        try:
            conf = None if conf is None else max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = None
        seg = {
            "id": str(s.get("id") or new_id("s")),
            "tier_id": s["tier_id"],
            "start": start,
            "end": end,
            "value": val,
            "confidence": conf,
            "source": s.get("source") or "human",
            "reviewed": bool(s.get("reviewed", False)),
        }
        if s.get("meta"):
            seg["meta"] = s["meta"]
        geom = _geom(s.get("geom"))
        if geom:
            seg["geom"] = geom
        segs.append(seg)
    order = {t["id"]: i for i, t in enumerate(tiers)}
    segs.sort(key=lambda s: (order[s["tier_id"]], s["start"] if s["start"] is not None else -1, s["end"] or 0))
    return {"media_id": mid, "rev": int(doc.get("rev") or 0), "tiers": tiers, "segments": segs,
            "updated_at": doc.get("updated_at")}


def summarize(doc: dict, low: float) -> dict:
    tiers = {}
    for t in doc["tiers"]:
        tiers[t["id"]] = {"n": 0, "reviewed": 0, "low": 0, "task_id": t.get("task_id"), "source": t.get("source")}
    for s in doc["segments"]:
        d = tiers.get(s["tier_id"])
        if d is None:
            continue
        d["n"] += 1
        if s["reviewed"]:
            d["reviewed"] += 1
        elif s["confidence"] is not None and s["confidence"] < low:
            d["low"] += 1
    return {
        "tiers": tiers,
        "n_segments": sum(d["n"] for d in tiers.values()),
        "n_reviewed": sum(d["reviewed"] for d in tiers.values()),
        "n_low": sum(d["low"] for d in tiers.values()),
    }


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks = defaultdict(threading.RLock)
        self._glock = threading.Lock()

    # ---------- helpers ----------
    def lock(self, key) -> threading.RLock:
        with self._glock:
            return self._locks[key]

    def pdir(self, pid: str) -> Path:
        if not PID_RE.match(pid or ""):
            raise NotFound(pid)
        d = self.root / pid
        if not (d / "project.json").exists():
            raise NotFound(pid)
        return d

    def cache_dir(self, pid: str, mid: str) -> Path:
        d = self.pdir(pid) / "cache" / mid
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---------- projects ----------
    def list_projects(self) -> list:
        out = []
        for d in self.root.iterdir():
            p = read_json(d / "project.json") if d.is_dir() else None
            if p:
                p["stats"] = self.stats(p["id"])
                out.append(p)
        out.sort(key=lambda p: p.get("updated_at") or "", reverse=True)
        return out

    def create_project(self, name: str, description: str = "", tasks=None) -> dict:
        name = (name or "").strip() or "Untitled project"
        base = slugify(name)
        with self._glock:
            pid, i = base, 2
            while (self.root / pid).exists():
                pid = f"{base}-{i}"
                i += 1
            d = self.root / pid
            (d / "media").mkdir(parents=True)
            (d / "annotations").mkdir()
            proj = {
                "id": pid, "name": name, "description": description or "",
                "tasks": list(tasks or []), "settings": dict(DEFAULT_SETTINGS),
                "created_at": now_iso(), "updated_at": now_iso(),
            }
            write_json(d / "project.json", proj)
            write_json(d / "media.json", {"items": []})
        return proj

    def get_project(self, pid: str) -> dict:
        p = read_json(self.pdir(pid) / "project.json")
        p.setdefault("settings", {})
        for k, v in DEFAULT_SETTINGS.items():
            p["settings"].setdefault(k, v)
        return p

    def update_project(self, pid: str, patch: dict) -> dict:
        with self.lock(("p", pid)):
            p = self.get_project(pid)
            for k in ("name", "description", "tasks", "tier_order"):
                if k in patch and patch[k] is not None:
                    p[k] = patch[k]
            if isinstance(patch.get("settings"), dict):
                p["settings"].update(patch["settings"])
            p["updated_at"] = now_iso()
            write_json(self.pdir(pid) / "project.json", p)
            return p

    def touch(self, pid: str) -> None:
        with self.lock(("p", pid)):
            p = read_json(self.pdir(pid) / "project.json")
            p["updated_at"] = now_iso()
            write_json(self.pdir(pid) / "project.json", p)

    def delete_project(self, pid: str) -> None:
        d = self.pdir(pid).resolve()
        if d.parent != self.root.resolve():
            raise NotFound(pid)
        # Linked media live outside the project folder and are never touched.
        shutil.rmtree(d)

    def stats(self, pid: str) -> dict:
        items = self.list_media(pid)
        by_mod = {"audio": 0, "video": 0, "image": 0}
        dur = 0.0
        annotated = reviewed = segs = low = 0
        for m in items:
            by_mod[m["modality"]] = by_mod.get(m["modality"], 0) + 1
            dur += m.get("duration") or 0
            st = m.get("status", "new")
            if st in ("annotated", "in_review", "reviewed"):
                annotated += 1
            if st == "reviewed":
                reviewed += 1
            sm = m.get("summary") or {}
            segs += sm.get("n_segments", 0)
            low += sm.get("n_low", 0)
        return {"files": len(items), "by_modality": by_mod, "duration": round(dur, 2),
                "annotated": annotated, "reviewed": reviewed, "segments": segs, "low": low}

    # ---------- media ----------
    def list_media(self, pid: str) -> list:
        return read_json(self.pdir(pid) / "media.json", {"items": []})["items"]

    def _write_media(self, pid: str, items: list) -> None:
        write_json(self.pdir(pid) / "media.json", {"items": items})

    def add_media(self, pid: str, new_items: list) -> list:
        with self.lock(("p", pid)):
            items = self.list_media(pid)
            items.extend(new_items)
            self._write_media(pid, items)
        self.touch(pid)
        return new_items

    def get_media(self, pid: str, mid: str) -> dict:
        if not MID_RE.match(mid or ""):
            raise NotFound(mid)
        for m in self.list_media(pid):
            if m["id"] == mid:
                return m
        raise NotFound(mid)

    def update_media(self, pid: str, mid: str, patch: dict) -> dict:
        with self.lock(("p", pid)):
            items = self.list_media(pid)
            for m in items:
                if m["id"] == mid:
                    m.update(patch)
                    m["status"] = self._status(m)
                    self._write_media(pid, items)
                    return m
        raise NotFound(mid)

    def remove_media(self, pid: str, mid: str) -> None:
        with self.lock(("p", pid)):
            items = self.list_media(pid)
            item = next((m for m in items if m["id"] == mid), None)
            if item is None:
                raise NotFound(mid)
            self._write_media(pid, [m for m in items if m["id"] != mid])
        d = self.pdir(pid)
        if item.get("source") == "upload":
            f = (d / "media" / item["rel_path"]).resolve()
            if (d / "media").resolve() in f.parents and f.exists():
                f.unlink()
        (d / "annotations" / f"{mid}.json").unlink(missing_ok=True)
        shutil.rmtree(d / "cache" / mid, ignore_errors=True)

    def media_path(self, pid: str, item: dict) -> Path:
        if item.get("source") == "linked":
            return Path(item["abs_path"])
        return self.pdir(pid) / "media" / item["rel_path"]

    @staticmethod
    def _status(m: dict) -> str:
        if m.get("reviewed"):
            return "reviewed"
        sm = m.get("summary") or {}
        if sm.get("n_segments", 0) == 0:
            return "new"
        if sm.get("n_reviewed", 0) > 0:
            return "in_review"
        return "annotated"

    # ---------- annotations ----------
    def ann_path(self, pid: str, mid: str) -> Path:
        return self.pdir(pid) / "annotations" / f"{mid}.json"

    def load_doc(self, pid: str, mid: str) -> dict:
        self.get_media(pid, mid)
        doc = read_json(self.ann_path(pid, mid))
        return normalize_doc(doc, mid) if doc else empty_doc(mid)

    def save_doc(self, pid: str, mid: str, doc: dict, expected_rev=None) -> dict:
        with self.lock(("m", pid, mid)):
            cur = self.load_doc(pid, mid)
            if expected_rev is not None and int(expected_rev) != cur["rev"]:
                raise Conflict(cur["rev"])
            new = normalize_doc(doc, mid)
            new["rev"] = cur["rev"] + 1
            new["updated_at"] = now_iso()
            write_json(self.ann_path(pid, mid), new)
        low = self.get_project(pid)["settings"].get("low_confidence", 0.6)
        self.update_media(pid, mid, {"summary": summarize(new, low)})
        return new

    def refresh_summaries(self, pid: str) -> None:
        low = self.get_project(pid)["settings"].get("low_confidence", 0.6)
        for m in self.list_media(pid):
            doc = self.load_doc(pid, m["id"])
            self.update_media(pid, m["id"], {"summary": summarize(doc, low)})
