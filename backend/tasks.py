"""Task registry: built-in tasks (config/tasks.json) + user-created prompt tasks (workspace)."""
import re
import threading

from . import config
from .util import new_id, read_json, slugify, write_json

KINDS = {"annotation", "captioning"}
OUTPUTS = {"text", "label", "tags", "score", "boxes", "keypoints"}
SPATIAL = {"boxes", "keypoints"}
COCO_KEYPOINTS = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
                  "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
                  "left_knee", "right_knee", "left_ankle", "right_ankle"]
GRANULARITIES = {"file", "segment", "window", "event", "word", "phone"}
MODALITIES = {"audio", "video", "image"}

_lock = threading.Lock()
_builtin_cache = {"mtime": None, "data": None}


def _builtin_raw() -> dict:
    mtime = config.BUILTIN_TASKS_FILE.stat().st_mtime
    if _builtin_cache["mtime"] != mtime:
        _builtin_cache["data"] = read_json(config.BUILTIN_TASKS_FILE, {"tasks": [], "categories": [], "presets": []})
        _builtin_cache["mtime"] = mtime
    return _builtin_cache["data"]


def categories() -> list:
    return _builtin_raw().get("categories", []) + [{"id": "custom", "name": "My prompt tasks"}]


def presets() -> list:
    return _builtin_raw().get("presets", [])


# Fields of a built-in task the user may change; output type and granularity stay fixed
# (to change those, duplicate the task as a new prompt task).
OVERRIDABLE = ["name", "title", "description", "prompt", "labels", "color", "window_sec", "score_min", "score_max"]


def _overrides() -> dict:
    return read_json(config.TASK_OVERRIDES_FILE, {"tasks": {}}).get("tasks", {})


def builtin_tasks() -> list:
    """Built-in tasks with the user's changes applied. A changed task carries `modified`,
    `modified_fields` and `default` (the original values) so the UI can show and reset them."""
    ov = _overrides()
    out = []
    for t in _builtin_raw().get("tasks", []):
        o = ov.get(t["id"]) or {}
        changed = [k for k in OVERRIDABLE if k in o and o[k] != t.get(k)]
        task = {**t, **{k: o[k] for k in changed}, "builtin": True, "modified": bool(changed)}
        if changed:
            task["modified_fields"] = changed
            task["modified_at"] = o.get("updated_at")
            task["default"] = {k: t.get(k) for k in OVERRIDABLE if k in t}
        out.append(task)
    return out


def builtin_default(task_id: str):
    return next((t for t in _builtin_raw().get("tasks", []) if t["id"] == task_id), None)


def save_override(task_id: str, raw: dict) -> dict:
    """Change classes / prompt / names of a built-in task. Only differences from the default are stored."""
    base = builtin_default(task_id)
    if base is None:
        raise KeyError(task_id)
    merged = validate_task({**base, **{k: raw[k] for k in OVERRIDABLE if k in raw}, "open_labels": not base.get("labels")})
    diff = {k: merged[k] for k in OVERRIDABLE if k in merged and merged[k] != base.get(k)}
    with _lock:
        data = read_json(config.TASK_OVERRIDES_FILE, {"tasks": {}})
        if diff:
            from .util import now_iso
            data.setdefault("tasks", {})[task_id] = {**diff, "updated_at": now_iso()}
        else:
            data.setdefault("tasks", {}).pop(task_id, None)
        write_json(config.TASK_OVERRIDES_FILE, data)
    return get_task(task_id)


def reset_override(task_id: str) -> dict:
    if builtin_default(task_id) is None:
        raise KeyError(task_id)
    with _lock:
        data = read_json(config.TASK_OVERRIDES_FILE, {"tasks": {}})
        data.setdefault("tasks", {}).pop(task_id, None)
        write_json(config.TASK_OVERRIDES_FILE, data)
    return get_task(task_id)


def custom_tasks() -> list:
    data = read_json(config.CUSTOM_TASKS_FILE, {"tasks": []})
    return [{**t, "builtin": False} for t in data.get("tasks", [])]


def all_tasks() -> list:
    return builtin_tasks() + custom_tasks()


def tasks_by_id() -> dict:
    return {t["id"]: t for t in all_tasks()}


def get_task(task_id: str):
    return tasks_by_id().get(task_id)


