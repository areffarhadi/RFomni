"""A Task sharing or Subjective test page in plain words: the form the page assistant and the Settings tab edit.

The default pages (templates/) give a page its start. From then on the page itself is edited, not a fixed list of
settings: any question can be added, removed, moved, reworded or turned into another kind, as long as the volunteer
page can show it (shared_tasks.validate_design checks every version).

    view(design, practice, modality) -> {"title", "instructions",
        "questions":    [{"question", "type": a block's kind (backend/blocks.py), "answers", "help", "required",
                          and the block's own settings: "from", "to", "words", "decimals", "big_box", "hint", "count",
                          "same", "several"}],
        "before_start": [{"question", "type": buttons|text, "answers", "required"}],   # about the people themselves
        "item_position": top|side, "look": {...}, "rules": {...}, "charts": {...},
        "output": {"format": csv|json|jsonl, "rows": answer|item|person, "include": [...],   # the results file
                   "column_names": [{"question", "column"}]}}
    build(view, old_design) -> design      clean(view) -> view      describe(old, new) -> "Done: …"
"""
import re

from .. import blocks as BL
from .. import shared_output as SO
from .. import shared_tasks as ST
from ..util import slugify
from .settings import THEMES

KINDS = BL.KINDS
ALIASES = {"choice": "buttons", "button": "buttons", "radio": "buttons", "one": "buttons", "single": "buttons", "select": "dropdown",
           "multi": "checkboxes", "checkbox": "checkboxes", "tick boxes": "checkboxes", "several": "checkboxes",
           "rating": "scale", "sliding bar": "slider", "range": "slider", "continuous": "slider", "box": "text", "free text": "text",
           "textbox": "text", "write": "text", "menu": "dropdown", "list": "dropdown", "rank": "ranking", "order": "ranking",
           "matrix": "grid", "correct": "correct_text", "edit": "correct_text", "ab": "ab_choice", "a/b": "ab_choice",
           "compare": "ab_choice", "cmos": "ab_scale", "best_and_worst": "best_worst", "pick": "pick_items",
           "segments": "time_ranges", "ranges": "time_ranges", "time_range": "time_ranges", "moments": "time_points",
           "time_point": "time_points", "trace": "rating_over_time", "box_drawing": "boxes", "bounding_boxes": "boxes",
           "point": "points", "polygons": "regions", "polygon": "regions", "shapes": "regions", "spans": "highlights",
           "highlight": "highlights"}
LOOK = {"colours": THEMES, "language": ["en", "fa"], "progress": ["bar", "ring"]}
RULES = {"people_per_item": (0, 50), "items_per_person": (0, 100000), "practice_items": (0, 10), "max_plays": (0, 20),
         "seconds_per_item": (0, 3600)}
FLAGS = ["random_order", "can_skip", "can_go_back", "ask_name", "play_to_end", "no_repeat", "final_comment", "keyboard_keys"]
PLAYED = ("play_to_end", "no_repeat", "max_plays")   # only recordings and videos are played
CHARTS = {"last_screen": list(ST.FINISH_CHARTS), "results": list(ST.RESULT_CHARTS)}
MAX_QUESTIONS, MAX_BEFORE = 12, 8


class Bad(ValueError):
    pass


# ----------------------------------------------------------------------------- design -> view
def _question(f: dict) -> dict:
    return BL.to_view(f)


def view(design: dict, practice: int = 0, modality: str = "") -> dict:
    st, lk = design["settings"], design["look"]
    rules = {"people_per_item": st["labels_per_item"], "items_per_person": st["max_per_session"], "random_order": st["shuffle"],
             "can_skip": st["allow_skip"], "can_go_back": st["allow_back"], "ask_name": st["ask_name"],
             "play_to_end": st["must_listen"], "no_repeat": st["no_repeat"], "practice_items": practice, "final_comment": st["final_comment"],
             "max_plays": st.get("max_plays", 0), "seconds_per_item": st.get("time_limit", 0), "keyboard_keys": st.get("show_keys", True)}
    if modality and modality not in ("voice", "video"):
        for k in PLAYED:
            rules.pop(k)
    return {
        "title": design["title"], "instructions": design["instructions"],
        "questions": [_question(f) for f in design["fields"]],
        "before_start": [{"question": f["label"], "type": "buttons" if f["type"] == "choice" else "text",
                          "answers": list(f.get("options") or []), "required": bool(f.get("required"))} for f in design.get("intro_fields") or []],
        "item_position": "top" if lk.get("layout") == "stacked" else "side",
        "look": {"colours": lk.get("theme", "forest"), "accent": lk.get("accent", ""), "language": lk.get("ui_lang", "en"),
                 "progress": lk.get("progress", "bar")},
        "rules": rules,
        "charts": {"last_screen": list(st.get("finish_charts") or []), "results": list(st.get("result_charts") or [])},
        "output": output_view(design),
    }


