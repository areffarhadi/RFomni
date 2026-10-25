"""Data collection: projects that ask people to record data, e.g. sentences read in several emotions.

The owner defines prompts (sentences, per language) and conditions (e.g. emotions, each with a short acting hint),
the texts people see (instructions, consent) and the recording settings, and shares a link or lists the project on
the public Data collection page. People need no account: they agree to the consent, get a session ID, choose a
language and record each prompt in each condition. The page records clean 16-bit WAV (no noise suppression or
echo cancellation), lets them listen, trim and re-record, and uploads it. The owner listens, accepts or rejects,
and exports a ZIP (audio by language and condition, plus a metadata CSV).

Everything lives in workspace/collect/<id>/: project.json, sessions.json, recordings.jsonl (one line per
recording, the latest per session, prompt and condition counts) and audio/<language>/<condition>/*.wav.
"""
import csv
import io
import json
import re
import secrets
import threading
import wave
import zipfile
from pathlib import Path

from . import config
from .shared_tasks import new_session_id
from .util import new_id, now_iso, read_json, slugify, write_json

ROOT = config.WORKSPACE / "collect"
STATUSES = ("draft", "live", "paused", "closed")
THEMES = ("forest", "dark", "light")
INFO_FIELDS = {  # what people may be asked about themselves (all optional unless the owner requires them)
    "name": "Name or ID",
    "gender": "Gender",
    "age": "Age range",
    "first_language": "First language",
    "accent": "Accent or region",
}
AGE_RANGES = ["under 18", "18–24", "25–34", "35–44", "45–54", "55–64", "65 or older"]
GENDERS = ["female", "male", "non-binary", "prefer not to say"]
MAX_BYTES = 20 << 20

_lock = threading.RLock()


class NotFound(KeyError):
    pass


def default_design(title: str = "") -> dict:
    return {
        "title": title or "Help us collect speech",
        "summary": "",
        "instructions": "Find a quiet place and use headphones if you can. Read each sentence aloud in the way asked, then listen to it before you submit.",
        "consent": "I agree that my recordings and the information I give are stored and used to build a speech dataset for research.",
        "languages": [{"code": "en", "name": "English", "prompts": [{"id": "p1", "text": "The meeting starts at ten o'clock tomorrow."}]}],
        "conditions": [{"id": "neutral", "label": "Neutral", "hint": "Calm and plain, like reading a timetable."}],
        "look": {"theme": "forest", "accent": ""},
        "settings": {"sample_rate": 16000, "min_seconds": 1.0, "max_seconds": 15.0, "per_pair": 0,
                     "info": ["gender", "age", "first_language"], "info_required": [], "allow_skip": True,
                     "condition_order": "together"},
    }


def _num(v, lo, hi, default, cast=float):
    try:
        return max(lo, min(hi, cast(v)))
    except (TypeError, ValueError):
        return default


def validate_design(d: dict) -> dict:
    if not isinstance(d, dict):
        raise ValueError("The design must be an object")
    base = default_design()
    langs, seen = [], set()
    for lg in (d.get("languages") or [])[:40]:
        code = re.sub(r"[^A-Za-z0-9_-]", "", str(lg.get("code") or ""))[:12]
        if not code or code in seen:
            continue
        seen.add(code)
        prompts, pids = [], set()
        for i, pr in enumerate((lg.get("prompts") or [])[:500]):
            text = str(pr.get("text") if isinstance(pr, dict) else pr or "").strip()[:500]
            if not text:
                continue
            pid = re.sub(r"[^A-Za-z0-9_-]", "", str(pr.get("id") if isinstance(pr, dict) else "") or "")[:20] or f"p{i + 1}"
            while pid in pids:
                pid += "b"
            pids.add(pid)
            prompts.append({"id": pid, "text": text})
        langs.append({"code": code, "name": str(lg.get("name") or code).strip()[:40], "prompts": prompts})
    if not langs or not any(lg["prompts"] for lg in langs):
        raise ValueError("Add at least one language with a sentence to read")
    conds, cids = [], set()
    for c in (d.get("conditions") or [])[:12]:
        label = str(c.get("label") or "").strip()[:40]
        if not label:
            continue
        cid = slugify(str(c.get("id") or label), 20).replace("-", "_") or "c"
        while cid in cids:
            cid += "_2"
        cids.add(cid)
        conds.append({"id": cid, "label": label, "hint": str(c.get("hint") or "").strip()[:200]})
    if not conds:
        conds = base["conditions"]
    look = {**base["look"], **(d.get("look") or {})}
    st = {**base["settings"], **(d.get("settings") or {})}
    accent = str(look.get("accent") or "")
    info = [k for k in st.get("info") or [] if k in INFO_FIELDS]
    return {
        "title": str(d.get("title") or base["title"]).strip()[:120],
        "summary": str(d.get("summary") or "").strip()[:300],
        "instructions": str(d.get("instructions") or "").strip()[:4000],
        "consent": str(d.get("consent") or "").strip()[:4000],
        "languages": langs,
        "conditions": conds,
        "look": {"theme": look.get("theme") if look.get("theme") in THEMES else "forest",
                 "accent": accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else ""},
        "settings": {"sample_rate": 48000 if int(_num(st.get("sample_rate"), 8000, 48000, 16000, int)) >= 44100 else 16000,
                     "min_seconds": _num(st.get("min_seconds"), 0.2, 60, 1.0),
                     "max_seconds": _num(st.get("max_seconds"), 2, 300, 15.0),
                     # per_pair 0: everyone records every sentence in every condition
                     "per_pair": int(_num(st.get("per_pair"), 0, 1000, 0, int)),
                     "info": info, "info_required": [k for k in st.get("info_required") or [] if k in info],
                     "allow_skip": bool(st.get("allow_skip", True)),
                     "condition_order": "shuffled" if st.get("condition_order") == "shuffled" else "together"},
    }


