"""One conversational turn of the Omni Studio assistant.

Each turn makes two short calls to the thinker, because that is how it works best:
  1. decide: JSON only, {"action", "args"}: which single action, if any, follows from the
             user's message. A few examples anchor the format. The decision is checked in
             code (known action, arguments the action accepts).
  2. speak:  a plain-text answer for the user, written knowing what was decided, so it asks
             for approval instead of claiming the work is done.
An act never runs here: it becomes a proposal that runs only after the user approves it.

The workflow step is computed from the project state (no project -> project, no files ->
data, ...), not chosen by the model, and every action checks its own prerequisites, so the
order holds even when the model wanders. Stop, approve and reject are recognised without
the model, so they always work.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..engine import omni
from ..engine.omni import parse_json
from . import actions as A
from . import drafter
from . import knowledge as K
from . import plan as P

MAX_PROMPT_CHARS = int(os.environ.get("OMNI_AGENT_MAX_PROMPT_CHARS", "30000"))  # thinker context is 16k tokens

STOP_RE = re.compile(r"^\W*(please\s+)?(stop|cancel|abort|kill|halt|enough)(\s+(it|that|this|the run|everything|now|please))*\W*$", re.I)
YES_RE = re.compile(r"^\W*(yes|yeah|yep|sure|ok|okay|approve[d]?|confirm(ed)?|go( ahead)?|do it|run it|start( it)?|"
                    r"sounds good|correct|right|please do|yes please|let'?s go|perfect)\b[\w\s,.!']{0,30}$", re.I)
NO_RE = re.compile(r"^\W*(no|nope|don'?t|do not|reject|wait|not yet|hold on|never mind)\b", re.I)

ROLE = (
    "You are the Omni Studio assistant, an expert in data engineering and data labeling. You help the user "
    "annotate audio, video and image data in Omni Studio step by step: project, data, tasks, test on samples, "
    "run, review. Explain briefly what a step or task does when it helps."
)
SPEAK_RULES = (
    "Answer the user's actual question from the facts about the open project and page. Be short: 1 to 3 "
    "plain sentences, read aloud as well, so no markdown and no bullet lists. When they ask which tasks the "
    "project has or could add, name them in a sentence separated by commas (by audio, video and image when that "
    "helps; if there are more than about 12, give the number and the main ones); use task names, not ids in "
    "brackets. A task that exists in the "
    "studio but is not chosen in this project is not part of the project: say it can be added. Do not "
    "mention how the facts are written. You cannot change anything yourself: when "
    "the user wants a change, say what you suggest and ask them to approve it. Never say you did something "
    "unless the facts show it. Use the exact project names and task ids from the facts. If asked which model you "
    "are or which model labels the data, say it is RFomni, the annotation model; do not name any vendor or base model."
)
DECIDE = (
    "You choose the next action of the Omni Studio assistant. JSON only. Keys: action, args. Pick exactly one "
    "action from the list. Use \"none\" when the assistant only answers, explains or asks a question: questions "
    "about the project (which tasks it has, which could be added, how many files, what to do next) are answered "
    "from the facts with \"none\". Use \"show\" only when the user asks where something is or how to do "
    "something on a page, and \"filter_tasks\" only when they ask to see or filter the task library. Use only "
    "project names, task ids and paths from the facts."
)
EXAMPLES = [
    ("Let's use transcription and emotion.", {"action": "select_tasks", "args": {"tasks": ["asr", "ser"]}}),
    ("Start the run.", {"action": "run", "args": {"mode": "missing"}}),
    ("What does overwrite mean?", {"action": "none", "args": {}}),
    ("What would you suggest for finding laughter?", {"action": "none", "args": {}}),
    ("Open the review page.", {"action": "show", "args": {"page": "review"}}),
    ("Where can I choose the tasks?", {"action": "show", "args": {"page": "tasks", "point": "tasks.library"}}),
    ("How do I add my files?", {"action": "show", "args": {"page": "data", "point": "data.add"}}),
    ("Where do I turn on speech recognition?", {"action": "show", "args": {"page": "tasks", "point": "task:asr"}}),
    ("Show me only the image tasks.", {"action": "filter_tasks", "args": {"media": "image"}}),
    ("Show me the speaker tasks in the library.", {"action": "filter_tasks", "args": {"search": "speaker"}}),
    ("Which tasks did we choose for this project?", {"action": "none", "args": {}}),
    ("Which audio tasks can I still add?", {"action": "none", "args": {}}),
    ("Which image tasks are we not using?", {"action": "none", "args": {}}),
    ("Do we have anything for laughter?", {"action": "none", "args": {}}),
    ("Add speaker diarization too.", {"action": "select_tasks", "args": {"add": ["diarization"]}}),
    ("Drop the translation task.", {"action": "select_tasks", "args": {"remove": ["translation"]}}),
    ("I want to label whether people sound tired.", {"action": "create_task", "args": {}}),
    ("Make the emotion classes simpler.", {"action": "edit_task", "args": {"task": "ser"}}),
    ("Add a class for bored, and make the prompt shorter.", {"action": "revise", "args": {"change": "add bored, shorter prompt"}}),
]
NEXT = {
    "project": "Next: choose an existing project or create a new one.",
    "data": "Next: add files (upload them on the Data page, or link a folder on the server).",
    "tasks": "Next: choose the tasks (or edit or create one).",
    "test": "Next: test a task on a few sample files, or start the run.",
    "run": "A run is in progress; the user can ask for its status or stop it.",
    "review": "Next: review the results on the Review page, check Statistics, or export.",
}


# ----------------------------------------------------------------------------- state
def derive_step(plan: dict) -> str:
    try:
        proj = A.project_of(plan) if plan.get("project_id") else None
    except ValueError:
        proj = None
    if not proj:
        return "project"
    app = A._app()
    if not app.store.list_media(proj["id"]):
        return "data"
    if not (plan["tasks"] or proj.get("tasks")):
        return "tasks"
    job = app.jobs.get(plan["job_id"]) if plan.get("job_id") else None
    if job and job["status"] in ("queued", "running"):
        return "run"
    if job:
        return "review"
    return "test"


def page_project(page: dict | None) -> str | None:
    """The project open in the user's studio tab (#/p/<id>/...), if any."""
    m = re.match(r"#/p/([^/?]+)", (page or {}).get("hash") or "")
    return urllib.parse.unquote(m.group(1)) if m else None


