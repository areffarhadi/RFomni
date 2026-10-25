"""What the assistant can do in the studio.

Three kinds:
  ui   moves the user around the studio or updates the plan's memory; runs at once
  act  changes data or starts work; becomes a proposal and runs only after the user approves
  stop cancels the run and drops the pending proposal; never goes through the model

Every action has a check (raises ValueError with a message the user can act on) and, for
acts, a describe() that is shown on the approval card, so the user approves exactly what
runs. The handlers call the same store and job manager as the HTTP API.
"""
from pathlib import Path

from .. import config
from .. import jobs as jobs_mod
from .. import tasks as task_reg
from .knowledge import mentioned_tasks, support

PAGES = {"home", "data", "tasks", "stats", "review", "export"}
# Parts of the pages the assistant can point at (data-guide names in the frontend): page, what, how to use it.
GUIDE = {
    "home.new": ("home", "the New project card", "click it to create a project"),
    "data.add": ("data", "the + button above the file list", "upload files or a folder, or link a folder on the server"),
    "data.files": ("data", "the file list of this project", "click a file to open it"),
    "tasks.library": ("tasks", "the task library", "switch a task on to add it to this project, click a card for details"),
    "tasks.selected": ("tasks", "the tiers of this project", "drag to reorder, × to remove"),
    "tasks.filters": ("tasks", "the library filters", "search, or filter by annotation or captioning and by media type"),
    "tasks.new": ("tasks", "the New prompt task button", "write your own task with a prompt and classes"),
    "tasks.mine": ("tasks", "your changed and new tasks", "edit, test or reset them here"),
    "tasks.run": ("tasks", "the Run tasks… button", "run the project's tasks on its files"),
}
PAGE_NAME = {"home": "Projects", "data": "Data", "tasks": "Tasks", "stats": "Statistics", "review": "Review", "export": "Export"}
DEFAULT_POINT = {"home": "home.new", "data": "data.add", "tasks": "tasks.library"}
MODE_TEXT = {"missing": "only fill empty tiers", "unreviewed": "re-run unreviewed tiers",
             "overwrite": "replace everything, including reviewed work"}


def _app():
    from .. import main  # late: main imports this package
    return main


def _nav(pid=None, page=None) -> dict:
    if not pid:
        return {"type": "navigate", "hash": "#/"}
    return {"type": "navigate", "hash": f"#/p/{pid}" + (f"/{page}" if page and page != "data" else "")}


# ----------------------------------------------------------------------------- lookups
def project_of(plan: dict) -> dict:
    if not plan.get("project_id"):
        raise ValueError("No project is selected yet. Which project should we work on?")
    try:
        return _app().store.get_project(plan["project_id"])
    except Exception:
        plan["project_id"] = None
        raise ValueError("The selected project no longer exists. Which project should we work on?")