# ----------------------------------------------------------------------------- projects
def _dir(cid: str) -> Path:
    if not re.fullmatch(r"dc[0-9a-f]{8}", cid or ""):
        raise NotFound(cid)
    d = ROOT / cid
    if not (d / "project.json").exists():
        raise NotFound(cid)
    return d


def get(cid: str) -> dict:
    return read_json(_dir(cid) / "project.json")


def _save(p: dict) -> dict:
    p["updated_at"] = now_iso()
    write_json(ROOT / p["id"] / "project.json", p)
    return p


def create(owner: dict, title: str = "", design: dict | None = None) -> dict:
    with _lock:
        cid = new_id("dc", 4)
        p = {"id": cid, "token": secrets.token_urlsafe(9), "owner": owner["id"], "owner_name": owner.get("name") or "",
             "status": "draft", "listed": False, "created_at": now_iso(),
             "design": validate_design(design) if design else default_design(title)}
        (ROOT / cid / "audio").mkdir(parents=True, exist_ok=True)
        return _save(p)


def update(cid: str, patch: dict) -> dict:
    with _lock:
        p = get(cid)
        if "design" in patch:
            p["design"] = validate_design(patch["design"])
        if patch.get("status"):
            if patch["status"] not in STATUSES:
                raise ValueError(f"status must be one of {', '.join(STATUSES)}")
            p["status"] = patch["status"]
        if "listed" in patch:
            p["listed"] = bool(patch["listed"])
        if patch.get("new_link"):
            p["token"] = secrets.token_urlsafe(9)
        return _save(p)


def delete(cid: str) -> None:
    import shutil
    d = _dir(cid)
    with _lock:
        shutil.rmtree(d)


def list_for(user: dict) -> list:
    out = []
    for f in sorted(ROOT.glob("dc*/project.json")):
        p = read_json(f)
        if p and (p["owner"] == user["id"] or user.get("role") == "admin"):
            out.append({**summary(p), "owned": p["owner"] == user["id"]})
    return sorted(out, key=lambda p: p.get("updated_at") or "", reverse=True)


def by_token(token: str) -> dict | None:
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", token):
        return None
    for f in ROOT.glob("dc*/project.json"):
        p = read_json(f)
        if p and secrets.compare_digest(p.get("token", ""), token):
            return p
    return None


def public_list() -> list:
    out = []
    for f in ROOT.glob("dc*/project.json"):
        p = read_json(f)
        if p and p.get("status") == "live" and p.get("listed"):
            sm = summary(p)
            d = p["design"]
            out.append({"token": p["token"], "title": d["title"], "summary": d.get("summary") or "",
                        "languages": [lg["name"] for lg in d["languages"]], "conditions": [c["label"] for c in d["conditions"]],
                        "prompts": sm["prompts"], "recordings": sm["recordings"], "people": sm["people"],
                        "minutes": sm["minutes"], "updated_at": p.get("updated_at")})
    return sorted(out, key=lambda p: p["updated_at"] or "", reverse=True)


# ----------------------------------------------------------------------------- sessions and recordings
def _sessions(cid: str) -> dict:
    return read_json(_dir(cid) / "sessions.json", {"sessions": {}})["sessions"]


def _save_sessions(cid: str, s: dict) -> None:
    write_json(_dir(cid) / "sessions.json", {"sessions": s})


def recordings(cid: str) -> list:
    """The latest recording per session, language, prompt and condition (a re-recording replaces the earlier one)."""
    f = _dir(cid) / "recordings.jsonl"
    if not f.exists():
        return []
    latest = {}
    for ln in f.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            latest[(r["session"], r["lang"], r["prompt"], r["condition"])] = r
    return [r for r in latest.values() if not r.get("deleted")]