def page_facts(page: dict | None) -> list:
    """What the user has on screen: the page, and the parts of it the assistant can point at."""
    if not page or not page.get("hash"):
        return []
    parts = re.match(r"#/p/[^/?]+(?:/([^/?]+))?(?:/([^/?]+))?", page["hash"])
    if not parts:
        where = "the Projects page (the list of all projects)"
    elif parts.group(1) == "f":
        where = "the editor of one file of the open project"
    else:
        where = f"the {A.PAGE_NAME.get(parts.group(1) or 'data', 'Data')} page of the open project"
    seen = [f"{A.GUIDE[g][1]}" for g in page.get("parts") or [] if g in A.GUIDE]
    return [f"The user is looking at {where}" + (f", which shows {'; '.join(seen)}." if seen else ".")]


def facts(plan: dict, page: dict | None = None, ids: bool = True) -> list:
    """What the model knows this turn; ids=False gives task names only (for the spoken answer)."""
    app = A._app()
    step = plan["step"]
    out = [f"Step: {step}. {NEXT[step]}", *page_facts(page)]
    proj = A.project_of(plan) if plan.get("project_id") and step != "project" else None
    if proj:
        st = app.store.stats(proj["id"])
        mods = ", ".join(f"{n} {m}" for m, n in st["by_modality"].items() if n) or "no files"
        out.append(f"Open project “{proj['name']}”: {st['files']} files ({mods}), {st['annotated']} annotated, "
                   f"{st['reviewed']} reviewed, {st['low']} low-confidence segments.")
        out += K.project_tasks(proj, st["by_modality"], ids)
    else:
        ps = app.store.list_projects()
        out.append("Projects: " + ("; ".join(f"“{p['name']}” ({p['stats']['files']} files)" for p in ps[:15]) or "none"))
        out.append("All tasks (id=title [A audio, V video, I image]): " + K.catalog())
    if plan["tasks"] and (not proj or plan["tasks"] != proj.get("tasks")):
        out.append(f"Tasks agreed in this conversation but not set on the project yet: {', '.join(plan['tasks'])}.")
    if plan["notes"]:
        out.append("Decisions: " + "; ".join(plan["notes"][-5:]))
    if plan.get("pending"):
        out.append(f"Waiting for approval: {plan['pending']['summary']}")
    if plan.get("last_test") and step in ("test", "run"):
        out.append("Last test: " + plan["last_test"][:400])
    if plan.get("job_id"):
        j = app.jobs.get(plan["job_id"])
        if j:
            errs = "; ".join(e["error"][:100] for e in j["errors"][:2])
            state = {"done": "The run has FINISHED", "cancelled": "The run was cancelled", "error": "The run FAILED"}.get(
                j["status"], "The run is still going")
            out.append(f"{state}: {j['done']} of {j['total']} files done" + (f". Problems: {errs}" if errs else "."))
    return out