def output_view(design: dict) -> dict:
    o = SO.clean(design.get("output"))
    label = {f["id"]: f["label"] for f in design["fields"]}
    return {"format": o["format"], "rows": o["rows"], "include": o["include"],
            "column_names": [{"question": label[k], "column": c} for k, c in o["names"].items() if k in label]}


# ----------------------------------------------------------------------------- checking a view
def _text(v, n: int) -> str:
    return str(v if v is not None else "").strip()[:n]


def _list(v, n: int, size: int) -> list:
    if isinstance(v, str):
        v = re.split(r"\n|;|,", v)
    return list(dict.fromkeys(_text(x, size) for x in (v or []) if _text(x, size)))[:n]


def _int(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return default


def _bool(v, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "on", "1")


def _clean_q(q: dict, before: bool = False, modality: str = "") -> dict:
    if not isinstance(q, dict):
        raise Bad("A question needs its words and a kind")
    text = _text(q.get("question") or q.get("label"), 300 if not before else 200)
    if not text:
        raise Bad("A question has no words")
    t = str(q.get("type") or "").strip().lower().replace(" ", "_").replace("-", "_")
    t = ALIASES.get(t, ALIASES.get(t.replace("_", " "), t))
    answers = _list(q.get("answers") or q.get("options"), 20, 60)
    if before:
        t = "buttons" if t in ("buttons", "checkboxes", "dropdown") and len(answers) >= 2 else "text"
        return {"question": text, "type": t, "answers": answers if t == "buttons" else [], "required": _bool(q.get("required"), False)}
    if t not in KINDS or (modality and modality not in BL.KIND[t].media):   # not a kind this data can have
        t = "buttons" if len(answers) >= 2 else "text"
    out = {"question": text, "type": t, "answers": [], "help": _text(q.get("help"), 300),
           "required": _bool(q.get("required"), t not in ("text", "correct_text"))}
    try:
        out.update(BL.clean_view(q, t, text))
    except ValueError as e:
        raise Bad(str(e))
    return out


def clean(v: dict, modality: str = "") -> dict:
    """A view with every value checked and in range; raises Bad with a plain reason."""
    if not isinstance(v, dict):
        raise Bad("The page should be an object")
    qs = [_clean_q(q, modality=modality) for q in (v.get("questions") or [])][:MAX_QUESTIONS]
    if not qs:
        raise Bad("The page needs at least one question")
    seen, uniq = set(), []
    for q in qs:   # the same question twice: the first one stays
        if q["question"].lower() not in seen:
            seen.add(q["question"].lower())
            uniq.append(q)
    qs = uniq
    look, rules, charts = v.get("look") or {}, v.get("rules") or {}, v.get("charts") or {}
    accent = _text(look.get("accent"), 7).lower()
    out = {
        "title": _text(v.get("title"), 100) or "Help us label this data",
        "instructions": _text(v.get("instructions"), 1500),
        "questions": qs,
        "before_start": [_clean_q(q, before=True) for q in (v.get("before_start") or [])][:MAX_BEFORE],
        "item_position": "top" if v.get("item_position") == "top" else "side",
        "look": {"colours": look.get("colours") if look.get("colours") in LOOK["colours"] else "forest",
                 "accent": accent if re.fullmatch(r"#[0-9a-f]{6}", accent) else "",
                 "language": look.get("language") if look.get("language") in LOOK["language"] else "en",
                 "progress": look.get("progress") if look.get("progress") in LOOK["progress"] else "bar"},
        "rules": {**{k: _int(rules.get(k), lo, hi, 0) for k, (lo, hi) in RULES.items()},
                  **{k: _bool(rules.get(k), k in ("random_order", "can_skip", "keyboard_keys")) for k in FLAGS}},
        "charts": {k: [c for c in dict.fromkeys(charts.get(k) or []) if c in opts] for k, opts in CHARTS.items()},
    }
    o = v.get("output") or {}
    asked = {q["question"].lower() for q in qs}
    out["output"] = {"format": o.get("format") if o.get("format") in SO.FORMATS else SO.DEFAULT["format"],
                     "rows": o.get("rows") if o.get("rows") in SO.ROWS else SO.DEFAULT["rows"],
                     "include": [x for x in dict.fromkeys(o.get("include") or []) if x in SO.EXTRAS] if "include" in o else list(SO.DEFAULT["include"]),
                     "column_names": [{"question": _text(c.get("question"), 300), "column": _text(c.get("column"), 60)}
                                      for c in (o.get("column_names") or []) if isinstance(c, dict) and _text(c.get("column"), 60)
                                      and _text(c.get("question"), 300).lower() in asked][:24]}
    if modality and modality not in ("voice", "video"):
        for k in PLAYED:
            out["rules"].pop(k)
    return out


# ----------------------------------------------------------------------------- view -> design
def _words(t: str) -> set:
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in re.findall(r"[^\W\d_]{4,}", t.lower())} - {"which", "what", "your", "this", "there"}