def find_project(name: str) -> dict:
    name = str(name or "").strip()
    projects = _app().store.list_projects()
    low = name.lower()
    for test in (lambda p: p["id"] == name, lambda p: p["name"].lower() == low,
                 lambda p: p["name"].lower().startswith(low) or p["id"].startswith(low),
                 lambda p: low in p["name"].lower()):
        hits = [p for p in projects if low and test(p)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError(f"Several projects match “{name}”: {', '.join(p['name'] for p in hits[:6])}. Which one?")
    names = ", ".join(p["name"] for p in projects[:10]) or "none yet"
    raise ValueError(f"There is no project called “{name}”. Projects: {names}.")


def find_task(ref) -> dict:
    ref = str(ref or "").strip()
    t = task_reg.get_task(ref) or task_reg.get_task(ref.lower())
    if t:
        return t
    low = ref.lower()
    for t in task_reg.all_tasks():
        if low in (t["name"].lower(), t.get("title", "").lower()):
            return t
    ids = mentioned_tasks(ref)
    if len(ids) == 1:
        return task_reg.get_task(ids[0])
    raise ValueError(f"I don't know a task called “{ref}”.")


def _task_names(ids) -> str:
    by = task_reg.tasks_by_id()
    return ", ".join(by[i]["name"] if i in by else i for i in ids)


def _media_files(root: Path, cap: int = 20001) -> int:
    if root.is_file():
        return int(root.suffix.lower() in config.MEDIA_EXT)
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in config.MEDIA_EXT:
            n += 1
            if n >= cap:
                break
    return n


def active_job(pid: str):
    return next((j for j in _app().jobs.list(pid) if j["status"] in ("queued", "running")), None)


# ----------------------------------------------------------------------------- ui actions
def point_of(args) -> tuple:
    """(page, target, note) for a show action; target is None when nothing is highlighted."""
    page = str(args.get("page") or "").strip().lower()
    point = str(args.get("point") or args.get("target") or "").strip()
    target = what = how = None
    if point.lower().startswith("task:") or (point and point not in GUIDE and f"{page}.{point}" not in GUIDE):
        try:
            t = find_task(point.split(":", 1)[-1])
            target, page = f"task:{t['id']}", "tasks"
            what = f"the {t['name']} card ({t.get('title') or t['id']})"
            how = "switch it on to add it to this project, or click the card for details"
        except ValueError:
            point = ""
    if not target and point:
        key = point if point in GUIDE else f"{page}.{point}"
        if key in GUIDE:
            target, page = key, GUIDE[key][0]
    page = page if page in PAGES else "data"
    if not target and page in DEFAULT_POINT:
        target = DEFAULT_POINT[page]
    if target and not what:
        what, how = GUIDE[target][1:]
    return page, target, what, how


def point_say(args) -> str:
    """What the assistant says when it highlights something: written here, so it is exact."""
    page, target, what, how = point_of(args)
    if not target:
        return f"Here is the {PAGE_NAME[page]} page."
    return f"I've highlighted {what} on the {PAGE_NAME[page]} page. {how[0].upper() + how[1:]}."


def ui_show(plan, args):
    page, target, what, how = point_of(args)
    nav = _nav() if page == "home" else _nav(project_of(plan)["id"], page)
    note = f"{what[0].upper() + what[1:]}: {how}." if target else ""
    return {"ui": [nav] + ([{"type": "point", "target": target, "note": note}] if target else [])}


MEDIA = {"audio": "audio", "speech": "audio", "sound": "audio", "voice": "audio", "video": "video", "videos": "video",
         "image": "image", "images": "image", "picture": "image", "photo": "image", "all": "all", "any": "all"}
KINDS = {"annotation": "annotation", "captioning": "captioning", "caption": "captioning", "all": "all"}


def filter_of(args) -> dict:
    media = MEDIA.get(str(args.get("media") or args.get("modality") or "all").strip().lower(), "all")
    kind = KINDS.get(str(args.get("kind") or "all").strip().lower(), "all")
    search = str(args.get("search") or "").strip()[:40]
    n = sum(1 for t in task_reg.all_tasks()
            if (media == "all" or media in t.get("modalities", [])) and (kind == "all" or t.get("kind") == kind)
            and (not search or search.lower() in f"{t['name']} {t.get('title', '')} {t.get('description', '')} {t['id']}".lower()))
    return {"media": media, "kind": kind, "search": search, "count": n}


def filter_say(args) -> str:
    f = filter_of(args)
    what = " ".join(x for x in (f["media"] if f["media"] != "all" else "", f["kind"] if f["kind"] != "all" else "") if x)
    what = f"{what} tasks" if what else "tasks"
    if f["search"]:
        what += f" matching “{f['search']}”"
    if f["media"] == f["kind"] == "all" and not f["search"]:
        return "The task library shows all tasks again."
    return f"The task library now shows only {what}: {f['count']} of them. Ask me, or clear the filters, to see every task again."


def ui_filter_tasks(plan, args):
    f = filter_of(args)
    note = "Filtered by the assistant. Change it here at any time."
    return {"ui": [_nav(project_of(plan)["id"], "tasks"), {"type": "filter_tasks", **f},
                   {"type": "point", "target": "tasks.filters", "note": note}]}


# what the page shows after these ui actions; the model gets it and writes the answer around it
SHOWN = {"show": point_say, "filter_tasks": filter_say}


def ui_plan_tasks(plan, args):
    ids = [find_task(t)["id"] for t in args.get("tasks") or []]
    if not ids:
        raise ValueError("Which tasks?")
    plan["tasks"] = list(dict.fromkeys(ids))
    return {}


def ui_note(plan, args):
    text = str(args.get("text") or "").strip()
    if text:
        plan["notes"] = (plan["notes"] + [text[:200]])[-10:]
    return {}


def ui_open_upload(plan, args):
    p = project_of(plan)
    return {"ui": [_nav(p["id"], "data"), {"type": "open_upload"}]}


# ----------------------------------------------------------------------------- acts
def check_select_project(plan, args):
    find_project(args.get("project"))


def describe_select_project(plan, args):
    p = find_project(args.get("project"))
    st = p.get("stats") or {}
    return f"Open project “{p['name']}” ({st.get('files', 0)} files) and work on it."


def run_select_project(plan, args):
    p = find_project(args.get("project"))
    plan.update(project_id=p["id"], job_id=None, last_test=None, tasks=list(p.get("tasks") or []))
    return {"say": f"Project “{p['name']}” is open.", "ui": [_nav(p["id"])]}


def check_create_project(plan, args):
    name = str(args.get("name") or "").strip()
    if not name:
        raise ValueError("What should the new project be called?")
    if any(p["name"].lower() == name.lower() for p in _app().store.list_projects()):
        raise ValueError(f"A project called “{name}” already exists.")


def describe_create_project(plan, args):
    return f"Create a new project “{str(args['name']).strip()}”."


def run_create_project(plan, args):
    p = _app().store.create_project(str(args["name"]).strip(), str(args.get("description") or ""), plan["tasks"] or None)
    plan.update(project_id=p["id"], job_id=None, last_test=None)
    return {"say": f"Created project “{p['name']}”.", "ui": [_nav(p["id"])]}


def _link_root(args) -> Path:
    raw = str(args.get("path") or "").strip()
    if not raw:
        raise ValueError("Which folder on the server should I link?")
    root = Path(raw).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"The path {root} does not exist on the server.")
    return root