def _fit(msgs: list) -> list:
    while len(json.dumps(msgs, ensure_ascii=False)) > MAX_PROMPT_CHARS and len(msgs) > 2:
        msgs.pop(1)  # oldest history first
    return msgs


def speak_messages(plan: dict, text: str, fact_lines: list, intent: str) -> list:
    try:
        proj_tasks = A.project_of(plan).get("tasks") or [] if plan.get("project_id") else None
    except ValueError:
        proj_tasks = None
    cards = K.cards_for(plan["step"], text, plan["tasks"], project_tasks=proj_tasks)
    # the instruction for this reply comes last, after the long facts, so the model keeps to it
    system = "\n".join([ROLE, SPEAK_RULES, "Facts:", *fact_lines, "Knowledge:", *cards, "", f"For this reply: {intent}"])
    history = [{"role": h["role"], "content": h["text"]} for h in plan["history"]]
    return _fit([{"role": "system", "content": system}, *history, {"role": "user", "content": text}])


def decide_messages(plan: dict, text: str, fact_lines: list) -> list:
    acts = [f"- {n} {spec[1]}: {spec[2]}" for n, spec in A.ACTIONS.items()]
    shots = [f"User: {u}\n{json.dumps(d)}" for u, d in EXAMPLES]
    recent = [f"{h['role'].capitalize()}: {h['text']}" for h in plan["history"][-4:]]
    parts = [f"- {k}: {what} ({A.PAGE_NAME[page]} page)" for k, (page, what, _) in A.GUIDE.items()]
    parts.append("- task:<task id>: the card of one task in the library (Tasks page)")
    system = "\n".join([DECIDE, "Actions:", *acts, "Parts of the pages that show can point at:", *parts,
                         "Examples:", *shots, "Facts:", *fact_lines])
    convo = "\n".join(recent + [f"User: {text}", "JSON:"])
    return _fit([{"role": "system", "content": system}, {"role": "user", "content": convo}])


# ----------------------------------------------------------------------------- model
def complete(messages: list, max_tokens: int, temperature: float) -> str:
    body = {"model": omni.MODEL, "modalities": ["text"], "max_tokens": max_tokens, "temperature": temperature,
            "messages": messages}
    req = urllib.request.Request(f"{omni.URL}/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with omni._slots, urllib.request.urlopen(req, timeout=omni.TIMEOUT) as r:
            return (json.load(r)["choices"][0]["message"].get("content") or "").strip()
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"The model is not reachable at {omni.URL}: {e}") from e


def _clean_say(raw: str) -> str:
    obj = parse_json(raw)
    if isinstance(obj, dict):  # it answered in JSON anyway
        raw = str(obj.get("say") or obj.get("text") or obj.get("answer") or "")
    raw = re.sub(r"\*\*|__|`|^#+\s*", "", raw, flags=re.M).strip()
    return raw[:1500]


def interpret(raw: str, plan: dict):
    """(action, args, problem) from the decide reply; problem is a message for the user."""
    obj = parse_json(raw)
    if not isinstance(obj, dict):
        return "none", {}, None
    action = A.canonical(obj.get("action"))
    args = obj.get("args") if isinstance(obj.get("args"), dict) else {k: v for k, v in obj.items() if k != "action"}
    if action not in A.ACTIONS:
        return "none", {}, None
    try:
        if action in A.ACTS and action not in ("create_task", "edit_task"):  # those are checked once written
            A.ACTS[action][0](plan, args)
    except ValueError as e:
        return "none", {}, str(e)
    return action, args, None