def _same(a: str, a_type: str, b: str, b_type: str) -> bool:
    """Two questions in the same place are one question when they are of a kind or share a word."""
    kind = lambda t: "scale" if t == "slider" else "text" if t == "correct_text" else t   # a scale turned into a slider is the same question
    return kind(a_type) == kind(b_type) or bool(_words(a) & _words(b))


def _ids(new: list, old_fields: list) -> list:
    """Field ids for the questions: a question keeps its id when its words stay, or when it is reworded in its place
    as the same kind, so answers already given stay with it."""
    stored = lambda q: BL.KIND[q["type"]].stored
    by_text = {f["label"].strip().lower(): f for f in old_fields}
    texts = {q["question"].strip().lower() for q in new}
    used, out = set(), []
    for i, q in enumerate(new):
        o = by_text.get(q["question"].strip().lower())
        fid = o["id"] if o and o["type"] == stored(q) else None   # another kind of answer: a new id, so old answers do not mix
        if not fid or fid in used:
            o = old_fields[i] if i < len(old_fields) else None
            if o and o["type"] == stored(q) and _same(o["label"], BL.kind_of(o), q["question"], q["type"]) and o["id"] not in used \
                    and o["label"].strip().lower() not in texts:
                fid = o["id"]
            else:
                fid = "q_" + (slugify(q["question"], 20).replace("-", "_") or "q")
                if any(f["id"] == fid and f["type"] != stored(q) for f in old_fields):
                    fid += "_" + q["type"][:6]
        while fid in used:
            fid += "_2"
        used.add(fid)
        out.append(fid)
    return out


def build(v: dict, old: dict) -> dict:
    """The shared-task design of a (clean) view, on top of the design before it; checked by shared_tasks."""
    fields = []
    for fid, q in zip(_ids(v["questions"], old.get("fields") or []), v["questions"]):
        fields.append({"id": fid, "label": q["question"], "required": q["required"], "help": q.get("help", ""), **BL.to_design(q)})
    intro = [{"id": slugify(b["question"], 24).replace("-", "_") or f"info{i + 1}", "label": b["question"],
              "type": "choice" if b["type"] == "buttons" else "text", "options": b["answers"], "required": b["required"]}
             for i, b in enumerate(v["before_start"])]
    r, lk = v["rules"], v["look"]
    st = {**old.get("settings", {}), "labels_per_item": r["people_per_item"], "max_per_session": r["items_per_person"],
          "shuffle": r["random_order"], "allow_skip": r["can_skip"], "allow_back": r["can_go_back"], "ask_name": r["ask_name"],
          "must_listen": r.get("play_to_end", False), "no_repeat": r.get("no_repeat", False), "final_comment": r["final_comment"],
          "max_plays": r.get("max_plays", 0), "time_limit": r.get("seconds_per_item", 0), "show_keys": r.get("keyboard_keys", True),
          "finish_charts": v["charts"]["last_screen"], "result_charts": v["charts"]["results"]}
    look = {**old.get("look", {}), "theme": lk["colours"], "accent": lk["accent"], "layout": "stacked" if v["item_position"] == "top" else "side",
            "ui_lang": lk["language"], "dir": "rtl" if lk["language"] == "fa" else "ltr", "progress": lk["progress"]}
    o = v.get("output") or SO.DEFAULT
    by_label = {f["label"].lower(): f["id"] for f in fields}
    output = {"format": o["format"], "rows": o["rows"], "include": o["include"],
              "names": {by_label[c["question"].lower()]: c["column"] for c in o.get("column_names") or [] if c["question"].lower() in by_label}}
    return ST.validate_design({**old, "title": v["title"], "instructions": v["instructions"], "fields": fields,
                               "intro_fields": intro, "look": look, "settings": st, "output": output})