def check_link_data(plan, args):
    project_of(plan)
    if not _media_files(_link_root(args)):
        raise ValueError("That folder has no audio, video or image files.")


def describe_link_data(plan, args):
    p, root = project_of(plan), _link_root(args)
    n = _media_files(root)
    more = "more than 20,000" if n > 20000 else str(n)
    return f"Link {more} media files from {root} into “{p['name']}” (they stay where they are, nothing is copied)."


def run_link_data(plan, args):
    p = project_of(plan)
    res = _app().link_media(p["id"], {"path": str(_link_root(args))})
    errs = f" {len(res['errors'])} files could not be read." if res.get("errors") else ""
    return {"say": f"Added {res['added']} files ({res['skipped']} were already there).{errs}",
            "ui": [_nav(p["id"]), {"type": "refresh"}]}


def _task_ids(refs) -> list:
    if isinstance(refs, str):
        refs = [r.strip() for r in refs.split(",")]
    return list(dict.fromkeys(find_task(r)["id"] for r in refs or [] if str(r).strip()))


def _resolve_tasks(args, current=()) -> list:
    """The project's new task list: "tasks" sets it; "add" / "remove" change the current one."""
    if args.get("tasks"):
        ids = _task_ids(args["tasks"])
    else:
        add, remove = _task_ids(args.get("add")), set(_task_ids(args.get("remove")))
        ids = [i for i in list(dict.fromkeys([*current, *add])) if i not in remove]
    if not ids:
        raise ValueError("Which tasks should the project run?")
    return ids


def check_select_tasks(plan, args):
    p = project_of(plan)
    if _resolve_tasks(args, p.get("tasks") or []) == (p.get("tasks") or []):
        raise ValueError("The project already has exactly these tasks.")


def describe_select_tasks(plan, args):
    p = project_of(plan)
    ids = _resolve_tasks(args, p.get("tasks") or [])
    old = set(p.get("tasks") or [])
    added, removed = [i for i in ids if i not in old], [i for i in old if i not in ids]
    by = task_reg.tasks_by_id()
    blocked = [i for i in (added if old else ids) if support(by[i]).startswith("cannot")]
    if not old or args.get("tasks") and len(ids) <= 8:   # a new list: name it; a change: name only what changes
        parts = [f"Set the tasks of “{p['name']}” to: {_task_names(ids)}."]
    else:
        parts = [f"Change the tasks of “{p['name']}” ({len(old)} now, {len(ids)} after):"]
    if added:
        parts.append(f"add {_task_names(added)}.")
    if removed:
        parts.append(f"remove {_task_names(removed)} (their existing annotations stay).")
    if blocked:
        parts.append(f"Note: {_task_names(blocked)} cannot run yet.")
    return " ".join(parts)


