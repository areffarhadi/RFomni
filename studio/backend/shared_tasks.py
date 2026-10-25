"""Shared tasks: data that needs human labels, a page designed for the people who label it, and a link.

The owner (signed in) uploads or links data (images, video, audio or text), designs the page (instructions,
questions, look, how many people label each item) and shares a link. People who open the link need no account:
they get a session ID (e.g. K7Q2-M9XD), which they can type in again later to continue, and are given items one
at a time. Answers are kept per session; the owner sees progress and agreement and exports the results.

Everything lives in workspace/shared/<id>/: task.json (design, status, link token), items.json, sessions.json
and answers.jsonl (one line per answer, append-only).
"""
import csv
import io
import json
import re
import secrets
import threading
import time
from collections import Counter
from pathlib import Path

from . import config
from . import media as media_mod
from .util import new_id, now_iso, read_json, safe_rel_path, slugify, unique_path, write_json

ROOT = config.WORKSPACE / "shared"
TEXT_EXT = {".txt", ".csv", ".tsv", ".jsonl", ".json"}
DATA_EXT = config.MEDIA_EXT | TEXT_EXT
FIELD_TYPES = ("choice", "multi", "rating", "text")
STATUSES = ("draft", "live", "paused", "closed")
THEMES = ("forest", "dark", "light")
LEASE_SEC = 600            # an item handed to one person is not handed to another for this long
MAX_TEXT = 20000           # characters kept per text item

_lock = threading.RLock()
_leases: dict = {}         # (task id, item id) -> (session id, until)


class NotFound(KeyError):
    pass


# ----------------------------------------------------------------------------- design
def default_design(title: str = "") -> dict:
    return {
        "title": title or "Help us label this data",
        "instructions": "Look at each item and answer the question. There are no wrong answers; go with your first impression.",
        "fields": [{"id": "label", "type": "choice", "label": "Which option fits best?",
                    "options": ["yes", "no", "not sure"], "required": True}],
        "intro_fields": [],
        "look": {"theme": "forest", "accent": "", "layout": "side", "dir": "ltr", "ui_lang": "en"},
        "settings": {"labels_per_item": 1, "max_per_session": 0, "allow_skip": True, "shuffle": True,
                     "ask_name": False, "group_label": "Group", "must_listen": False, "no_repeat": False,
                     "allow_back": False, "final_comment": False},
    }