def _write_task(plan: dict, action: str, args: dict, text: str) -> tuple:
    """Let the task writer fill in a new or changed task: (action, args, note). The decide step only
    notices that the user wants a task; the prompt and classes are written with the writer's rules."""
    ctx = [f"{h['role'].capitalize()}: {h['text']}" for h in plan["history"][-4:]]
    try:
        proj = A.project_of(plan) if plan.get("project_id") else None
    except ValueError:
        proj = None
    if proj:
        has = [m for m, n in A._app().store.stats(proj["id"])["by_modality"].items() if n]
        ctx.append(f"The project has {', '.join(has) or 'no'} files.")
    context = "Conversation so far:\n" + "\n".join(ctx) if ctx else ""
    if action == "create_task":
        out = drafter.write(text, None, context)
        return action, out["task"], out["note"]
    t = A.find_task(args.get("task"))
    out = drafter.write(text, t, context)
    new = {"task": t["id"]}
    if out["task"].get("labels") != (t.get("labels") or []):
        new["labels"] = out["task"].get("labels") or []
    # an existing prompt (the built-in ones are tuned for the model) changes only when the user asks about it
    if out["task"].get("prompt") != t.get("prompt") and re.search(r"prompt|question|wording|phras|instruction", text, re.I):
        new["prompt"] = out["task"]["prompt"]
    return action, new, out["note"]


def _revise(plan: dict, text: str) -> tuple:
    """Change the task proposal that waits for approval, as the user asks."""
    pr = plan.get("pending") or {}
    if pr.get("action") == "create_task":
        out = drafter.write(text, pr["args"], "")
        return "create_task", out["task"], out["note"]
    if pr.get("action") == "edit_task":
        t = A.find_task(pr["args"].get("task"))
        draft = {**t, **{k: v for k, v in pr["args"].items() if k in ("labels", "prompt")}}
        out = drafter.write(text, draft, "")
        return "edit_task", {"task": t["id"], "labels": out["task"].get("labels") or [], "prompt": out["task"]["prompt"]}, out["note"]
    raise ValueError("There is nothing waiting for approval to change.")


def think(plan: dict, text: str, page: dict | None = None) -> dict:
    fact_lines = facts(plan, page)
    raw_act = complete(decide_messages(plan, text, fact_lines), 160, 0.0)
    action, args, problem = interpret(raw_act, plan)
    note = ""
    try:
        if action == "revise":
            action, args, note = _revise(plan, text)
        elif action in ("create_task", "edit_task"):
            action, args, note = _write_task(plan, action, args, text)
        if action in A.ACTS:
            A.ACTS[action][0](plan, args)
    except ValueError as e:
        action, args, problem = "none", {}, str(e)
    if action in A.ACTS:
        intent = (f"You are proposing this to the user, who will see it on an approval card: "
                  f"{A.ACTS[action][1](plan, args)} In one or two sentences say what you will do (and why, only if there "
                  f"is a real reason in the conversation or the facts), and ask "
                  f"whether to go ahead. You do it once they approve: do not tell them to do it themselves, and do "
                  f"not say it is done." + (f" Also tell them: {note}" if note else "")
                  + (" Say they can ask you to change the prompt or the classes before approving."
                     if action in ("create_task", "edit_task") else ""))
    elif problem:
        intent = f"This cannot be done as asked: {problem} Tell the user and help them choose."
    elif action in A.SHOWN:
        intent = (f"The studio is now showing this to the user (already done): {A.SHOWN[action](args)} "
                  f"Answer their message using it and the facts, in one or two sentences.")
    else:
        intent = "Answer the user. Do not propose actions as if they were done."
    raw_say = complete(speak_messages(plan, text, facts(plan, page, ids=False), intent), 320, 0.3)
    say = _clean_say(raw_say) or (problem or "")
    return {"say": say or "Sorry, could you say that again?", "action": action, "args": args,
            "raw": {"decide": raw_act, "speak": raw_say, "problem": problem}}


# ----------------------------------------------------------------------------- turn
def _public_pending(plan):
    return {k: v for k, v in plan["pending"].items() if k != "ts"} if plan.get("pending") else None


def _propose(plan: dict, action: str, args: dict) -> dict:
    summary = A.ACTS[action][1](plan, args)
    plan["pending"] = {"id": f"pr{int(time.time() * 1000) % 10**10}", "action": action, "args": args,
                       "summary": summary, "ts": time.time()}
    return _public_pending(plan)