def validate_task(raw: dict) -> dict:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("Task name is required")
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Prompt is required")
    output = raw.get("output", "text")
    if output not in OUTPUTS:
        raise ValueError(f"output must be one of {sorted(OUTPUTS)}")
    gran = raw.get("granularity", "file")
    if gran not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {sorted(GRANULARITIES)}")
    kind = raw.get("kind", "annotation")
    if kind not in KINDS:
        raise ValueError("kind must be 'annotation' or 'captioning'")
    if output in SPATIAL:  # rectangles / keypoints live on still images
        gran = "file"
        raw = {**raw, "modalities": ["image"]}
    mods = [m for m in raw.get("modalities", []) if m in MODALITIES]
    if not mods:
        raise ValueError("Select at least one modality")
    labels = [str(l).strip() for l in raw.get("labels", []) if str(l).strip()]
    if output in ("label",) and not labels and not raw.get("open_labels"):
        raise ValueError("A label task needs at least one label")
    color = raw.get("color") or "#a78bfa"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#a78bfa"
    task = {
        "name": name[:40],
        "title": str(raw.get("title") or name).strip()[:80],
        "kind": kind,
        "category": "custom",
        "modalities": mods,
        "granularity": gran,
        "output": output,
        "labels": labels,
        "color": color,
        "description": str(raw.get("description") or "").strip()[:400],
        "prompt": prompt,
    }
    if gran == "window":
        try:
            task["window_sec"] = max(0.5, min(600.0, float(raw.get("window_sec", 5))))
        except (TypeError, ValueError):
            task["window_sec"] = 5.0
    if output == "score":
        task["score_min"] = float(raw.get("score_min", 1))
        task["score_max"] = float(raw.get("score_max", 5))
    return task


def save_custom_task(raw: dict, task_id: str | None = None) -> dict:
    task = validate_task(raw)
    with _lock:
        data = read_json(config.CUSTOM_TASKS_FILE, {"tasks": []})
        items = data.setdefault("tasks", [])
        if task_id:
            for i, t in enumerate(items):
                if t["id"] == task_id:
                    task["id"] = task_id
                    task["created_at"] = t.get("created_at")
                    items[i] = task
                    break
            else:
                raise KeyError(task_id)
        else:
            existing = {t["id"] for t in all_tasks()}
            base = "c_" + slugify(task["name"], 24).replace("-", "_")
            tid = base
            while tid in existing:
                tid = f"{base}_{new_id(nbytes=2)}"
            task["id"] = tid
            from .util import now_iso
            task["created_at"] = now_iso()
            items.append(task)
        write_json(config.CUSTOM_TASKS_FILE, data)
    return {**task, "builtin": False}


def delete_custom_task(task_id: str) -> None:
    with _lock:
        data = read_json(config.CUSTOM_TASKS_FILE, {"tasks": []})
        before = len(data.get("tasks", []))
        data["tasks"] = [t for t in data.get("tasks", []) if t["id"] != task_id]
        if len(data["tasks"]) == before:
            raise KeyError(task_id)
        write_json(config.CUSTOM_TASKS_FILE, data)


def output_spec(task: dict) -> str:
    """The structured-output instruction appended to every prompt (kept here so the
    future model adapter and the UI preview use the exact same text)."""
    out, gran = task.get("output", "text"), task.get("granularity", "file")
    labels = task.get("labels") or []
    lab = ", ".join(labels)
    if out == "boxes":
        extra = f" Labels: [{lab}]." if labels else ""
        return ('Return only JSON: {"boxes": [{"label": "<label>", "box_2d": [x1, y1, x2, y2], "confidence": <0-1>}, ...]} '
                f"with one item per instance and coordinates scaled to 0-1000.{extra}")
    if out == "keypoints":
        return ('Return only JSON: {"people": [{"box_2d": [x1, y1, x2, y2], "keypoints": [[x, y, v], ...]}, ...]} '
                f"with the 17 COCO keypoints in this order: {', '.join(COCO_KEYPOINTS)}; coordinates scaled to 0-1000; "
                "v = 2 visible, 1 occluded, 0 not in the image.")
    if gran in ("word", "phone", "event"):
        unit = task.get("unit") or gran  # e.g. "speaker turn" for diarization
        val = {"text": '"<text>"', "label": '"<one label>"', "tags": '["<tag>", ...]', "score": "<number>"}[out]
        extra = f" Labels: [{lab}]." if labels and out in ("label", "tags") else ""
        return (f"Return only JSON: {{\"items\": [{{\"start\": <sec>, \"end\": <sec>, \"value\": {val}}}, ...]}}"
                f" with one item per {unit}.{extra}")
    if out == "label":
        if labels:
            return f'Choose exactly one of [{lab}]. Return only JSON: {{"label": "<label>", "confidence": <0-1>}}'
        return 'Return only JSON: {"label": "<label>", "confidence": <0-1>}'
    if out == "tags":
        if labels:
            return f'Choose zero or more of [{lab}]. Return only JSON: {{"tags": ["<tag>", ...]}}'
        return 'Return only JSON: {"tags": ["<tag>", ...]}'
    if out == "score":
        lo, hi = task.get("score_min", 1), task.get("score_max", 5)
        return f'Return only JSON: {{"score": <number from {lo:g} to {hi:g}>}}'
    return 'Return only JSON: {"text": "<answer>"}'


def build_prompt(task: dict) -> str:
    prompt = task.get("prompt", "").strip()
    prompt = prompt.replace("{labels}", ", ".join(task.get("labels") or []))
    return f"{prompt}\n\n{output_spec(task)}"