def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def validate_design(d: dict) -> dict:
    """A clean design: unknown keys dropped, sizes bounded, at least one question."""
    if not isinstance(d, dict):
        raise ValueError("The design must be an object")
    base = default_design()
    fields, seen = [], set()
    for f in (d.get("fields") or [])[:12]:
        if not isinstance(f, dict):
            continue
        typ = f.get("type") if f.get("type") in FIELD_TYPES else "choice"
        label = str(f.get("label") or "").strip()[:300]
        if not label:
            continue
        fid = slugify(str(f.get("id") or label), 24).replace("-", "_") or "q"
        while fid in seen:
            fid += "_2"
        seen.add(fid)
        out = {"id": fid, "type": typ, "label": label, "required": bool(f.get("required", typ != "text")),
               "help": str(f.get("help") or "").strip()[:300]}
        if typ in ("choice", "multi"):
            opts = list(dict.fromkeys(str(o).strip()[:60] for o in f.get("options") or [] if str(o).strip()))[:20]
            if len(opts) < 2:
                raise ValueError(f"“{label}” needs at least two options")
            out["options"] = opts
        elif typ == "rating":
            lo = _int(f.get("min"), 0, 10, 1)
            hi = _int(f.get("max"), lo + 1, 11, max(lo + 1, 5))
            out.update(min=lo, max=hi, min_label=str(f.get("min_label") or "").strip()[:40],
                       max_label=str(f.get("max_label") or "").strip()[:40])
        else:
            out.update(multiline=bool(f.get("multiline", True)), placeholder=str(f.get("placeholder") or "").strip()[:120])
        fields.append(out)
    if not fields:
        raise ValueError("The page needs at least one question")
    intro = []
    for f in (d.get("intro_fields") or [])[:8]:  # questions people answer once, before they start (stored per session)
        label = str(f.get("label") or "").strip()[:200]
        if not label:
            continue
        fid = slugify(str(f.get("id") or label), 24).replace("-", "_") or "info"
        typ = "choice" if f.get("type") == "choice" else "text"
        opts = list(dict.fromkeys(str(o).strip()[:60] for o in f.get("options") or [] if str(o).strip()))[:20]
        if typ == "choice" and len(opts) < 2:
            typ = "text"
        intro.append({"id": fid, "label": label, "type": typ, "options": opts if typ == "choice" else [],
                      "required": bool(f.get("required", False))})
    look = {**base["look"], **(d.get("look") or {})}
    accent = str(look.get("accent") or "")
    st = {**base["settings"], **(d.get("settings") or {})}
    return {
        "title": str(d.get("title") or base["title"]).strip()[:120],
        "summary": str(d.get("summary") or "").strip()[:300],
        "instructions": str(d.get("instructions") or "").strip()[:4000],
        "fields": fields,
        "intro_fields": intro,
        "look": {"theme": look.get("theme") if look.get("theme") in THEMES else "forest",
                 "accent": accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else "",
                 "layout": "stacked" if look.get("layout") == "stacked" else "side",
                 "dir": "rtl" if look.get("dir") == "rtl" else "ltr",
                 "ui_lang": look.get("ui_lang") if look.get("ui_lang") in ("en", "fa") else "en"},
        # labels_per_item 0: everyone labels every item (each person goes through all of them)
        "settings": {"labels_per_item": _int(st.get("labels_per_item"), 0, 50, 1),
                     "max_per_session": _int(st.get("max_per_session"), 0, 100000, 0),
                     "allow_skip": bool(st.get("allow_skip", True)), "shuffle": bool(st.get("shuffle", True)),
                     "ask_name": bool(st.get("ask_name", False)),
                     "group_label": str(st.get("group_label") or "Group").strip()[:40],
                     # listening tests: rate only after hearing the clip to the end; never the same sentence twice in
                     # a row; go back and change an earlier answer; a comment at the end
                     "must_listen": bool(st.get("must_listen", False)), "no_repeat": bool(st.get("no_repeat", False)),
                     "allow_back": bool(st.get("allow_back", False)), "final_comment": bool(st.get("final_comment", False))},
    }


# ----------------------------------------------------------------------------- tasks
def _dir(tid: str) -> Path:
    if not re.fullmatch(r"st[0-9a-f]{8}", tid or ""):
        raise NotFound(tid)
    d = ROOT / tid
    if not (d / "task.json").exists():
        raise NotFound(tid)
    return d


def get(tid: str) -> dict:
    return read_json(_dir(tid) / "task.json")


def _save(task: dict) -> dict:
    task["updated_at"] = now_iso()
    write_json(ROOT / task["id"] / "task.json", task)
    return task


KINDS = ("labelling", "listening")


def create(owner: dict, title: str = "", kind: str = "labelling") -> dict:
    with _lock:
        tid = new_id("st", 4)
        design = default_design(title)
        if kind == "listening":
            design["settings"].update(labels_per_item=0, allow_skip=False, must_listen=True, no_repeat=True, allow_back=True,
                                      final_comment=True)
            design["look"]["layout"] = "stacked"
            design["fields"] = [{"id": "naturalness", "type": "rating", "label": "How natural does the voice sound?",
                                 "min": 1, "max": 5, "min_label": "bad", "max_label": "excellent", "required": True}]
        task = {"id": tid, "token": secrets.token_urlsafe(9), "owner": owner["id"], "owner_name": owner.get("name") or "",
                "kind": kind if kind in KINDS else "labelling",
                "status": "draft", "created_at": now_iso(), "design": validate_design(design), "chat": []}
        (ROOT / tid / "media").mkdir(parents=True, exist_ok=True)
        write_json(ROOT / tid / "items.json", {"items": []})
        return _save(task)


def list_for(user: dict, kind: str | None = None) -> list:
    out = []
    for f in sorted(ROOT.glob("st*/task.json")):
        t = read_json(f)
        if kind and t and t.get("kind", "labelling") != kind:
            continue
        if t and (t["owner"] == user["id"] or user.get("role") == "admin"):
            out.append({**summary(t), "owned": t["owner"] == user["id"]})
    return sorted(out, key=lambda t: t.get("updated_at") or "", reverse=True)


