"""The labeling plan: what the user and the assistant agreed on, one file per user.

The plan is the assistant's memory. A conversation anywhere (the studio panel, the chat
page, voice) reads and updates the same plan, so a plan made in a chat can be picked up in
the studio later. Every turn and every approved action is also appended to a log, which
is the material for evaluating (and, later, fine-tuning) the assistant.
"""
import json
import re
import threading
import time

from .. import config
from ..util import now_iso, read_json, write_json

AGENT_DIR = config.WORKSPACE / "agent"
STEPS = ["project", "data", "tasks", "test", "run", "review"]
HISTORY_TURNS = 16         # user + assistant messages kept for the prompt
PROPOSAL_TTL = 15 * 60     # an unanswered proposal expires after this many seconds

_lock = threading.Lock()


def user_key(user: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_.@-]", "_", (user or "local").strip())[:80] or "local"


def _path(user: str):
    return AGENT_DIR / "plans" / f"{user_key(user)}.json"


def new_plan() -> dict:
    return {
        "step": "project",
        "project_id": None,
        "tasks": [],          # task ids agreed for this project (the run uses these)
        "mode": "missing",    # run mode
        "notes": [],          # decisions worth remembering ("only the German files", ...)
        "pending": None,      # the proposal waiting for approval
        "job_id": None,       # the run the assistant started
        "last_test": None,    # short summary of the last sample test
        "history": [],
        "updated_at": now_iso(),
    }


def load(user: str) -> dict:
    plan = read_json(_path(user), None)
    if not plan:
        return new_plan()
    base = new_plan()
    base.update(plan)
    if base["pending"] and time.time() - base["pending"].get("ts", 0) > PROPOSAL_TTL:
        base["pending"] = None
    return base


def save(user: str, plan: dict) -> dict:
    plan["history"] = plan["history"][-HISTORY_TURNS:]
    plan["updated_at"] = now_iso()
    with _lock:
        _path(user).parent.mkdir(parents=True, exist_ok=True)
        write_json(_path(user), plan)
    return plan


def reset(user: str) -> dict:
    return save(user, new_plan())


def remember(plan: dict, role: str, text: str) -> None:
    if text:
        plan["history"].append({"role": role, "text": text[:600]})


def log(user: str, event: dict) -> None:
    f = AGENT_DIR / "log" / f"{user_key(user)}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now_iso(), **event}, ensure_ascii=False) + "\n")


def public(plan: dict) -> dict:
    """What the panel shows (no history, no internal timestamps)."""
    return {k: plan[k] for k in ("step", "project_id", "tasks", "mode", "notes", "job_id", "last_test")} | {
        "pending": {k: v for k, v in plan["pending"].items() if k != "ts"} if plan.get("pending") else None}