def run_select_tasks(plan, args):
    p = project_of(plan)
    ids = _resolve_tasks(args, p.get("tasks") or [])
    _app().store.update_project(p["id"], {"tasks": ids})
    plan["tasks"] = ids
    return {"say": f"The project now has these tasks: {_task_names(ids)}.",
            "ui": [_nav(p["id"], "tasks"), {"type": "refresh"}]}


def _edit_fields(args) -> dict:
    out = {}
    if args.get("labels") is not None:
        labels = args["labels"] if isinstance(args["labels"], list) else str(args["labels"]).split(",")
        out["labels"] = [str(l).strip() for l in labels if str(l).strip()]
    if str(args.get("prompt") or "").strip():
        out["prompt"] = str(args["prompt"]).strip()
    if not out:
        raise ValueError("What should change in the task: its classes or its prompt?")
    return out


def check_edit_task(plan, args):
    t = find_task(args.get("task"))
    fields = _edit_fields(args)
    if "labels" in fields and t["output"] == "label" and not fields["labels"]:
        raise ValueError("A label task needs at least one class.")


def describe_edit_task(plan, args):
    t, f = find_task(args.get("task")), _edit_fields(args)
    parts = [f"Change the task “{t['name']}”:"]
    if "labels" in f:
        parts.append(f"classes {', '.join(t.get('labels') or []) or '(none)'} → {', '.join(f['labels'])}.")
    if "prompt" in f:
        parts.append(f"new prompt: “{f['prompt'][:300]}”.")
    if t.get("builtin"):
        parts.append("It is a built-in task; it can be reset to its default later.")
    return " ".join(parts)


def run_edit_task(plan, args):
    t, f = find_task(args.get("task")), _edit_fields(args)
    if t.get("builtin"):
        task_reg.save_override(t["id"], {**{k: t.get(k) for k in task_reg.OVERRIDABLE if k in t}, **f})
    else:
        task_reg.save_custom_task({**t, **f}, t["id"])
    ui = [_nav(plan["project_id"], "tasks"), {"type": "refresh"}] if plan.get("project_id") else []
    return {"say": f"Updated the task “{t['name']}”.", "ui": ui}


def _draft(args) -> dict:
    mods = args.get("modalities") or ["audio"]
    raw = {"name": args.get("name"), "prompt": args.get("prompt"), "output": args.get("output", "label"),
           "granularity": args.get("granularity", "segment"),
           "modalities": mods if isinstance(mods, list) else [m.strip() for m in str(mods).split(",")],
           "labels": args.get("labels") or [], "kind": args.get("kind", "annotation"),
           "description": args.get("description", ""),
           **{k: args[k] for k in ("score_min", "score_max", "window_sec") if k in args}}
    return task_reg.validate_task(raw)


def check_create_task(plan, args):
    _draft(args)


def describe_create_task(plan, args):
    t = _draft(args)
    cls = f" Classes: {', '.join(t['labels'])}." if t["labels"] else ""
    if t["output"] == "score":
        cls = f" Score from {t['score_min']:g} to {t['score_max']:g}."
    where = f" and add it to “{project_of(plan)['name']}”" if plan.get("project_id") else ""
    each = f"{t['window_sec']:g}-second window" if t["granularity"] == "window" else t["granularity"]
    return (f"Create the task “{t['name']}”{where}: {t['output']} per {each}, for {', '.join(t['modalities'])}. "
            f"Prompt: “{t['prompt']}”{cls}")


def run_create_task(plan, args):
    t = task_reg.save_custom_task(_draft(args))
    plan["tasks"] = list(dict.fromkeys(plan["tasks"] + [t["id"]]))
    ui = []
    if plan.get("project_id"):
        p = project_of(plan)
        _app().store.update_project(p["id"], {"tasks": list(dict.fromkeys((p.get("tasks") or []) + [t["id"]]))})
        ui = [_nav(p["id"], "tasks"), {"type": "refresh"}]
    return {"say": f"Created the task “{t['name']}” (id {t['id']}).", "ui": ui}