def _append(cid: str, rec: dict) -> None:
    with open(_dir(cid) / "recordings.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def start_session(p: dict, info: dict) -> dict:
    st = p["design"]["settings"]
    clean = {}
    for k in st["info"]:
        v = str((info or {}).get(k) or "").strip()[:80]
        if not v and k in st["info_required"]:
            raise ValueError(f"Please fill in “{INFO_FIELDS[k]}”")
        if v:
            clean[k] = v
    with _lock:
        ss = _sessions(p["id"])
        sid = new_session_id()
        while sid in ss:
            sid = new_session_id()
        ss[sid] = {"id": sid, "info": clean, "consent_at": now_iso(), "started_at": now_iso(), "last_at": now_iso()}
        _save_sessions(p["id"], ss)
        return ss[sid]


def session(p: dict, sid: str) -> dict | None:
    return _sessions(p["id"]).get((sid or "").strip().upper())


def _pairs(p: dict, lang: str, sid: str) -> list:
    """Everything to record in a language, in order: each sentence in every condition (conditions in a fixed
    or, per person, shuffled order)."""
    d = p["design"]
    lg = next((x for x in d["languages"] if x["code"] == lang), None)
    if not lg:
        raise NotFound(lang)
    conds = list(d["conditions"])
    out = []
    for i, pr in enumerate(lg["prompts"]):
        cs = conds
        if d["settings"]["condition_order"] == "shuffled":
            import random
            cs = list(conds)
            random.Random(f"{sid}:{pr['id']}").shuffle(cs)
        out += [{"prompt": pr, "condition": c, "index": i} for c in cs]
    return out


def next_pair(p: dict, sid: str, lang: str) -> dict:
    """The next sentence and condition this person has not recorded or skipped (and, when only some recordings
    per pair are wanted, that still needs one); with progress in the language."""
    s = session(p, sid)
    if not s:
        raise NotFound(sid)
    pairs = _pairs(p, lang, s["id"])
    recs = recordings(p["id"])
    mine = {(r["prompt"], r["condition"]) for r in recs if r["session"] == s["id"] and r["lang"] == lang}
    skipped = set(map(tuple, (s.get("skipped") or {}).get(lang, [])))
    per = p["design"]["settings"]["per_pair"]
    counts = {}
    if per:
        for r in recs:
            if r["lang"] == lang and r.get("status") != "rejected":
                counts[(r["prompt"], r["condition"])] = counts.get((r["prompt"], r["condition"]), 0) + 1
    done = sum(1 for x in pairs if (x["prompt"]["id"], x["condition"]["id"]) in mine)
    for x in pairs:
        key = (x["prompt"]["id"], x["condition"]["id"])
        if key in mine or key in skipped or (per and counts.get(key, 0) >= per):
            continue
        return {"pair": x, "done": done, "total": len(pairs)}
    return {"pair": None, "done": done, "total": len(pairs)}


def skip(p: dict, sid: str, lang: str, prompt: str, condition: str) -> None:
    with _lock:
        ss = _sessions(p["id"])
        s = ss.get(sid)
        if not s:
            raise ValueError("This session was not found. Start a new one.")
        sk = s.setdefault("skipped", {}).setdefault(lang, [])
        if [prompt, condition] not in sk:
            sk.append([prompt, condition])
        _save_sessions(p["id"], ss)


def save_recording(p: dict, sid: str, lang: str, prompt: str, condition: str, wav: bytes, meta: dict) -> dict:
    """Check and store one recording (16-bit PCM WAV from the page); a new one for the same sentence and
    condition replaces this person's earlier one."""
    s = session(p, sid)
    if not s:
        raise ValueError("This session was not found. Start a new one.")
    pairs = {(x["prompt"]["id"], x["condition"]["id"]): x for x in _pairs(p, lang, s["id"])}
    if (prompt, condition) not in pairs:
        raise ValueError("This sentence or condition is not part of the project")
    if len(wav) > MAX_BYTES:
        raise ValueError("The recording is too large")
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    except (wave.Error, EOFError):
        raise ValueError("The recording could not be read. Please record it again.")
    dur = n / float(sr or 1)
    st = p["design"]["settings"]
    if sw != 2 or ch != 1:
        raise ValueError("Recordings must be 16-bit mono WAV")
    if dur < st["min_seconds"]:
        raise ValueError(f"The recording is shorter than {st['min_seconds']:g} s. Please record it again.")
    if dur > st["max_seconds"] + 0.5:
        raise ValueError(f"The recording is longer than {st['max_seconds']:g} s. Trim it or record it again.")
    rel = f"{lang}/{condition}/{s['id']}_{prompt}_{condition}.wav"
    out = _dir(p["id"]) / "audio" / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        out.write_bytes(wav)
        rec = {"id": new_id("r", 5), "session": s["id"], "lang": lang, "prompt": prompt, "condition": condition,
               "text": pairs[(prompt, condition)]["prompt"]["text"], "file": rel, "seconds": round(dur, 3),
               "sample_rate": sr, "at": now_iso(), "status": "new",
               "trim": meta.get("trim"), "client": str(meta.get("client") or "")[:120]}
        _append(p["id"], rec)
        ss = _sessions(p["id"])
        if s["id"] in ss:
            ss[s["id"]]["last_at"] = rec["at"]
            _save_sessions(p["id"], ss)
    return rec


def set_status(p: dict, rid: str, status: str) -> dict:
    if status not in ("new", "accepted", "rejected"):
        raise ValueError("status must be new, accepted or rejected")
    for r in recordings(p["id"]):
        if r["id"] == rid:
            with _lock:
                r = {**r, "status": status, "reviewed_at": now_iso()}
                _append(p["id"], r)
            return r
    raise NotFound(rid)


def audio_path(p: dict, rec: dict) -> Path:
    return _dir(p["id"]) / "audio" / rec["file"]


def find(p: dict, rid: str) -> dict:
    for r in recordings(p["id"]):
        if r["id"] == rid:
            return r
    raise NotFound(rid)


# ----------------------------------------------------------------------------- overview and export
def summary(p: dict) -> dict:
    d = p["design"]
    try:
        recs = recordings(p["id"])
        people = len(_sessions(p["id"]))
    except NotFound:
        recs, people = [], 0
    return {"id": p["id"], "title": d["title"], "status": p["status"], "listed": bool(p.get("listed")),
            "token": p["token"], "owner_name": p.get("owner_name", ""), "updated_at": p.get("updated_at"),
            "languages": len(d["languages"]), "conditions": len(d["conditions"]),
            "prompts": sum(len(lg["prompts"]) for lg in d["languages"]),
            "recordings": len(recs), "accepted": sum(r.get("status") == "accepted" for r in recs),
            "rejected": sum(r.get("status") == "rejected" for r in recs),
            "people": len({r["session"] for r in recs}), "sessions": people,
            "minutes": round(sum(r["seconds"] for r in recs) / 60, 1)}


def overview(p: dict) -> dict:
    """Counts per language and condition, and the people who took part."""
    d = p["design"]
    recs = recordings(p["id"])
    grid = {lg["code"]: {c["id"]: 0 for c in d["conditions"]} for lg in d["languages"]}
    for r in recs:
        if r["lang"] in grid and r["condition"] in grid[r["lang"]]:
            grid[r["lang"]][r["condition"]] += 1
    ss = _sessions(p["id"])
    people = []
    for s in ss.values():
        mine = [r for r in recs if r["session"] == s["id"]]
        people.append({"id": s["id"], "info": s.get("info") or {}, "recordings": len(mine),
                       "minutes": round(sum(r["seconds"] for r in mine) / 60, 1), "last_at": s.get("last_at")})
    return {"summary": summary(p), "grid": grid, "people": sorted(people, key=lambda x: x["last_at"] or "", reverse=True)}


def export_zip(p: dict, only_accepted: bool = False) -> bytes:
    recs = [r for r in recordings(p["id"]) if not only_accepted or r.get("status") == "accepted"]
    ss = _sessions(p["id"])
    names = {lg["code"]: lg["name"] for lg in p["design"]["languages"]}
    conds = {c["id"]: c["label"] for c in p["design"]["conditions"]}
    buf = io.BytesIO()
    meta = io.StringIO()
    w = csv.writer(meta)
    info_keys = list(INFO_FIELDS)
    w.writerow(["file", "language", "language_name", "prompt_id", "text", "condition", "condition_label", "seconds",
                "sample_rate", "speaker_session", *[f"speaker_{k}" for k in info_keys], "status", "recorded_at"])
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in sorted(recs, key=lambda r: (r["lang"], r["condition"], r["session"], r["prompt"])):
            z.write(audio_path(p, r), f"audio/{r['file']}")
            info = (ss.get(r["session"]) or {}).get("info") or {}
            w.writerow([f"audio/{r['file']}", r["lang"], names.get(r["lang"], r["lang"]), r["prompt"], r["text"],
                        r["condition"], conds.get(r["condition"], r["condition"]), r["seconds"], r["sample_rate"],
                        r["session"], *[info.get(k, "") for k in info_keys], r.get("status", "new"), r["at"]])
        z.writestr("metadata.csv", meta.getvalue())
        z.writestr("project.json", json.dumps(p["design"], ensure_ascii=False, indent=1))
    return buf.getvalue()