def update(tid: str, patch: dict) -> dict:
    with _lock:
        task = get(tid)
        if "design" in patch:
            task["design"] = validate_design(patch["design"])
        if patch.get("status"):
            if patch["status"] not in STATUSES:
                raise ValueError(f"status must be one of {', '.join(STATUSES)}")
            if patch["status"] == "live" and not items(tid):
                raise ValueError("Add some data before sharing the link")
            task["status"] = patch["status"]
        if patch.get("new_link"):
            task["token"] = secrets.token_urlsafe(9)
        if "listed" in patch:
            task["listed"] = bool(patch["listed"])
        return _save(task)


def delete(tid: str) -> None:
    import shutil
    d = _dir(tid)
    with _lock:
        shutil.rmtree(d)


def by_token(token: str) -> dict | None:
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", token):
        return None
    for f in ROOT.glob("st*/task.json"):
        t = read_json(f)
        if t and secrets.compare_digest(t.get("token", ""), token):
            return t
    return None


def public_list(kind: str = "labelling") -> list:
    """Live tasks of a kind their owners chose to list on the public page (Task sharing, or Subjective tests)."""
    out = []
    for f in ROOT.glob("st*/task.json"):
        t = read_json(f)
        if t and t.get("status") == "live" and t.get("listed") and t.get("kind", "labelling") == kind:
            sm = summary(t)
            out.append({"token": t["token"], "title": t["design"]["title"], "summary": t["design"].get("summary") or "",
                        "owner_name": t.get("owner_name") or "", "by_kind": sm["by_kind"], "items": sm["items"],
                        "complete": sm["complete"], "people": sm["people"], "answers": sm["answers"],
                        "groups": len(groups(t["id"])), "group_label": t["design"]["settings"].get("group_label") or "Group",
                        "everyone": t["design"]["settings"]["labels_per_item"] == 0, "updated_at": t.get("updated_at"),
                        "ui_lang": t["design"]["look"].get("ui_lang", "en"),
                        "scales": [f["label"] for f in t["design"]["fields"] if f["type"] == "rating"]})
    return sorted(out, key=lambda t: t["updated_at"] or "", reverse=True)


def any_live() -> bool:
    return any((read_json(f) or {}).get("status") == "live" for f in ROOT.glob("st*/task.json"))


# ----------------------------------------------------------------------------- items
def items(tid: str) -> list:
    return read_json(_dir(tid) / "items.json", {"items": []})["items"]


def real_items(tid: str) -> list:
    """The items that count: everything but practice items (shown first to each person, never scored)."""
    return [i for i in items(tid) if not i.get("practice")]


def groups(tid: str) -> list:
    """The groups items are sorted into (e.g. languages), in first-seen order; empty when there are none."""
    return list(dict.fromkeys(i["group"] for i in items(tid) if i.get("group")))


def item(tid: str, iid: str) -> dict:
    for it in items(tid):
        if it["id"] == iid:
            return it
    raise NotFound(iid)


def _add(tid: str, new: list) -> int:
    with _lock:
        cur = items(tid)
        cur.extend(new)
        write_json(_dir(tid) / "items.json", {"items": cur})
    return len(new)


def clear_items(tid: str) -> None:
    with _lock:
        write_json(_dir(tid) / "items.json", {"items": []})


def remove_item(tid: str, iid: str) -> None:
    with _lock:
        write_json(_dir(tid) / "items.json", {"items": [i for i in items(tid) if i["id"] != iid]})