def execute(user: str, plan: dict, via: str) -> dict:
    pr = plan["pending"]
    plan["pending"] = None
    check, _, run = A.ACTS[pr["action"]]
    try:
        check(plan, pr["args"])
        res = run(plan, pr["args"])
    except ValueError as e:
        res = {"say": f"I could not do that: {e}", "ui": []}
    except Exception as e:  # report, don't crash the panel
        res = {"say": f"Something went wrong while doing that: {e}", "ui": []}
    plan["step"] = derive_step(plan)
    P.log(user, {"event": "executed", "via": via, "action": pr["action"], "args": pr["args"],
                 "summary": pr["summary"], "say": res.get("say")})
    return res


def _finish(user: str, plan: dict, text: str | None, out: dict) -> dict:
    if text:
        P.remember(plan, "user", text)
    P.remember(plan, "assistant", out.get("say", ""))
    plan["step"] = derive_step(plan)
    P.save(user, plan)
    return {"say": out.get("say", ""), "ui": out.get("ui", []), "proposal": out.get("proposal"),
            "result": out.get("result"), "step": plan["step"], "plan": P.public(plan)}


def follow_page(plan: dict, page: dict | None) -> None:
    """The assistant works on the project the user has open: opening another project switches to it."""
    pid = page_project(page)
    if not pid or pid == plan.get("project_id"):
        return
    try:
        A._app().store.get_project(pid)
    except Exception:
        return
    plan.update(project_id=pid, tasks=[], pending=None, job_id=None, last_test=None)


def turn(user: str, text: str, via: str = "text", speaker_ok: bool | None = None, page: dict | None = None) -> dict:
    """Handle one user message. via = text | voice; spoken approvals need speaker_ok; page = the user's studio tab."""
    plan = P.load(user)
    follow_page(plan, page)
    plan["step"] = derive_step(plan)
    text = (text or "").strip()
    out = {"say": "", "ui": [], "proposal": None}

    if STOP_RE.match(text):
        res = A.stop(plan)
        out.update(say=res["say"], ui=res["ui"])
        P.log(user, {"event": "stop", "via": via, "text": text})
    elif plan.get("pending") and YES_RE.match(text):
        if via == "voice" and speaker_ok is not True:
            out.update(say="I can only take a spoken approval from the registered voice. Please press Approve instead.",
                       proposal=_public_pending(plan))
        else:
            out.update(execute(user, plan, via))
    elif plan.get("pending") and NO_RE.match(text):
        P.log(user, {"event": "rejected", "via": via, "proposal": plan["pending"]["id"]})
        plan["pending"] = None
        out["say"] = "Okay, I won't do that. What would you like to change?"
    else:
        r = think(plan, text, page)
        out["say"] = r["say"]
        if r["action"] in A.ACTS:
            out["proposal"] = _propose(plan, r["action"], r["args"])
            if "?" not in out["say"][-80:]:
                out["say"] = f"{out['say']} Shall I go ahead?".strip()
        elif r["action"] in A.HANDLERS:
            try:
                out["ui"] = A.HANDLERS[r["action"]](plan, r["args"]).get("ui", [])
            except ValueError as e:
                out["say"] = f"{out['say']} ({e})".strip()
        P.log(user, {"event": "turn", "via": via, "step": plan["step"], "text": text, "raw": r["raw"],
                     "action": r["action"], "args": r["args"], "say": out["say"],
                     "proposal": out["proposal"]["summary"] if out["proposal"] else None})
    return _finish(user, plan, text, out)


def approve(user: str, proposal_id: str, via: str = "click", speaker_ok: bool | None = None) -> dict:
    plan = P.load(user)
    pr = plan.get("pending")
    if not pr or pr["id"] != proposal_id:
        out = {"say": "That proposal is no longer open."}
    elif via == "voice" and speaker_ok is not True:
        out = {"say": "Spoken approval needs the registered voice. Please press Approve.", "proposal": _public_pending(plan)}
    else:
        out = execute(user, plan, via)
    return _finish(user, plan, None, out)


def reject(user: str, proposal_id: str) -> dict:
    plan = P.load(user)
    if plan.get("pending") and plan["pending"]["id"] == proposal_id:
        plan["pending"] = None
        P.log(user, {"event": "rejected", "via": "click", "proposal": proposal_id})
    return _finish(user, plan, None, {"say": "Okay, I won't do that. What would you like to change?"})


def stop(user: str) -> dict:
    plan = P.load(user)
    res = A.stop(plan)
    P.log(user, {"event": "stop", "via": "button"})
    return _finish(user, plan, None, res)
