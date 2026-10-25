"""Task writer: turns what the user wants to label into a task (prompt, classes, output, granularity).

Used by the "New prompt task" form ("Write it for me" / "Improve") and by the assistant when it
proposes or revises a task. The model writes in the style of the built-in tasks, which are the
prompts the annotation model answers best: one short question, a small clear class list, and
nothing about the output format (the studio appends that to every prompt).
"""
import json

from .. import tasks as task_reg
from ..engine.omni import parse_json

FIELDS = ("name", "prompt", "output", "granularity", "modalities", "labels", "kind", "score_min", "score_max", "window_sec")

RULES = """You write annotation tasks for RFomni, the annotation model, which listens to audio and watches video and images.
Return JSON only, with these keys:
  name: short tier name, 1 to 3 words
  prompt: the instruction for the model
  output: label (exactly one class) | tags (zero or more classes) | score (a number in a range) | text (free answer) |
          boxes (rectangles around things in still images only)
  granularity: segment (one answer per speech region: what someone says or how they say it) | file (one answer per file) |
               window (one answer per fixed piece of time, for states that change, e.g. what is on screen) |
               event (find every timed occurrence, e.g. a laugh or a door slam; use output label, one class per occurrence)
  modalities: list of audio, video, image (video files have sound; images have no time, so image tasks use file)
  labels: the classes (for label, tags, boxes; empty for score and text)
  kind: annotation, or captioning for free descriptions
  score_min, score_max: only for score
  window_sec: only for window
  note: one short sentence to the user about a choice you made or a question to settle (or "")

A good prompt is short and specific: one question or instruction, usually under 25 words, that says exactly what to judge
and on what evidence (voice, words, face, scene). Match the granularity ("in this segment", "in this recording", "in this
image"). Never put the output format, JSON or the class list in the prompt: the studio adds them.
Good classes are short lowercase words, mutually exclusive for label, together covering every case (2 to 8 of them).
Add "other", "none" or "unclear" when real data can fit none of them. When the user asks to add or tell apart
something, make it a class of its own (a yes/no task becomes a class list). Keep the user's own classes and wording when they gave
them, only fixing what would confuse the model. Prefer label over text when the user wants to count or compare answers.
"""


def _examples(n_max: int = 7) -> str:
    """A few built-in tasks of different shapes, live from the registry (edited classes included)."""
    want = ["ser", "sentiment", "topic", "image_tags", "mos", "nonverbal", "fer", "accent"]
    by_id = task_reg.tasks_by_id()
    rows = []
    for tid in want:
        t = by_id.get(tid)
        if t:
            rows.append(json.dumps({k: t[k] for k in FIELDS if k in t and t[k] not in (None, [], "")}, ensure_ascii=False))
    return "\n".join(rows[:n_max])


def _messages(request: str, draft: dict | None, context: str) -> list:
    system = f"{RULES}\nBuilt-in tasks written this way:\n{_examples()}"
    if draft:
        cur = json.dumps({k: draft[k] for k in FIELDS if k in draft and draft[k] not in (None, [], "")}, ensure_ascii=False)
        user = (f"Current task:\n{cur}\n\nChange it as the user asks, keeping what they did not mention. When they "
                f"ask to add something, it becomes a new class (a yes/no task turns into a list of classes that "
                f"tells the cases apart, plus none). Keep the prompt and the classes consistent: the prompt asks "
                f"about all the classes, and a catch-all class is a plain word such as neutral, none or other. "
                f"The user asks: "
                f"{request or 'make the prompt and classes clear, short and complete'}")
    else:
        user = f"The user wants a task for this: {request}"
    if context:
        user = f"{context}\n\n{user}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user + "\nJSON:"}]


def _fix(task: dict) -> dict:
    """Mend the slips the model makes most: lists given as text, rectangles asked of sound, timed events
    without a class output, classes on outputs that have none."""
    for k in ("labels", "modalities"):
        if isinstance(task.get(k), str):
            task[k] = [x.strip() for x in task[k].split(",") if x.strip()]
    mods = task.get("modalities") or []
    if task.get("output") in ("boxes", "keypoints") and "image" not in mods:
        task["output"] = "label"
    if task.get("granularity") == "event" and task.get("output") not in ("label", "tags", "text"):
        task["output"] = "label"
    if task.get("output") not in ("label", "tags", "boxes"):
        task["labels"] = []
    task["labels"] = list(dict.fromkeys(str(x).strip() for x in task.get("labels") or [] if str(x).strip()))
    return task


def write(request: str, draft: dict | None = None, context: str = "") -> dict:
    """{"task": <a valid task draft>, "note": str}. Raises ValueError when the model's draft is unusable."""
    from .brain import complete  # late: brain imports actions, which use this module
    messages = _messages(request.strip(), draft, context)
    base = {k: v for k, v in (draft or {}).items() if k in FIELDS}
    problem = "it did not return JSON"
    for _ in range(2):  # one retry, told what was wrong
        raw = complete(messages, 420, 0.2)
        obj = parse_json(raw)
        if isinstance(obj, dict):
            try:
                task = task_reg.validate_task(_fix({**base, **{k: obj[k] for k in FIELDS if k in obj}}))
                return {"task": {k: task[k] for k in FIELDS if k in task}, "note": str(obj.get("note") or "").strip()[:300]}
            except ValueError as e:
                problem = str(e)
        messages = [*messages, {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That task is not valid: {problem}. Return the corrected JSON only."}]
    raise ValueError(f"The model could not write a valid task ({problem}). Try describing it in other words.")
