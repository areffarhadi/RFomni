"""Requests to the administrators: "no default page fits my task" or "the page cannot do this yet".

Anyone signed in can write one; administrators see them all in the studio and answer within 12 hours (the studio
shows how much of that time is left), then make the default page that was missing. The answer shows in the
person's page chat and in their list of requests. Stored in workspace/page_requests.json.
"""
import threading
from datetime import datetime, timedelta, timezone

from .. import config
from ..util import new_id, now_iso, read_json, write_json

FILE = config.WORKSPACE / "page_requests.json"
ANSWER_WITHIN = timedelta(hours=12)
_lock = threading.Lock()


def _all() -> list:
    return read_json(FILE, {"requests": []})["requests"]


def _due(at: str) -> str:
    return (datetime.fromisoformat(at) + ANSWER_WITHIN).astimezone(timezone.utc).isoformat(timespec="seconds")


def add(user: dict, text: str, area: str = "", modality: str = "", ref: dict | None = None, contact: str = "") -> dict:
    text = str(text or "").strip()
    if len(text) < 10:
        raise ValueError("Please describe what you need in a sentence or two")
    with _lock:
        rs = _all()
        r = {"id": new_id("rq", 4), "at": now_iso(), "user": user["id"], "user_name": user.get("name") or "",
             "user_email": user.get("email") or "", "contact": str(contact or "").strip()[:200],
             "area": area, "modality": modality, "ref": ref or None, "text": text[:4000],
             "status": "open", "answer": "", "answered_at": None}
        r["due"] = _due(r["at"])
        rs.append(r)
        write_json(FILE, {"requests": rs[-2000:]})
        return r


def for_user(user: dict) -> list:
    """Open requests first, the oldest (most urgent) at the top; then the answered ones, newest first."""
    rs = _all() if user.get("role") == "admin" else [r for r in _all() if r["user"] == user["id"]]
    return (sorted((r for r in rs if r["status"] == "open"), key=lambda r: r["at"])
            + sorted((r for r in rs if r["status"] != "open"), key=lambda r: r["at"], reverse=True))


def for_ref(kind: str, oid: str) -> list:
    return [r for r in _all() if (r.get("ref") or {}).get("id") == oid and (r.get("ref") or {}).get("kind") == kind]


def answer(rid: str, text: str, status: str = "answered") -> dict:
    if status not in ("answered", "done", "open"):
        raise ValueError("status is answered, done or open")
    with _lock:
        rs = _all()
        for r in rs:
            if r["id"] == rid:
                if text.strip():
                    r["answer"] = text.strip()[:4000]
                    r["answered_at"] = now_iso()
                r["status"] = status
                write_json(FILE, {"requests": rs})
                return r
    raise KeyError(rid)


def open_count() -> int:
    return sum(1 for r in _all() if r["status"] == "open")
