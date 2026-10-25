"""Page designer for shared tasks: the owner describes the task in a chat and the model designs the page.

Each message goes to the model with the current design, what the data looks like and the last few messages;
the model answers with a short reply and the whole new design, which the studio shows in the live preview at
once (a design is a draft until the owner puts the link live). A server folder named in the message is linked
first, so the design can fit the data.
"""
import json
import os
import re

from .. import shared_tasks as ST
from ..engine.omni import parse_json

RULES = """You design the web page that volunteers use to label data for a shared labeling task. They need no
account; they see one item at a time (an image, a video, an audio clip or a piece of text) and answer the questions.
Return JSON only: {"reply": "...", "design": {...}}
  reply: 1 to 3 plain sentences to the task owner: what you set up or changed, and one question if something important
         is unclear (e.g. which classes, how many people per item). No markdown.
  design: the whole page, with these keys:
    title: short title the volunteers see
    instructions: what to do and how to decide, for someone who has never done this, in 2 to 6 short sentences
                  (plain text, line breaks allowed); include the definitions of the classes when they are not obvious
    fields: the questions, usually 1 to 3. Each is one of
        {"id", "type": "choice", "label", "options": [...], "required": true}   one answer from a list
        {"id", "type": "multi", "label", "options": [...], "required": false}   any number from a list
        {"id", "type": "rating", "label", "min": 1, "max": 5, "min_label", "max_label", "required": true}
        {"id", "type": "text", "label", "placeholder", "multiline": true, "required": false}   free text, e.g. a transcript
      Optional "help": one short hint under the question. Labels are short questions; options are short, do not
      overlap, cover every case, and include "not sure" or "other" when real data may fit none.
    look: {"theme": "forest" | "dark" | "light", "accent": "" or "#rrggbb", "layout": "side" (media left, questions
          right) | "stacked" (questions under the media; better for text and for phones)}
    settings: {"labels_per_item": how many different people label each item (1, or 3 to 5 when agreement matters),
               "max_per_session": items per person, 0 = no limit, "allow_skip": true, "shuffle": true,
               "ask_name": ask volunteers for a name or ID before they start}
A yes/no question is a "choice" with the options yes and no (not "multi"). Use layout "stacked" when the data is
text, "side" for images and video. Keep everything the owner did not ask to change. Write for the volunteers, not
for the owner.
"""


def _data_line(tid: str) -> str:
    d = ST.data_summary(tid)
    if not d["total"]:
        return "Data: none yet (the owner can upload files or name a folder on the server)."
    kinds = ", ".join(f"{n} {k}" for k, n in d["by_kind"].items())
    parts = [f"Data: {d['total']} items ({kinds}); e.g. {', '.join(d['examples'][:3])}."]
    if d["text_examples"]:
        parts.append("Text examples: " + " | ".join(d["text_examples"]))
    if d["info_columns"]:
        parts.append("Extra columns: " + ", ".join(d["info_columns"]))
    return " ".join(parts)


def _folders(text: str) -> list:
    """Server folders named in the message (absolute or ~/ paths that exist)."""
    out = []
    for m in re.finditer(r"(?<![\w/])(~?/[^\s'\"`,;]+)", text):
        p = os.path.expanduser(m.group(1).rstrip(".)"))
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def chat(task: dict, message: str, may_link: bool) -> dict:
    """One message from the owner: {"reply", "design", "linked": [...]}; the task is saved."""
    from .brain import complete  # late: brain imports actions, which import the app
    tid = task["id"]
    linked = []
    for folder in _folders(message):
        if not may_link:
            linked.append({"folder": folder, "error": "Only an administrator can add data from server folders."})
            continue
        try:
            linked.append(ST.link_folder(tid, folder))
        except ValueError as e:
            linked.append({"folder": folder, "error": str(e)})
    notes = [f"Just added {r['added']} items from {r['folder']}." for r in linked if "added" in r]
    notes += [f"Could not add {r['folder']}: {r['error']}" for r in linked if "error" in r]

    history = task.get("chat") or []
    convo = [f"{m['role'].capitalize()}: {m['text']}" for m in history[-6:]]
    system = "\n".join([RULES, "", _data_line(tid), *notes, "",
                        "Current design:", json.dumps(task["design"], ensure_ascii=False)])
    user = "\n".join([*convo, f"Owner: {message}", "JSON:"])
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    design, reply, problem = None, "", "it did not return JSON"
    for _ in range(2):  # one retry, told what was wrong
        raw = complete(messages, 1100, 0.2)
        obj = parse_json(raw)
        if isinstance(obj, dict) and isinstance(obj.get("design"), dict):
            try:
                design = ST.validate_design({**task["design"], **obj["design"]})
                reply = str(obj.get("reply") or "").strip()[:800]
                break
            except ValueError as e:
                problem = str(e)
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": f"That design is not valid: {problem}. Return the corrected JSON only."}]
    if design is None:
        reply = f"I could not turn that into a page ({problem}). Could you say it another way?"
    else:
        task = ST.update(tid, {"design": design})
    reply = " ".join([*notes, reply]).strip()
    task = ST.get(tid)
    task["chat"] = (history + [{"role": "owner", "text": message[:2000]}, {"role": "assistant", "text": reply}])[-40:]
    ST._save(task)
    return {"reply": reply, "design": task["design"], "linked": linked}