def _text_items(name: str, raw: bytes) -> list:
    """Text data: .txt is one item per line (or the whole file when it is one paragraph), .csv / .tsv use the
    text column (text, sentence, content, utterance or transcript; else the first column), .jsonl / .json
    use "text". Other columns are kept as the item's info."""
    text = raw.decode("utf-8-sig", errors="replace")
    ext = Path(name).suffix.lower()
    rows = []
    if ext == ".txt":
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        rows = [{"text": ln} for ln in lines] if len(lines) > 1 else [{"text": text.strip()}]
    elif ext in (".csv", ".tsv"):
        reader = csv.DictReader(io.StringIO(text), delimiter="\t" if ext == ".tsv" else ",")
        cols = reader.fieldnames or []
        key = next((c for c in cols if c and c.strip().lower() in ("text", "sentence", "content", "utterance", "transcript")),
                   cols[0] if cols else None)
        for r in reader:
            if key and (r.get(key) or "").strip():
                rows.append({"text": r[key], "info": {k: v for k, v in r.items() if k and k != key and v}})
    else:
        try:
            data = json.loads(text) if ext == ".json" else [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        except json.JSONDecodeError as e:
            raise ValueError(f"{name}: not valid JSON ({e})")
        for r in data if isinstance(data, list) else [data]:
            if isinstance(r, str):
                rows.append({"text": r})
            elif isinstance(r, dict) and str(r.get("text") or "").strip():
                rows.append({"text": str(r["text"]), "info": {k: v for k, v in r.items() if k != "text" and isinstance(v, (str, int, float))}})
    out = []
    for i, r in enumerate(rows):
        info = {k: str(v)[:200] for k, v in (r.get("info") or {}).items()}
        label = info.get("id") or (f"{name} #{i + 1}" if len(rows) > 1 else name)
        out.append({"id": new_id("i", 5), "kind": "text", "name": label, "text": r["text"].strip()[:MAX_TEXT],
                    "info": info, "source": name})
    return out


def _media_item(path: Path, rel: str, source: str) -> dict:
    meta = media_mod.probe(path)
    it = {"id": new_id("i", 5), "kind": meta["modality"], "name": path.name, "rel_path": rel, "source": source,
          "duration": meta.get("duration"), "playable": meta.get("playable", True),
          "width": meta.get("width"), "height": meta.get("height")}
    if source == "linked":
        it["abs_path"] = str(path.resolve())
    return it


def add_upload(tid: str, filename: str, rel_path: str | None, data: bytes) -> int:
    rel = safe_rel_path(rel_path or filename or "file")
    ext = Path(rel.name).suffix.lower()
    if ext not in DATA_EXT:
        raise ValueError(f"Unsupported file type: {rel.name}")
    if ext in TEXT_EXT:
        return _add(tid, _text_items(rel.name, data))
    d = _dir(tid) / "media"
    dest = unique_path(d / Path(*rel.parts))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    try:
        return _add(tid, [_media_item(dest, str(dest.relative_to(d)), "upload")])
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise ValueError(f"Could not read {rel.name}: {e}")


def link_folder(tid: str, path: str) -> dict:
    """Add the data files of a folder on the server without copying them (text files are read in)."""
    root = Path(str(path)).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"There is no folder {root}")
    files = [root] if root.is_file() else sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in DATA_EXT
        and not any(part.startswith(".") for part in p.relative_to(root).parts))
    have = {i.get("abs_path") for i in items(tid)}
    base = root.parent
    new, errors = [], []
    for p in files[:50000]:
        if str(p) in have:
            continue
        try:
            if p.suffix.lower() in TEXT_EXT:
                new += _text_items(p.name, p.read_bytes())
            else:
                new.append(_media_item(p, str(p.relative_to(base)), "linked"))
        except Exception as e:
            errors.append({"file": str(p), "error": str(e)[:200]})
    _add(tid, new)
    return {"added": len(new), "errors": errors[:20], "folder": str(root)}


def item_path(tid: str, it: dict) -> Path:
    if it.get("abs_path"):
        return Path(it["abs_path"])
    return _dir(tid) / "media" / it["rel_path"]


def cache_dir(tid: str, iid: str) -> Path:
    d = _dir(tid) / "cache" / iid
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_summary(tid: str) -> dict:
    its = items(tid)
    kinds = Counter(i["kind"] for i in its)
    gs = Counter(i["group"] for i in its if i.get("group"))
    return {"total": len(its), "by_kind": dict(kinds), "groups": dict(gs.most_common(60)),
            "examples": [i["name"] for i in its[:5]],
            "text_examples": [i["text"][:160] for i in its if i["kind"] == "text"][:3],
            "info_columns": sorted({k for i in its[:200] for k in (i.get("info") or {})})[:12]}


