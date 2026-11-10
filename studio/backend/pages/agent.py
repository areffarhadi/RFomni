"""The small model that picks and adapts default pages: Qwen3.5-9B on the 4090 (llama.cpp, see
/home/rf/models/qwen9b-4090/README.md; started with `qwen9b start`). Not the Omni model: these are small jobs.

Both calls ask for JSON in a fixed shape (the server enforces it while the model writes, so a reply always parses):
  pick  which default page fits a description, or "none"
  edit  a chat message -> the settings it changes. The model first splits the message into wishes and gives each
        the setting and value it needs; that made it far more reliable than asking for the changes in one go (9 of 12
        test requests right against 6, in about 1.5 s). Thinking is off: with a fixed shape it made things worse.
Every value is checked again here (settings.clean); a wrong one is dropped and reported, never applied.
"""
import json
import os
import re
import urllib.error
import urllib.request

from .. import blocks as BL
from . import catalogue as C
from . import design as D
from . import settings as S

URL = os.environ.get("OMNI_PAGES_MODEL_URL", "http://127.0.0.1:8003/v1").rstrip("/")
MODEL = os.environ.get("OMNI_PAGES_MODEL", "Qwen3.5-9B")
OFFLINE = ("The page assistant is not running right now. You can still change every setting by hand in the Settings tab, "
           "or try again in a few minutes.")


class Offline(RuntimeError):
    pass