# ----------------------------------------------------------------------------- what changed, in plain words
def _and(xs: list) -> str:
    xs = [str(x) for x in xs]
    return "none" if not xs else xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


_what = BL.what


RULE_WORDS = {
    "people_per_item": lambda v: f"{v} {'person' if v == 1 else 'people'} per item" if v else "everyone does every item",
    "items_per_person": lambda v: f"at most {v} items per person" if v else "no limit per person",
    "random_order": lambda v: "random order" if v else "the same order for everyone",
    "can_skip": lambda v: "skipping allowed" if v else "no skipping", "can_go_back": lambda v: "going back allowed" if v else "no going back",
    "ask_name": lambda v: "a name or ID asked" if v else "no name asked",
    "play_to_end": lambda v: "answers after playing to the end" if v else "answers any time",
    "no_repeat": lambda v: "no sentence twice in a row" if v else "sentences may repeat",
    "practice_items": lambda v: f"{v} practice items" if v else "no practice items",
    "final_comment": lambda v: "an overall comment at the end" if v else "no overall comment",
    "max_plays": lambda v: f"at most {v} play{'s' if v != 1 else ''} per clip" if v else "any number of plays",
    "seconds_per_item": lambda v: f"{v} seconds per item" if v else "no time limit",
    "keyboard_keys": lambda v: "keyboard keys shown" if v else "no keyboard keys",
}
CHART_WORDS = {"answers": "answer counts", "scores": "scores", "systems": "score per system", "agreement": "agreement",
               "timeline": "answers per day", "time-per-item": "time per item", "people": "items per person",
               "my-answers": "their own answers", "everyone": "everyone's answers", "my-time": "their time per item"}


FILE_WORDS = {"csv": "a CSV file (opens in Excel)", "json": "a JSON file", "jsonl": "a JSON lines file",
              "answer": "one row per answer", "item": "one row per item, each person's answer in their own column",
              "person": "one row per person, with all their answers"}
EXTRA_WORDS = {"path": "file paths", "group": "groups", "system": "systems", "name": "names", "before_start": "answers before starting",
               "time": "times", "seconds": "seconds per item", "plays": "plays", "majority": "majority answers"}


def changed(old: dict, new: dict) -> list:
    """The parts of the page that differ: title, instructions, questions, before_start, item_position, output, look.x, rules.x, charts.x."""
    out = [k for k in ("title", "instructions", "questions", "before_start", "item_position", "output") if old.get(k) != new.get(k)]
    for part in ("look", "rules", "charts"):
        out += [f"{part}.{k}" for k in new[part] if old[part].get(k) != new[part][k]]
    return out