# ----------------------------------------------------------------------------- sessions and answers
def _sessions(tid: str) -> dict:
    return read_json(_dir(tid) / "sessions.json", {"sessions": {}})["sessions"]


def _save_sessions(tid: str, s: dict) -> None:
    write_json(_dir(tid) / "sessions.json", {"sessions": s})


def _answers(tid: str, practice: bool = False) -> list:
    """The latest answer of each person to each item (an earlier one is replaced when they go back and change
    it); practice answers only when asked for."""
    f = _dir(tid) / "answers.jsonl"
    if not f.exists():
        return []
    latest = {}
    for ln in f.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            a = json.loads(ln)
            latest[(a["session"], a["item"])] = a
    return [a for a in latest.values() if practice or not a.get("practice")]


def new_session_id() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O or 1/I, easy to read out and type
    s = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{s[:4]}-{s[4:]}"


def start_session(task: dict, name: str = "", info: dict | None = None) -> dict:
    clean = {}
    for f in task["design"].get("intro_fields") or []:
        v = str((info or {}).get(f["id"]) or "").strip()[:120]
        if f["type"] == "choice" and v and v not in f["options"]:
            v = ""
        if not v and f["required"]:
            raise ValueError(f"Please answer “{f['label']}”")
        if v:
            clean[f["id"]] = v
    with _lock:
        ss = _sessions(task["id"])
        sid = new_session_id()
        while sid in ss:
            sid = new_session_id()
        ss[sid] = {"id": sid, "name": name.strip()[:60], "info": clean, "started_at": now_iso(), "last_at": now_iso(),
                   "done": 0, "skipped": [], "history": []}
        _save_sessions(task["id"], ss)
        return ss[sid]


def session(task: dict, sid: str) -> dict | None:
    return _sessions(task["id"]).get((sid or "").strip().upper())


def _counts(tid: str) -> tuple:
    per_item, by_session = Counter(), {}
    for a in _answers(tid):
        per_item[a["item"]] += 1
        by_session.setdefault(a["session"], set()).add(a["item"])
    return per_item, by_session


def next_item(task: dict, sid: str, group: str | None = None) -> dict | None:
    """The next item for this person (in their chosen group, when the task has groups): one that still needs
    labels, that they have not answered or skipped, and that nobody else is working on right now; the
    least-labelled first. When everyone labels every item, only this person's own answers count."""
    tid, st = task["id"], task["design"]["settings"]
    everyone = st["labels_per_item"] == 0
    with _lock:
        s = session(task, sid)
        if not s:
            return None
        if st["max_per_session"] and s["done"] >= st["max_per_session"]:
            return None
        per_item, by_session = _counts(tid)
        done_practice = {a["item"] for a in _answers(tid, practice=True) if a["session"] == s["id"] and a.get("practice")}
        for it in items(tid):  # practice items first, in their order, once per person
            if it.get("practice") and it["id"] not in done_practice:
                return it
        mine = by_session.get(s["id"], set()) | set(s.get("skipped") or [])
        now = time.time()
        held = set() if everyone else {iid for (t, iid), (who, until) in _leases.items() if t == tid and who != s["id"] and until > now}
        # an item this person was already given and has not answered comes back first
        for (t, iid), (who, until) in _leases.items():
            if t == tid and who == s["id"] and until > now and iid not in mine:
                try:
                    it = item(tid, iid)
                    if not group or it.get("group") == group:
                        return it
                except NotFound:
                    pass
        cands = [i for i in real_items(tid) if i["id"] not in mine and i["id"] not in held
                 and (not group or i.get("group") == group)
                 and (everyone or per_item[i["id"]] < st["labels_per_item"])]
        if not cands:
            return None
        if not everyone:
            low = min(per_item[i["id"]] for i in cands)
            cands = [i for i in cands if per_item[i["id"]] == low]
        if st.get("no_repeat") and s.get("history"):  # not the same sentence (stimulus) as the one just before
            try:
                last = item(tid, s["history"][-1]).get("stimulus")
            except NotFound:
                last = None
            other = [i for i in cands if last is None or i.get("stimulus") != last]
            cands = other or cands
        pick = secrets.choice(cands) if st["shuffle"] else cands[0]
        _leases[(tid, pick["id"])] = (s["id"], now + LEASE_SEC)
        return pick