def _n(args) -> int:
    try:
        return max(1, min(5, int(args.get("n") or 3)))
    except (TypeError, ValueError):
        return 3


def check_test_task(plan, args):
    p, t = project_of(plan), find_task(args.get("task"))
    if not any(m["modality"] in t.get("modalities", []) for m in _app().store.list_media(p["id"])):
        raise ValueError(f"“{p['name']}” has no {'/'.join(t.get('modalities', []))} files for {t['name']}.")
    if support(t).startswith("cannot"):
        raise ValueError(f"{t['name']} {support(t)}.")


def describe_test_task(plan, args):
    t = find_task(args.get("task"))
    return f"Try “{t['name']}” on {_n(args)} random files of “{project_of(plan)['name']}”. Nothing is saved."


def run_test_task(plan, args):
    p, t = project_of(plan), find_task(args.get("task"))
    res = jobs_mod.test_task(_app().store, p["id"], t, None, _n(args))
    lines = []
    for r in res["results"]:
        if r.get("error"):
            lines.append(f"{r['media']['name']}: error {r['error'][:80]}")
            continue
        vals = [s.get("value") for s in r["segments"][:2]]
        vals = [", ".join(v) if isinstance(v, list) else str(v) for v in vals]
        lines.append(f"{r['media']['name']}: {r['count']} results, e.g. {' | '.join(v[:80] for v in vals) or '(nothing found)'}")
    plan["last_test"] = f"{t['name']} on {len(lines)} files: " + "; ".join(lines)
    return {"say": "Test results — " + "; ".join(lines), "ui": [_nav(p["id"], "tasks")], "result": res["results"]}


def _run_tasks(plan, args) -> list:
    ids = _resolve_tasks(args) if args.get("tasks") else list(plan["tasks"])
    if not ids:
        raise ValueError("No tasks are selected for the run yet.")
    return ids


def _mode(args) -> str:
    mode = str(args.get("mode") or "missing")
    if mode not in jobs_mod.MODES:
        raise ValueError(f"mode must be one of {', '.join(jobs_mod.MODES)}")
    return mode


def check_run(plan, args):
    p = project_of(plan)
    _run_tasks(plan, args)
    _mode(args)
    if not _app().store.list_media(p["id"]):
        raise ValueError(f"“{p['name']}” has no files yet. Let's add data first.")
    if active_job(p["id"]):
        raise ValueError("A run is already in progress for this project.")


def describe_run(plan, args):
    p, ids, mode = project_of(plan), _run_tasks(plan, args), _mode(args)
    media = _app().store.list_media(p["id"])
    hours = sum(m.get("duration") or 0 for m in media) / 3600
    by = task_reg.tasks_by_id()
    blocked = [i for i in ids if i in by and support(by[i]).startswith("cannot")]
    note = f" {_task_names(blocked)} will be skipped (not connected yet)." if blocked else ""
    size = f"{len(media)} files" + (f", {hours:.1f} h of audio/video" if hours >= 0.05 else "")
    return f"Run {_task_names(ids)} on all {size} of “{p['name']}”, mode: {MODE_TEXT[mode]}. You can stop it at any time.{note}"


def run_run(plan, args):
    p, ids, mode = project_of(plan), _run_tasks(plan, args), _mode(args)
    job = _app().jobs.start(p["id"], ids, None, mode)
    plan.update(job_id=job["id"], mode=mode)
    return {"say": f"The run has started on {job['total']} files. I'll keep you posted.",
            "ui": [_nav(p["id"]), {"type": "job", "job_id": job["id"]}]}