def describe(old: dict, new: dict) -> str:
    parts = []
    if old["title"] != new["title"]:
        parts.append(f"title “{new['title']}”")
    ot, nt = [q["question"] for q in old["questions"]], [q["question"] for q in new["questions"]]
    oq, nq = {q["question"]: q for q in old["questions"]}, {q["question"]: q for q in new["questions"]}
    reworded = {}
    for i, q in enumerate(new["questions"]):   # same place and kind, new words
        if q["question"] not in oq and i < len(old["questions"]):
            o = old["questions"][i]
            if o["question"] not in nq and _same(o["question"], o["type"], q["question"], q["type"]):
                reworded[q["question"]] = o
    for q in new["questions"]:
        o = oq.get(q["question"]) or reworded.get(q["question"])
        if not o:
            parts.append(f"added “{q['question']}” ({_what(q)})")
            continue
        if o["question"] != q["question"]:
            parts.append(f"“{o['question']}” is now “{q['question']}”")
        if o["type"] != q["type"] or o.get("answers") != q.get("answers") or any(o.get(k) != q.get(k) for k in ("from", "to", "decimals", "count", "same", "several")):
            parts.append(f"“{q['question']}”: {_what(q)}")
        if q["type"] in ("scale", "slider", "grid", "rating_over_time") and o.get("words") != q.get("words"):
            parts.append(f"scale words: {_and(q['words'])}" if q.get("words") else "numbers only on the scale")
        if o["required"] != q["required"]:
            parts.append(f"“{q['question']}” {'required' if q['required'] else 'optional'}")
        if o.get("help", "") != q.get("help", ""):
            parts.append(f"explanation under “{q['question']}”" if q.get("help") else f"no explanation under “{q['question']}”")
        if q["type"] in ("text", "correct_text") and o["type"] == q["type"]:
            if o.get("big_box") != q.get("big_box"):
                parts.append(f"“{q['question']}”: {'a big box' if q.get('big_box') else 'a one-line box'}")
            if o.get("hint", "") != q.get("hint", ""):
                parts.append(f"hint in “{q['question']}”: “{q['hint']}”" if q.get("hint") else f"no hint in “{q['question']}”")
    gone = [t for t in ot if t not in nq and not any(o["question"] == t for o in reworded.values())]
    parts += [f"removed “{t}”" for t in gone]
    kept_old = [t for t in ot if t in nq]
    if kept_old != [t for t in nt if t in oq] and not gone:
        parts.append("new order of the questions")
    ob, nb = {b["question"] for b in old["before_start"]}, {b["question"] for b in new["before_start"]}
    if nb - ob:
        parts.append("asked before starting: " + _and([f"“{x['question']}”" for x in new["before_start"] if x["question"] not in ob]))
    if ob - nb:
        parts.append("no longer asked before starting: " + _and([f"“{b}”" for b in ob - nb]))
    if old["instructions"] != new["instructions"]:
        parts.append("new instructions")
    if old["item_position"] != new["item_position"]:
        parts.append("the item above the questions" if new["item_position"] == "top" else "the item beside the questions")
    lo, ln = old["look"], new["look"]
    if lo["colours"] != ln["colours"]:
        parts.append(f"{dict(forest='Green', dark='Night', light='Day').get(ln['colours'], ln['colours'].capitalize())} colours")
    if lo["accent"] != ln["accent"]:
        parts.append(f"accent colour {ln['accent']}" if ln["accent"] else "the mode's own accent")
    if lo["language"] != ln["language"]:
        parts.append("page in Persian" if ln["language"] == "fa" else "page in English")
    if lo["progress"] != ln["progress"]:
        parts.append(f"progress as a {ln['progress']}")
    parts += [RULE_WORDS[k](v) for k, v in new["rules"].items() if old["rules"].get(k) != v and k in RULE_WORDS]
    for k, label in (("last_screen", "last-screen charts"), ("results", "results charts")):
        if old["charts"][k] != new["charts"][k]:
            parts.append(f"{label}: {_and([CHART_WORDS[c] for c in new['charts'][k]])}" if new["charts"][k] else f"no {label}")
    fo, fn = old.get("output") or {}, new.get("output") or {}
    if (fo.get("format"), fo.get("rows")) != (fn.get("format"), fn.get("rows")):
        parts.append(f"results file: {FILE_WORDS[fn['format']]}, {FILE_WORDS[fn['rows']]}")
    if fo.get("include") != fn.get("include"):
        parts.append(f"the file has {_and([EXTRA_WORDS[x] for x in fn['include']])}" if fn.get("include") else "the file has the answers only")
    if fo.get("column_names") != fn.get("column_names"):
        parts.append("column names: " + _and([f"{c['column']} for “{c['question']}”" for c in fn["column_names"]]) if fn.get("column_names") else "the usual column names")
    if not parts and old != new:   # something small that has no words above
        parts.append("small changes to the page")
    return f"Done: {' · '.join(parts)}." if parts else ""