def _check_values(task: dict, values: dict) -> dict:
    out = {}
    for f in task["design"]["fields"]:
        v = values.get(f["id"])
        if f["type"] == "choice":
            v = v if v in f["options"] else None
        elif f["type"] == "multi":
            v = [o for o in f["options"] if o in (v or [])] or None
        elif f["type"] == "rating":
            try:
                v = float(v)
                v = v if f["min"] <= v <= f["max"] else None
            except (TypeError, ValueError):
                v = None
        else:
            v = str(v or "").strip()[:5000] or None
        if v is None and f["required"]:
            raise ValueError(f"Please answer “{f['label']}”")
        if v is not None:
            out[f["id"]] = v
    return out


def answer(task: dict, sid: str, iid: str, values: dict, ms: int | None = None, plays: int | None = None) -> dict:
    """Store an answer; answering an item again (going back) replaces this person's earlier answer."""
    tid = task["id"]
    it = item(tid, iid)
    clean = _check_values(task, values if isinstance(values, dict) else {})
    with _lock:
        ss = _sessions(tid)
        s = ss.get(sid)
        if not s:
            raise ValueError("This session was not found. Start a new one.")
        rec = {"item": iid, "session": sid, "name": s.get("name", ""), "values": clean, "at": now_iso(),
               "ms": int(ms) if isinstance(ms, (int, float)) else None,
               **({"plays": int(plays)} if isinstance(plays, (int, float)) else {}),
               **({"practice": True} if it.get("practice") else {})}
        with open(_dir(tid) / "answers.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        hist = s.setdefault("history", [])
        if iid not in hist:
            hist.append(iid)
            if not it.get("practice"):
                s["done"] += 1
        s["last_at"] = rec["at"]
        _save_sessions(tid, ss)
        _leases.pop((tid, iid), None)
        return s


def skip(task: dict, sid: str, iid: str) -> None:
    if not task["design"]["settings"]["allow_skip"]:
        raise ValueError("Skipping is turned off for this task")
    with _lock:
        ss = _sessions(task["id"])
        s = ss.get(sid)
        if not s:
            raise ValueError("This session was not found. Start a new one.")
        s["skipped"] = list(dict.fromkeys((s.get("skipped") or []) + [iid]))[-5000:]
        _save_sessions(task["id"], ss)
        _leases.pop((task["id"], iid), None)


def previous(task: dict, sid: str, iid: str) -> dict:
    """An item this person already answered, with their answer (to go back to it)."""
    s = session(task, sid)
    if not s or iid not in (s.get("history") or []):
        raise NotFound(iid)
    vals = next((a["values"] for a in _answers(task["id"], practice=True) if a["session"] == s["id"] and a["item"] == iid), {})
    return {"item": item(task["id"], iid), "values": vals}


def save_comment(task: dict, sid: str, text: str) -> None:
    with _lock:
        ss = _sessions(task["id"])
        if sid not in ss:
            raise ValueError("This session was not found.")
        ss[sid]["comment"] = str(text or "").strip()[:4000]
        _save_sessions(task["id"], ss)


def set_key(tid: str, text: str) -> dict:
    """The blind key of a listening test: which system made each clip ("clip,system" lines; the clip by its file
    name, with or without extension, or its item id). Only the owner sees it; results are then given per system."""
    rows = [r for r in csv.reader(io.StringIO(text or "")) if len(r) >= 2 and r[0].strip()
            and r[0].strip().lower() not in ("clip", "file", "item", "id", "name", "filename")]  # a header line
    key = {r[0].strip(): r[1].strip() for r in rows if r[1].strip()}
    with _lock:
        its = items(tid)
        n = 0
        for i in its:
            stem = Path(i["name"]).stem
            sys_ = key.get(i["id"]) or key.get(i["name"]) or key.get(stem)
            if sys_:
                i["system"] = sys_[:60]
                n += 1
        write_json(_dir(tid) / "items.json", {"items": its})
    return {"matched": n, "rows": len(key), "systems": sorted({v for v in key.values()})}


def progress(task: dict, sid: str | None = None, group: str | None = None) -> dict:
    """Items and how many are complete (in the group, when given); with a session, what this person did.
    When everyone labels every item, "complete" is what this person has done."""
    per_item, by_session = _counts(task["id"])
    need = task["design"]["settings"]["labels_per_item"]
    its = [i for i in real_items(task["id"]) if not group or i.get("group") == group]
    s = session(task, sid) if sid else None
    mine = by_session.get(s["id"], set()) if s else set()
    if need == 0:
        done = sum(1 for i in its if i["id"] in mine)
    else:
        done = sum(1 for i in its if per_item[i["id"]] >= need)
    out = {"items": len(its), "complete": done}
    if s:
        out["mine"] = s["done"]
        out["mine_here"] = sum(1 for i in its if i["id"] in mine)
        out["limit"] = task["design"]["settings"]["max_per_session"]
    return out


def group_progress(task: dict, sid: str | None = None) -> list:
    """Per group: items, items complete, and (with a session) how many this person did."""
    per_item, by_session = _counts(task["id"])
    need = task["design"]["settings"]["labels_per_item"]
    s = session(task, sid) if sid else None
    mine = by_session.get(s["id"], set()) if s else set()
    out = {}
    for i in real_items(task["id"]):
        g = i.get("group")
        if not g:
            continue
        r = out.setdefault(g, {"name": g, "items": 0, "complete": 0, "mine": 0})
        r["items"] += 1
        r["mine"] += i["id"] in mine
        r["complete"] += (i["id"] in mine) if need == 0 else per_item[i["id"]] >= need
    return list(out.values())


# ----------------------------------------------------------------------------- results
def summary(task: dict) -> dict:
    tid = task["id"]
    try:
        its = real_items(tid)
    except NotFound:
        its = []
    per_item, by_session = _counts(tid)
    need = task["design"]["settings"]["labels_per_item"] or 1  # everyone labels: an item counts once someone did
    return {"id": tid, "title": task["design"]["title"], "status": task["status"], "token": task["token"],
            "listed": bool(task.get("listed")), "kind": task.get("kind", "labelling"),
            "owner_name": task.get("owner_name", ""), "updated_at": task.get("updated_at"),
            "items": len(its), "by_kind": dict(Counter(i["kind"] for i in its)),
            "complete": sum(1 for i in its if per_item[i["id"]] >= need),
            "answers": sum(per_item.values()), "people": len(by_session)}


def _mean_ci(vals: list) -> dict:
    """Mean with a 95% confidence interval (normal approximation, as MOS results are usually given)."""
    n = len(vals)
    if not n:
        return {"n": 0, "mean": None, "ci95": None}
    m = sum(vals) / n
    sd = (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5 if n > 1 else 0.0
    return {"n": n, "mean": round(m, 3), "ci95": round(1.96 * sd / n ** 0.5, 3) if n > 1 else None, "sd": round(sd, 3)}


def _keep(task: dict, where: dict | None) -> set | None:
    """Sessions whose answers to the listener questions match `where` (e.g. {"native": "yes"}); None = all."""
    if not where:
        return None
    return {sid for sid, s in _sessions(task["id"]).items()
            if all((s.get("info") or {}).get(k) == v for k, v in where.items())}


def results(task: dict, where: dict | None = None) -> dict:
    """Per question: the answer counts, and per item the majority answer and how many agreed. Rating questions
    also get a mean with a 95% confidence interval, overall and, once the blind key is in, per system.
    `where` keeps only the people whose answers to the listener questions match (e.g. native speakers)."""
    tid = task["id"]
    fields = task["design"]["fields"]
    keep = _keep(task, where)
    answers = [a for a in _answers(tid) if keep is None or a["session"] in keep]
    by_item: dict = {}
    for a in answers:
        by_item.setdefault(a["item"], []).append(a)
    dist = {f["id"]: Counter() for f in fields}
    agree = {f["id"]: [] for f in fields if f["type"] in ("choice", "rating")}
    rows = []
    systems: dict = {}
    for it in real_items(tid):
        aa = by_item.get(it["id"], [])
        if it.get("system"):
            for f in fields:
                if f["type"] == "rating":
                    systems.setdefault(it["system"], {}).setdefault(f["id"], []).extend(
                        float(a["values"][f["id"]]) for a in aa if a["values"].get(f["id"]) is not None)
        row = {"id": it["id"], "name": it["name"], "kind": it["kind"], "answers": len(aa), "majority": {}}
        for f in fields:
            vals = [a["values"].get(f["id"]) for a in aa if a["values"].get(f["id"]) is not None]
            for v in vals:
                for x in (v if isinstance(v, list) else [v]):
                    dist[f["id"]][str(x)] += 1
            if vals and f["type"] in ("choice", "rating"):
                top, n = Counter(map(str, vals)).most_common(1)[0]
                row["majority"][f["id"]] = top
                if len(vals) > 1:
                    agree[f["id"]].append(n / len(vals))
        rows.append(row)
    ss = _sessions(tid)
    ratings = {f["id"]: [float(a["values"][f["id"]]) for a in answers if a["values"].get(f["id"]) is not None]
               for f in fields if f["type"] == "rating"}
    return {"summary": summary(task), "where": where or {},
            "fields": [{"id": f["id"], "label": f["label"], "type": f["type"],
                        "counts": dist[f["id"]].most_common(30) if f["type"] != "text" else [],
                        "texts": sum(dist[f["id"]].values()) if f["type"] == "text" else None,
                        "agreement": round(sum(agree[f["id"]]) / len(agree[f["id"]]), 3) if agree.get(f["id"]) else None,
                        **({"mos": _mean_ci(ratings[f["id"]])} if f["type"] == "rating" else {})} for f in fields],
            "systems": [{"system": name, **{fid: _mean_ci(v) for fid, v in by.items()}} for name, by in sorted(systems.items())],
            "keyed": sum(1 for i in real_items(tid) if i.get("system")),
            "intro_fields": task["design"].get("intro_fields") or [],
            "sessions": sorted(({"id": s["id"], "name": s.get("name", ""), "info": s.get("info") or {}, "done": s["done"],
                                 "comment": s.get("comment", ""), "started_at": s["started_at"], "last_at": s["last_at"],
                                 "kept": keep is None or s["id"] in keep} for s in ss.values()),
                               key=lambda s: s["last_at"], reverse=True),
            "items": rows}


def export_csv(task: dict) -> str:
    fields = task["design"]["fields"]
    names = {i["id"]: i for i in items(task["id"])}
    buf = io.StringIO()
    w = csv.writer(buf)
    intro = [f["id"] for f in task["design"].get("intro_fields") or []]
    ss = _sessions(task["id"])
    w.writerow(["item_id", "item", "kind", "group", "system", "stimulus", "session", "name", *[f"listener_{k}" for k in intro],
                "answered_at", "seconds", "plays", *[f["id"] for f in fields]])
    for a in _answers(task["id"]):
        it = names.get(a["item"], {})
        vals = [a["values"].get(f["id"]) for f in fields]
        info = (ss.get(a["session"]) or {}).get("info") or {}
        w.writerow([a["item"], it.get("name", ""), it.get("kind", ""), it.get("group", ""), it.get("system", ""), it.get("stimulus", ""),
                    a["session"], a.get("name", ""), *[info.get(k, "") for k in intro], a["at"],
                    round(a["ms"] / 1000, 1) if a.get("ms") else "", a.get("plays", ""),
                    *["; ".join(v) if isinstance(v, list) else ("" if v is None else v) for v in vals]])
    return buf.getvalue()


def export_jsonl(task: dict) -> str:
    """One line per item: the item, every answer, and the majority per choice / rating question."""
    res = results(task)
    by_item: dict = {}
    for a in _answers(task["id"]):
        by_item.setdefault(a["item"], []).append({k: a.get(k) for k in ("session", "name", "values", "at", "ms")})
    out = []
    names = {i["id"]: i for i in items(task["id"])}
    for row in res["items"]:
        it = names[row["id"]]
        rec = {"id": row["id"], "item": it["name"], "kind": it["kind"], "group": it.get("group"), "majority": row["majority"],
               "answers": by_item.get(row["id"], [])}
        if it["kind"] == "text":
            rec["text"] = it["text"]
        else:
            rec["path"] = it.get("abs_path") or it.get("rel_path")
        out.append(json.dumps(rec, ensure_ascii=False))
    return "\n".join(out) + ("\n" if out else "")