def online() -> bool:
    try:
        with urllib.request.urlopen(URL.rsplit("/v1", 1)[0] + "/health", timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def _ask(system: str, user: str, schema: dict, max_tokens: int = 1500) -> dict:
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0.2, "top_p": 0.8, "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}}}
    req = urllib.request.Request(URL + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
    last = None
    for _ in range(2):  # a reply cut off at the token limit does not parse: once more
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.load(r)
        except (urllib.error.URLError, OSError) as e:
            raise Offline(OFFLINE) from e
        try:
            return json.loads(out["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            last = e
    raise Offline("The page assistant gave a broken answer twice. Please say it another way.") from last


def _str(n: int) -> dict:
    return {"type": "string", "maxLength": n}


# ----------------------------------------------------------------------------- pick a default page
def pick(area: str, modality: str, text: str) -> dict:
    """{"page": template id or None, "reply", "ask"} for a description of the task."""
    options = C.find(area, modality)
    if not options:
        return {"page": None, "reply": "There is no default page for this kind of data here yet.", "ask": ""}
    system = (
        f"A person is making a web page for {C.AREAS[area]} with {C.MODALITIES[modality].lower()} data. They describe "
        "their task. Choose the ready-made page that can do it, and answer in JSON:\n"
        "- page: the id of the page that is closest. Every page can be changed afterwards to any kind of question below, so "
        "choose none only when the task is not about labelling or rating this data at all (for example people record "
        "their own voice)\n"
        "- reply: one short sentence to the person: which page and why, or why none fits. Call a page by its name "
        "(the words in brackets), never by its id\n"
        "- ask: a question only when their description is too unclear to choose, else empty\n\nPages:\n"
        + "\n".join(f"- {t['id']} ({t['name']}): {t['pick']}" for t in options)
        + "\n\nKinds of question any page can be changed to:\n" + kinds_text(modality))
    schema = {"type": "object", "additionalProperties": False, "required": ["page", "reply", "ask"], "properties": {
        "page": {"enum": [t["id"] for t in options] + ["none"]}, "reply": _str(300), "ask": _str(300)}}
    out = _ask(system, f"Person: {text[:2000]}", schema, 400)
    reply = out["reply"].strip()
    for t in options:  # it still names pages by id now and then
        reply = reply.replace(f"{t['id']} page", f"{t['name']} page").replace(t["id"], f"{t['name']} page")
    return {"page": None if out["page"] == "none" else out["page"], "reply": reply, "ask": out["ask"].strip()}


# ----------------------------------------------------------------------------- change the settings
def _kind(s: dict) -> str:
    k = s["kind"]
    return {"text": "text", "long": "a longer text", "bool": "true or false", "list": "a list of words or sentences",
            "color": "#rrggbb or empty", "int": f"a whole number from {s.get('lo')} to {s.get('hi')}",
            "one": f"one of: {', '.join(s.get('options', []))}",
            "some": f"a list chosen from: {', '.join(s.get('options', []))}"}[k]


def _pasted(v: str, message: str) -> bool:
    """A description copied in as a title or question ("Street photos: people mark the vehicles."), not a real question
    the person wrote for it ("Which exercise is the person doing?")."""
    t = v.strip().lower().rstrip(".!")
    return len(t) > 25 and t in message.lower() and (len(t) > 90 or ":" in t or not v.strip().endswith("?"))


def intro(tpl: dict, current: dict, description: str) -> dict:
    """The first step after a page is made: from the person's description, only the title, the question and the classes
    (when it names them). Few choices suit the small model; edit() then does the rest of the description."""
    sp = S.spec(tpl["family"], tpl["modality"])
    keys = [k for k in ("title", "question", "classes", "scaleMin", "scaleMax") if k in sp]
    props = {k: (_str(sp[k].get("max", 200)) if sp[k]["kind"] in ("text", "long") else {"type": "integer"} if sp[k]["kind"] == "int"
                 else {"type": "array", "maxItems": 40, "items": _str(300)}) for k in keys}
    system = (f"A person made a page from a ready-made {tpl['name']} page: {tpl['pick']} From their description, write the "
              "page's title and the question the people who answer will see, in their words (for example 'Which vehicles do "
              "you see?'), never a copy of the description. Give the classes the description names, or a short list that "
              "fits the task when it names none; a scale only when it names one, else 0. Answer in JSON.\n\nNow:\n"
              + json.dumps({k: current.get(k) for k in keys}, ensure_ascii=False))
    schema = {"type": "object", "additionalProperties": False, "required": keys, "properties": props}
    out = _ask(system, f"Description: {description[:2000]}", schema, 1500)
    changes = {}
    for k in keys:
        v = out.get(k)
        if v in (None, "", [], 0) or (k in ("title", "question") and _pasted(str(v), description)):
            continue
        try:
            v = S.clean(sp[k], v)
        except S.Bad:
            continue
        if v != current.get(k):
            changes[k] = v
    return {"changes": changes}


GENERIC_EXAMPLES = [
    ["add a comment box people may leave empty", {"comment": "optional"}],
    ["show them a chart of their answers at the end", {"finishCharts": ["my-answers"]}],
    ["dark colours please", {"theme": "dark"}],
]


def edit(tpl: dict, current: dict, message: str, history: list, data_line: str = "") -> dict:
    """One chat message -> {"changes": {setting: value}, "reply", "ask", "unsupported", "problems": [...]}."""
    sp = S.spec(tpl["family"], tpl["modality"])
    examples = "\n".join(f"Person: {req} -> {json.dumps([{'setting': k, 'value': v} for k, v in ch.items()], ensure_ascii=False)}"
                         for req, ch in tpl.get("examples", []) + GENERIC_EXAMPLES)
    system = (
        f"You help a person adapt a ready-made web page: {tpl['pick']} You can only change the page's settings, listed "
        "below. You cannot add new features or write code.\n\nSettings (name: what it does; kind of value):\n"
        + "\n".join(f"- {k}: {s['desc']}; {_kind(s)}" for k, s in sp.items())
        + f"\n\nExamples:\n{examples}\n\n"
        "Answer in JSON:\n"
        "- wishes: split the message into its parts. For each part give the setting it changes and the new value. One "
        "part can need several entries (for example a 1 to 7 scale needs scaleMin and scaleMax). Use setting none for a "
        "part no setting can do. Empty when the message changes nothing. Change only what the message asks for: "
        "'also ask X' adds X and keeps every question there is. The title, instructions and questions are read by the "
        "people who answer: write them for them ('How natural does the voice sound?'), never in the person's own words "
        "('rate my samples'). When a change makes the instructions wrong (they mention the old scale or classes), "
        "also give new instructions. Never turn on anything the message does not ask for (a confidence question, Not "
        "sure, Other, comments, practice items, charts, an accent colour).\n"
        "- reply: one or two friendly sentences to the person about what you changed (only what is in wishes)\n"
        "- ask: a question when you need information from the person to continue, else empty. Plain words only: never "
        "setting names or values such as notSure or (confidence: true)\n"
        "- unsupported: what the person wants that no setting can do, in a few words, else empty")
    convo = "\n".join(f"{'Person' if m['role'] == 'owner' else 'You'}: {m['text'][:400]}" for m in history[-4:])
    user = "\n".join(x for x in ["Current settings:", json.dumps(current, ensure_ascii=False), data_line,
                                 f"Earlier in this chat:\n{convo}" if convo else "", f"Person: {message[:3000]}"] if x)
    schema = {"type": "object", "additionalProperties": False, "required": ["wishes", "reply", "ask", "unsupported"], "properties": {
        "wishes": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "additionalProperties": False, "required": ["wish", "setting", "value"], "properties": {
                "wish": _str(160), "setting": {"enum": list(sp) + ["none"]},
                "value": {"anyOf": [_str(1500), {"type": "integer"}, {"type": "boolean"},
                                    {"type": "array", "maxItems": 60, "items": _str(500)}]}}}},
        "reply": _str(500), "ask": _str(300), "unsupported": _str(300)}}
    out = _ask(system, user, schema, 3000)
    if not out["wishes"] and not out["ask"] and not out["unsupported"]:   # it says what it would do but lists nothing
        out = _ask(system, user + "\n\nList every change as a wish, with its setting and its new value.", schema, 3000)
    changes, problems = {}, []
    for w in out["wishes"]:
        name = w["setting"]
        if name not in sp:
            continue
        try:
            v = S.clean(sp[name], w["value"])
        except S.Bad as e:
            problems.append(f"{name} {e}")
            continue
        if name == "accent" and not re.search(r"#[0-9a-f]{3,6}\b|accent|button", message, re.I):
            continue   # it likes to add an accent to "dark colours please"; only when the person names one
        if name in ("question", "title", "secondQuestion") and isinstance(v, str) and _pasted(v, message):
            continue   # the person's own description pasted in as the question: people answering need a real question
        if v != current.get(name):
            changes[name] = v
    # the same new text in two settings: it was meant for the one that was empty (e.g. "also ask X" filling
    # secondQuestion should not also overwrite question)
    for a in list(changes):
        for b in list(changes):
            if a != b and a in changes and b in changes and isinstance(changes[a], str) and changes[a] == changes[b] \
                    and current.get(a) and not current.get(b):
                del changes[a]
    blank = lambda t: "" if re.match(r"^\W*(none|n/?a|null|nothing|no|-|empty)?\W*$", t or "", re.I) else _plain(t, sp)
    return {"changes": changes, "reply": _plain(out["reply"], sp), "ask": blank(out["ask"]),
            "unsupported": blank(out["unsupported"]), "problems": problems}


def _plain(text: str, sp: dict) -> str:
    """The model's words for the person without setting names in them ("(confidence: true)", "notSure")."""
    t = re.sub(r"\s*\((?:\w+\s*[:=]\s*[\w\"'#.-]+(?:,\s*)?)+\)", "", text.strip())
    names = [re.escape(k) for k in sp if re.search(r"[A-Z]", k)]
    if names:
        t = re.sub(r"\b(" + "|".join(names) + r")\b", lambda m: re.sub(r"(?<!^)([A-Z])", r" \1", m[1]).lower(), t)
    return re.sub(r"\s{2,}", " ", t).strip()


# ----------------------------------------------------------------------------- edit the page itself
# Task sharing and Subjective test pages. Two jobs, each in the form the small model does best:
#   page_edit   the page's content (title, instructions, questions, questions before starting, where the item is):
#               it gets that part of the page in plain words (design.view) and returns it rewritten, free to add,
#               remove, move, reword or change any question
#   rules_edit  the look, the rules and the charts: a short list of wishes, one value each (as edit() above)
# The server checks both (design.clean / build) and keeps out what the message did not ask for (service._guard).
ITEM = {"voice": "a voice recording", "text": "a short text", "image": "a picture", "video": "a video clip"}
NOTHING = re.compile(r"^\W*(none|n/?a|null|nothing|no|-|empty|not applicable)?\W*$", re.I)
CONTENT = ("title", "instructions", "questions", "before_start", "item_position", "output")


def _obj(props: dict, required: list = None) -> dict:
    return {"type": "object", "additionalProperties": False, "required": required if required is not None else list(props), "properties": props}


def content_schema(modality: str = "") -> dict:
    q = _obj({"question": _str(300), "type": {"enum": [b.kind for b in BL.for_media(modality)]},
              "answers": {"type": "array", "maxItems": 200, "items": _str(60)}, "help": _str(300), "required": {"type": "boolean"},
              "from": {"type": "integer"}, "to": {"type": "integer"}, "words": {"type": "array", "maxItems": 11, "items": _str(30)},
              "big_box": {"type": "boolean"}, "hint": _str(120), "decimals": {"type": "integer"}, "count": {"type": "integer"},
              "same": {"type": "boolean"}, "several": {"type": "boolean"}},
             ["question", "type", "answers", "required"])
    b = _obj({"question": _str(200), "type": {"enum": ["buttons", "text"]}, "answers": {"type": "array", "maxItems": 20, "items": _str(60)},
              "required": {"type": "boolean"}})
    return _obj({"page_title": _str(100), "instructions": _str(1500),
                 "questions": {"type": "array", "minItems": 1, "maxItems": D.MAX_QUESTIONS, "items": q},
                 "before_start": {"type": "array", "maxItems": D.MAX_BEFORE, "items": b},
                 "item_position": {"enum": ["top", "side"]},
                 "output": _obj({"format": {"enum": ["csv", "json", "jsonl"]}, "rows": {"enum": ["answer", "item", "person"]},
                                 "include": {"type": "array", "items": {"enum": ["path", "group", "system", "name", "before_start",
                                                                                 "time", "seconds", "plays", "majority"]}},
                                 "column_names": {"type": "array", "maxItems": 12, "items": _obj({"question": _str(300), "column": _str(60)})}})})


def kinds_text(modality: str) -> str:
    """The question types this page's data can have, one line each (backend/blocks.py)."""
    names = {"any": "For any item", "compare": "Comparisons: several items on one screen", "time": "On the recording or video itself",
             "picture": "On the picture itself", "text": "On the text itself"}
    out, last = [], None
    for b in BL.for_media(modality):
        group = "basic" if b.group in ("basic", "any") else b.group
        if group != last:
            out.append("" if group == "basic" else f"{names[b.group]}:")
            last = group
        out.append(f"- {b.kind}: {b.does}")
    return "\n".join(x for x in out if x)


def _content_system(tpl: dict) -> str:
    item = ITEM.get(tpl["modality"], "an item")
    return (
        f"You edit a web page that people use to label data. Each person sees one item at a time ({item}) and answers "
        f"the questions shown with it. The page started as a ready-made page: {tpl['pick']} Its content is described "
        "in JSON. Change it the way the person asks and return all of it.\n\n"
        "You may change: page_title (the heading at the top of the page), the instructions, the questions (add, remove, "
        "reorder, reword, change their type or answers), the questions asked once before people start (before_start) and "
        "where the item is shown "
        "(item_position: top = above the questions, side = beside them) and the results file (output). Colours, how "
        "many people answer, charts and "
        "other rules are handled elsewhere: ignore those parts of the message.\n"
        "Question types (type: what people do):\n" + kinds_text(tpl["modality"]) + "\n"
        "`answers` holds a type's answers, labels or aspects. A sliding bar, knob or continuous rating is a slider; "
        "\"from 1 to 5\" or a rating is a scale; a slider people move while the clip plays is rating_over_time. When the "
        "person names a test or a type above (MUSHRA, ABX, CMOS, A/B, best-worst, highlights, moments…), use that type: one "
        "question of it does the whole test, in place of the question it replaces. Use a type other than buttons, "
        "checkboxes, scale or text only when the "
        "person asks for what it does. A page has at most one comparison, and at most one type that works on the item "
        "itself (not both); with a comparison, the other questions are about the whole screen. "
        "help = a short explanation under the question. "
        "A comment box is a text question that is not required. Every box, window or field on the page is a question: "
        "its `question` is the words shown above it, so the title, name or label of a box or question is its `question`. "
        "To rename a box, change its `question`. Change page_title only when the person speaks of the page's own title "
        "or heading.\n"
        "output: the results file the person downloads with all the answers. format: csv (opens in Excel), json, or "
        "jsonl (one JSON record per line). rows: answer = one row per answer (an item and one person); item = one row "
        "per item, with each person's answer under their own key or column; person = one row per person with all "
        "their answers. include: extra details in the file (path of the item, group, system, name of the person, "
        "before_start answers, time answered, seconds spent, plays, majority = the most common answer per item, checks = "
        "how many items with known answers each person got right). "
        "column_names: a column name for a question. \"Each person's label as a column\" or \"a column per user\" means "
        "rows = item.\n"
        "before_start: questions about the people answering (their age, first language, headphones…), asked once. "
        "Something about each item (the speaker's gender, the emotion, what is in the picture) is a question, never "
        "before_start.\n"
        "The person adds two things themselves in the Results tab, never you: items with known answers (attention "
        "checks) and the blind key (which system made each clip). When they ask for these, tell them where.\n\n"
        "Keep everything the person does not ask to change exactly as it is. \"this box\" or \"it\" is the question "
        "spoken of last in the chat. When the person says you did not do what they asked, or asks it again, do it now in "
        "the page. Never say you changed something unless the page you return shows it. Questions and instructions are "
        "read by the "
        "people who answer: write them for them (\"Which emotion do you hear?\"), never in the person's own words. When a "
        "change makes the instructions wrong, rewrite them to match. When the person lists the answers, use exactly those. "
        "The page cannot paint masks, follow things from frame to frame in a video, or record people's voices. Never add "
        "a question that asks for these: say it in cannot instead.\n"
        "Answer in JSON: page = the new content; answer = your reply to the person in one or two friendly sentences, "
        "like in a chat: what you changed, or the answer to their question, or that the page already does what they ask "
        "(then say where); when they ask for something the page cannot do, say so and offer the closest thing it can do; "
        "ask = a question when you need to know more before you can do it, "
        "else empty; cannot = only when the person asks for something the page cannot do, say what in a few words, else "
        "empty.")


def page_edit(tpl: dict, view: dict, message: str, history: list, data_line: str = "", first: bool = False,
              note: str = "") -> dict:
    """The page's content changed by one chat message (or, `first`, by the description given when the page was made)
    -> {"content", "ask", "cannot"}; not yet checked. `note`: what was wrong with its last try at this message."""
    schema = _obj({"page": content_schema(tpl["modality"]), "answer": _str(600), "ask": _str(300), "cannot": _str(300)})
    convo = "\n".join(f"{'Person' if m['role'] == 'owner' else 'You'}: {m['text'][:300]}" for m in history[-6:])
    say = (f"This is how I describe my task: “{message[:2000]}”. Adapt the page to it: a page_title, the questions and their "
           "answers (the classes, the scale…) as my description says; when it names no answers, a short list that fits. "
           "Write the page_title and the questions in your own short words, never my sentence. When my description does "
           "not say what people should answer, keep the questions as they are and ask me in ask. Change anything else "
           "only when my description asks for it." if first else message[:3000])
    page = {("page_title" if k == "title" else k): view[k] for k in CONTENT}
    user = "\n".join(x for x in ["The page content now:", json.dumps(page, ensure_ascii=False), data_line,
                                 f"Earlier in this chat:\n{convo}" if convo else "", f"Person: {say}",
                                 f"(Your last try was wrong: {note})" if note else ""] if x)
    out = _ask(_content_system(tpl), user, schema, 4000)
    content = {("title" if k == "page_title" else k): v for k, v in out["page"].items()}
    blank = lambda t: "" if NOTHING.match(t or "") else _plain(t, {})
    return {"content": content, "answer": blank(out["answer"]), "ask": blank(out["ask"]), "cannot": blank(out["cannot"])}


ANSWERS_ARE = {"grid": "the aspects people rate, one per row", "ranking": "the answers people put in order",
               "dropdown": "the answers in the list", "time_ranges": "the labels people give each part (empty for none)",
               "time_points": "the labels people give each moment (empty for none)", "boxes": "the labels of the boxes, one per kind of thing",
               "points": "the labels of the points", "regions": "the labels of the regions",
               "highlights": "the labels, one for each kind of thing people highlight (names of people, places and dates = person, place, date)"}


def block_fill(tpl: dict, view: dict, message: str, kind: str) -> dict:
    """One question of a kind the person named ("a MUSHRA test", "highlight the names"), when the page the model
    returned lacks it: a small call that only writes that question and says which question it takes the place of.
    -> {"question", "answers", "replaces": index or -1, "count", "several", "from", "to"}"""
    b = BL.KIND[kind]
    item = ITEM.get(tpl["modality"], "an item")
    listing = "\n".join(f"{i + 1}. {q['question']} ({q['type']})" for i, q in enumerate(view["questions"]))
    system = (f"A person changes a web page where people label data; each person sees {item} at a time. They asked for a "
              f"question of the type {kind}: {b.does}. Write that one question in JSON: question = the words the people "
              "who answer read, short and written for them; answers = " + ANSWERS_ARE.get(kind, "empty") +
              "; replaces = the number of the page's question it takes the place of (when the person says instead, change "
              "it to, make it, turn it into, or it asks the same thing in another way), or -1 to add it as a new question"
              + ("; count = how many items on one screen" if "count" in b.keys else "")
              + ("; several = true when people may pick more than one" if "several" in b.keys else "")
              + ("; from and to = the lowest and highest number" if "from" in b.keys else "") + ".\n\nThe page's questions now:\n" + listing)
    props = {"question": _str(300), "answers": {"type": "array", "maxItems": 40, "items": _str(60)}, "replaces": {"type": "integer"}}
    for k in ("count", "from", "to"):
        if k in b.keys:
            props[k] = {"type": "integer"}
    if "several" in b.keys:
        props["several"] = {"type": "boolean"}
    out = _ask(system, f"Person: {message[:2000]}", _obj(props), 600)
    r = out.get("replaces")
    return {**out, "replaces": r - 1 if isinstance(r, int) and 1 <= r <= len(view["questions"]) else -1}


# the look, rules and charts of a page, by their place in the view
RULES_SPEC = {
    "look.colours": S.S("one", "colour mode of the page", options=S.THEMES),
    "look.accent": S.S("color", "accent colour of buttons and highlights as #rrggbb; empty = the colour mode's own"),
    "look.language": S.S("one", "language of the page's buttons: en = English, fa = Persian (right to left)", options=["en", "fa"]),
    "look.progress": S.S("one", "how people see their progress: a bar or a ring", options=["bar", "ring"]),
    "rules.people_per_item": S.S("int", "how many different people answer each item; 0 = everyone answers every item", lo=0, hi=50),
    "rules.items_per_person": S.S("int", "the most items one person is given; 0 = no limit", lo=0, hi=100000),
    "rules.random_order": S.S("bool", "each person gets the items in a different random order"),
    "rules.can_skip": S.S("bool", "people may skip an item they cannot answer"),
    "rules.can_go_back": S.S("bool", "people may go back and change an earlier answer"),
    "rules.ask_name": S.S("bool", "ask people for their name or ID before they start"),
    "rules.play_to_end": S.S("bool", "the answers unlock only after the recording or video has played to the end"),
    "rules.no_repeat": S.S("bool", "never the same sentence twice in a row (clips of one sentence by several systems)"),
    "rules.practice_items": S.S("int", "the first N items are practice: everyone gets them first and they are not scored", lo=0, hi=10),
    "rules.final_comment": S.S("bool", "a box for overall comments on the last screen"),
    "rules.max_plays": S.S("int", "how many times people may play each recording or video; 0 = any number", lo=0, hi=20),
    "rules.seconds_per_item": S.S("int", "seconds people have for each item before the page moves on; 0 = no limit", lo=0, hi=3600),
    "rules.keyboard_keys": S.S("bool", "show keyboard keys (1, 2, 3…, A and B) on the answers and let people answer with them"),
    "charts.last_screen": S.S("some", "charts on the last screen people see: my-answers = their own answers, everyone = how "
                              "everyone answered, my-time = how long they took per item", options=list(D.CHARTS["last_screen"])),
    "charts.results": S.S("some", "charts on the owner's results page: answers = counts per answer, scores = how often each "
                          "score was given, systems = mean score per system, agreement = how often people agree, timeline = "
                          "answers per day, time-per-item = how long people take, people = items done per person",
                          options=list(D.CHARTS["results"])),
}
RULES_EXAMPLES = [
    ["3 people per clip", {"rules.people_per_item": 3}],
    ["dark colours please", {"look.colours": "dark"}],
    ["show them a chart of their answers at the end", {"charts.last_screen": ["my-answers"]}],
    ["they must listen to the whole clip and can go back", {"rules.play_to_end": True, "rules.can_go_back": True}],
]


def rules_edit(view: dict, modality: str, message: str) -> dict:
    """The look, rules and charts a chat message asks for -> {"changes": {"rules.x": value}, "problems"}."""
    sp = {k: v for k, v in RULES_SPEC.items() if k.split(".")[1] in view[k.split(".")[0]]}
    cur = {k: view[k.split(".")[0]][k.split(".")[1]] for k in sp}
    examples = "\n".join(f"Person: {req} -> {json.dumps([{'setting': k, 'value': v} for k, v in ch.items()])}" for req, ch in RULES_EXAMPLES)
    system = ("A person changes a web page that people use to label data. Here only these settings matter; the questions "
              "and texts of the page are handled elsewhere, so ignore those parts of the message.\n\nSettings (name: what it "
              "does; kind of value):\n" + "\n".join(f"- {k}: {s['desc']}; {_kind(s)}" for k, s in sp.items())
              + f"\n\nExamples:\n{examples}\n\nAnswer in JSON: wishes = for each part of the message that one of these settings "
              "does, the setting and its new value; empty when the message asks for none of them. Never change a setting "
              "the message does not ask for.")
    schema = {"type": "object", "additionalProperties": False, "required": ["wishes"], "properties": {
        "wishes": {"type": "array", "maxItems": 10, "items": _obj({"wish": _str(160), "setting": {"enum": list(sp)},
                   "value": {"anyOf": [_str(40), {"type": "integer"}, {"type": "boolean"}, {"type": "array", "maxItems": 8, "items": _str(30)}]}})}}}
    out = _ask(system, f"Current settings: {json.dumps(cur)}\nPerson: {message[:2000]}", schema, 800)
    changes, problems = {}, []
    for w in out["wishes"]:
        try:
            v = S.clean(sp[w["setting"]], w["value"])
        except S.Bad as e:
            problems.append(f"{w['setting']} {e}")
            continue
        if w["setting"] == "look.accent" and not re.search(r"#[0-9a-f]{3,6}\b|accent|button", message, re.I):
            continue
        if v != cur[w["setting"]]:
            changes[w["setting"]] = v
    return {"changes": changes, "problems": problems}