# ----------------------------------------------------------------------------- registry
ACTIONS = {
    # name: (kind, args shown to the model, what it does)
    "none": ("ui", "{}", "just talk: answer, explain, ask"),
    "show": ("ui", '{"page": "data|tasks|stats|review|export|home", "point": "<optional: ' + "|".join(GUIDE) + '|task:<task id>>"}',
             "open a page and highlight the part the user asks about (where is X, how do I do Y on a page)"),
    "plan_tasks": ("ui", '{"tasks": ["<task id>", ...]}', "remember the agreed tasks (changes nothing yet)"),
    "note": ("ui", '{"text": "<decision>"}', "remember a decision"),
    "open_upload": ("ui", "{}", "open the upload area so the user can drop files"),
    "filter_tasks": ("ui", '{"media": "audio|video|image|all", "kind": "annotation|captioning|all", "search": "<optional word>"}',
                     "show only some tasks in the task library (only audio tasks, only captioning, tasks about a word)"),
    "select_project": ("act", '{"project": "<name or id>"}', "work on an existing project"),
    "create_project": ("act", '{"name": "<name>"}', "create a new project"),
    "link_data": ("act", '{"path": "<server folder>"}', "add files from a server folder without copying"),
    "select_tasks": ("act", '{"add": ["<task id>", ...]} or {"remove": ["<task id>", ...]} or {"tasks": [<the full new list>]}',
                     "change the tasks the project runs (add or remove some, or set the whole list)"),
    "edit_task": ("act", '{"task": "<id>", "labels": ["..."], "prompt": "..."}', "change a task's classes or prompt"),
    "create_task": ("act", '{"name": "...", "prompt": "...", "output": "label|text|tags|score", '
                           '"granularity": "file|segment|window|event", "modalities": ["audio"], "labels": ["..."]}',
                    "create a new prompt task"),
    "test_task": ("act", '{"task": "<id>", "n": 3}', "try a task on a few sample files (nothing saved)"),
    "revise": ("ui", '{"change": "<what the user wants different>"}',
               "change the proposal that is waiting for approval, e.g. a draft task's prompt, classes or name"),
    "run": ("act", '{"mode": "missing|unreviewed|overwrite"}', "annotate all files with the plan's tasks"),
}

HANDLERS = {
    "show": ui_show, "plan_tasks": ui_plan_tasks, "note": ui_note,
    "open_upload": ui_open_upload, "filter_tasks": ui_filter_tasks,
}
ACTS = {
    "select_project": (check_select_project, describe_select_project, run_select_project),
    "create_project": (check_create_project, describe_create_project, run_create_project),
    "link_data": (check_link_data, describe_link_data, run_link_data),
    "select_tasks": (check_select_tasks, describe_select_tasks, run_select_tasks),
    "edit_task": (check_edit_task, describe_edit_task, run_edit_task),
    "create_task": (check_create_task, describe_create_task, run_create_task),
    "test_task": (check_test_task, describe_test_task, run_test_task),
    "run": (check_run, describe_run, run_run),
}

# names the model tends to use for the real actions
ALIASES = {
    "run_tasks": "run", "start_run": "run", "start": "run", "annotate": "run", "run_all": "run",
    "set_tasks": "select_tasks", "add_tasks": "select_tasks", "choose_tasks": "select_tasks", "use_tasks": "select_tasks",
    "test": "test_task", "try_task": "test_task", "sample": "test_task",
    "open_project": "select_project", "choose_project": "select_project", "new_project": "create_project",
    "add_data": "link_data", "link": "link_data", "link_folder": "link_data", "upload": "open_upload",
    "new_task": "create_task", "change_task": "edit_task", "update_task": "edit_task",
    "navigate": "show", "open_page": "show", "answer": "none", "reply": "none", "talk": "none",
}


def canonical(name: str) -> str:
    name = str(name or "none").strip().lower().replace("-", "_").replace(" ", "_")
    return ALIASES.get(name, name)


def stop(plan: dict) -> dict:
    """Stop button / spoken "stop": cancel the run, drop the proposal. No model involved."""
    stopped = []
    if plan.get("pending"):
        stopped.append("dropped the pending proposal")
        plan["pending"] = None
    pid = plan.get("project_id")
    job = active_job(pid) if pid else None
    if job and _app().jobs.cancel(job["id"]):
        stopped.append(f"stopped the run ({job['done']} of {job['total']} files were done; "
                       "everything finished so far is kept)")
    say = ("Stopped: " + " and ".join(stopped) + ".") if stopped else "Nothing is running. I'm standing by."
    return {"say": say, "ui": [{"type": "refresh"}] if job else []}
