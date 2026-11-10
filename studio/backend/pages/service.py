"""A page made from a default: create it, adapt it in a chat, undo, change settings by hand, approve it.

The page lives on its shared task (Task sharing, Subjective tests) or collection project (Data collection), under
"page":
    {"template", "area", "modality", "description",
     "versions": [...],   # the last is current
     "approved": {"version", "at"} or None}
Task sharing and Subjective test pages are edited as a whole: a version is {"view", "design", "at", "by", "changes"},
the page in plain words (design.py) and the design the volunteer page shows. The page assistant may change any part
of it. Data collection pages keep their settings: a version is {"settings", "at", "by", "changes"}, compiled into the
project's design.
"""
import re
from difflib import SequenceMatcher

from .. import blocks as BL
from .. import collect as CO
from .. import shared_tasks as ST
from ..util import now_iso
from . import agent
from . import catalogue as C
from . import design as D
from . import settings as S

MAX_VERSIONS = 60
LONG_LIST = 20   # a list setting longer than this (e.g. 300 uploaded prompts) is not shown to the model or changed by it


class NotPage(ValueError):
    pass


# ----------------------------------------------------------------------------- loading and saving
def _load(kind: str, oid: str) -> dict:
    obj = ST.get(oid) if kind == "shared" else CO.get(oid)
    if not obj.get("page"):
        raise NotPage("This page was not made from a default page")
    return _whole(obj) if kind == "shared" else obj


def _whole(obj: dict) -> dict:
    """A Task sharing or Subjective test page made before pages were edited as a whole: each version's settings
    become its page."""
    tpl = C.get(obj["page"]["template"])
    for v in obj["page"]["versions"]:
        if "view" not in v:
            design = ST.validate_design(S.compile_task(tpl, v["settings"]))
            v.update(view=D.view(design, v["settings"].get("practiceItems", 0), tpl["modality"]), design=design)
            v.pop("settings")
        v["view"].setdefault("output", D.output_view(v["design"]))   # made before the results file was part of the page
    return obj


def _lock(kind: str):
    return ST._lock if kind == "shared" else CO._lock


def _compile(kind: str, tpl: dict, s: dict) -> dict:
    """The design of the settings, checked by the store it goes to (raises ValueError when it does not fit)."""
    if kind == "shared":
        return ST.validate_design(S.compile_task(tpl, s))
    return CO.validate_design(S.compile_collect(tpl, s))


def _save(kind: str, obj: dict) -> dict:
    return ST._save(obj) if kind == "shared" else CO._save(obj)


def current(obj: dict) -> dict:
    """The current page: its view (Task sharing, Subjective tests) or its settings (Data collection)."""
    v = obj["page"]["versions"][-1]
    return v["view"] if "view" in v else v["settings"]


def _practice(tid: str, n: int) -> None:
    """The first n items of a page made from a default are its practice items (shown first, not scored)."""
    with ST._lock:
        its = ST.items(tid)
        for i, it in enumerate(its):
            it.pop("practice", None)
            if i < n:
                it["practice"] = True
        ST.write_json(ST._dir(tid) / "items.json", {"items": its})


def _new_version(kind: str, obj: dict, settings: dict, by: str, changes: list) -> dict:
    tpl = C.get(obj["page"]["template"])
    settings = S.check(tpl["family"], tpl["modality"], settings)
    obj["design"] = _compile(kind, tpl, settings)
    vs = obj["page"]["versions"]
    vs.append({"settings": settings, "at": now_iso(), "by": by, "changes": changes})
    del vs[:-MAX_VERSIONS]
    if kind == "shared" and "practiceItems" in changes:
        _practice(obj["id"], settings.get("practiceItems", 0))
    return obj


def _new_view(obj: dict, view: dict, by: str, changes: list) -> dict:
    """A new version of a Task sharing or Subjective test page (view already clean); raises ValueError when the
    volunteer page cannot show it."""
    old_practice = current(obj)["rules"].get("practice_items", 0)
    design = D.build(view, obj["design"])
    obj["design"] = design
    vs = obj["page"]["versions"]
    vs.append({"view": view, "design": design, "at": now_iso(), "by": by, "changes": changes})
    del vs[:-MAX_VERSIONS]
    if view["rules"].get("practice_items", 0) != old_practice:
        _practice(obj["id"], view["rules"].get("practice_items", 0))
    return obj


def state(obj: dict) -> dict:
    """What the studio shows about the page: the default it came from, the page or its settings form, versions, approval."""
    if obj.get("kind") in ST.KINDS:   # a shared task
        obj = _whole(obj)
    p = obj["page"]
    tpl = C.get(p["template"])
    whole = "view" in p["versions"][-1]
    return {"template": C.public(tpl), "settings": None if whole else current(obj), "view": current(obj) if whole else None,
            "form": None if whole else C.form(tpl), "version": len(p["versions"]),
            "versions": [{"at": v["at"], "by": v["by"], "changes": v.get("changes") or [], "saved": v.get("saved")} for v in p["versions"]],
            "approved": p.get("approved"), "area": p["area"], "modality": p["modality"], "online": None,
            "kinds": BL.menu(p["modality"]) if whole else None}


def _data_line(kind: str, obj: dict) -> str:
    if kind == "collect":
        n = sum(len(lg["prompts"]) for lg in obj["design"]["languages"])
        return f"The page has {n} prompts."
    d = ST.data_summary(obj["id"])
    if not d["total"]:
        return "Data: none uploaded yet."
    return f"Data: {d['total']} items ({', '.join(f'{n} {k}' for k, n in d['by_kind'].items())})."


# ----------------------------------------------------------------------------- the steps
def create(user: dict, area: str, modality: str, template: str, description: str = "") -> dict:
    """A new task or collection project with the default page; the description, if any, is the first chat message."""
    tpl = C.get(template)
    if tpl["area"] != area or tpl["modality"] != modality:
        raise ValueError("That default page belongs to another area or kind of data")
    s = dict(tpl["settings"])
    kind = "collect" if area == "collect" else "shared"
    page = {"template": template, "area": area, "modality": modality, "description": description[:2000],
            "versions": [{"settings": s, "at": now_iso(), "by": "default", "changes": []}], "approved": None}
    if kind == "shared":
        obj = ST.create(user, s["title"], "listening" if area == "listen" else "labelling")
        design = _compile(kind, tpl, s)
        page["versions"] = [{"view": D.view(design, s.get("practiceItems", 0), modality), "design": design,
                             "at": now_iso(), "by": "default", "changes": []}]
        with ST._lock:
            obj = ST.get(obj["id"])
            obj["page"] = page
            obj["design"] = design
            obj = ST._save(obj)
    else:
        obj = CO.create(user, s["title"], _compile(kind, tpl, s))
        with CO._lock:
            obj = CO.get(obj["id"])
            obj["page"], obj["chat"] = page, []
            obj = CO._save(obj)
    first = None
    if description.strip():
        try:   # the description says what the task is; it is not a list of changes, so the model is told so
            first = chat(kind, obj["id"], description, intro=True)
        except agent.Offline as e:
            first = {"reply": str(e), "offline": True}
    return {"kind": kind, "id": obj["id"], "first": first}


# after the first step (agent.intro: title, question, classes), the rest of the description
FIRST = ("Here is how I describe my task: “{}”. The title, the question and the classes are already set. Set only what "
         "else my description says, for example how many people answer each item, what people are asked before they "
         "start, a comment box, the page language, the ways to read, or how long recordings are. Do not turn on anything "
         "my description does not mention.")
FIRST_KEYS = ("title", "question", "classes", "prompts")
# The small model also turns on things a description never mentions (a name, a comment box, Persian buttons). In the
# description, a setting may change only when the description says something about it.
HINTS = {
    "peoplePerItem": r"\d+\s*(people|persons|annotators|raters|listeners|labell?ers|per)|everyone|each person",
    "itemsPerPerson": r"per person|each person|at most|maximum|limit", "peoplePerPrompt": r"\d+\s*(people|persons|speakers)\s*(per|each|for|record)|everyone records",
    "askAbout": r"native|first language|\bage\b|gender|headphone|hearing|expert|speaker of|accent",
    "askName": r"\bname\b|\bid\b", "comment": r"comment|note|remark|feedback", "finalComment": r"comment|feedback",
    "language": r"page in|interface|buttons|persian page|farsi page", "progress": r"progress|ring",
    "finishCharts": r"chart|graph|plot|statistic", "charts": r"chart|graph|plot|statistic",
    "theme": r"colou?r|dark|light|night|navy|cream|charcoal|green|sand|theme", "accent": r"#[0-9a-f]{3,6}|accent",
    "randomOrder": r"random|shuffle|order", "allowSkip": r"skip", "allowBack": r"go back|change (their|an) answer",
    "practiceItems": r"practi|training|warm.?up", "mustPlay": r"whole|the end|full|complete|entire",
    "noRepeat": r"repeat|twice|same sentence", "multiple": r"several|multiple|more than one|all that apply|any number",
    "notSure": r"not sure|unsure|don.?t know|uncertain", "other": r"\bother\b|something else",
    "confidence": r"how sure|confiden|certain", "secondQuestion": r"also|second|another|as well",
    "extraRating": r"also|rate|rating|score", "scaleMin": r"\d\s*(to|-|–)\s*\d|scale|point", "scaleMax": r"\d\s*(to|-|–)\s*\d|scale|point",
    "pointLabels": r"label|word|bad|excellent|poor|good", "ways": r"voice|emotion|happy|sad|angry|style|whisper|ways?\b",
    "wayOrder": r"mix|shuffl|order|together", "minSeconds": r"second|minute|long|short|length",
    "maxSeconds": r"second|minute|long|short|length", "sampleRate": r"khz|quality|sample rate|48|16",
    "mode": r"\bread|answer|question", "consent": r"consent|agree|permission", "placeholder": r"hint|placeholder",
    "multiline": r"long|short|one line|sentence|paragraph", "classHelp": r"defin|explain|mean",
}


def _mentioned(name: str, value, text: str) -> bool:
    if name == "languageName":
        return str(value).lower() in text.lower()
    pat = HINTS.get(name)
    return pat is None or re.search(pat, text, re.I) is not None


# options of a multiple-choice setting that the model may add only when the message names them (it likes to add
# everyone's answers to "a chart of their answers")
OPTION_HINTS = {
    "finishCharts": {"my-answers": r"their (own )?answers|own answers|my answers|chart|graph|plot", "everyone": r"everyone|others|other people|all (the )?(people|answers)|average|compare",
                     "my-time": r"\btime\b|how long|speed|fast"},
    "charts": {"answers": r"answer|count", "scores": r"score|rating|distribution|histogram", "systems": r"system|model",
               "agreement": r"agree", "timeline": r"per day|over time|timeline|each day|daily", "time-per-item": r"\btime\b|how long|duration",
               "people": r"per person|each person|worker|annotator|who did"},
    "askAbout": {"native speaker": r"native", "first language": r"first language|mother tongue|\bl1\b", "age": r"\bage\b|how old",
                 "gender": r"gender|\bsex\b", "headphones": r"headphone|earphone|headset|listening (with|device)", "hearing problems": r"hearing",
                 "expertise": r"expert|experience|familiar", "name": r"\bname\b|\bid\b", "accent": r"accent|dialect|region"},
}


def _tidy(changes: dict, cur: dict, message: str) -> dict:
    """What the model changed, held to the message: no options it does not name, and a new scale that keeps the page
    right (the end words, the numbers in the instructions) instead of words the model makes up for every point."""
    changes = dict(changes)
    for name, hints in OPTION_HINTS.items():
        if isinstance(changes.get(name), list):
            old = cur.get(name) or []
            keep = [o for o in changes[name] if o in old or re.search(hints.get(o, "."), message, re.I)]
            if keep == old:
                changes.pop(name)
            else:
                changes[name] = keep
    lo0, hi0 = cur.get("scaleMin"), cur.get("scaleMax")
    lo, hi = changes.get("scaleMin", lo0), changes.get("scaleMax", hi0)
    if lo0 is not None and hi0 is not None and (lo, hi) != (lo0, hi0):
        words = cur.get("pointLabels") or []
        if not re.search(HINTS["pointLabels"] + r"|anchor", message, re.I):
            changes.pop("pointLabels", None)
            if len(words) > 2:
                changes["pointLabels"] = [words[0], words[-1]]
        for k in ("instructions", "question", "secondQuestion"):
            text = changes.get(k, cur.get(k))
            if not isinstance(text, str) or not text:
                continue
            new = re.sub(rf"\b{lo0}(\s*(?:to|-|–)\s*){hi0}\b", f"{lo} to {hi}", text)
            if hi != hi0:
                new = re.sub(rf"\b{hi0}(\s+(?:means|is|=))", rf"{hi}\1", new)
            if lo != lo0:
                new = re.sub(rf"\b{lo0}(\s+(?:means|is|=))", rf"{lo}\1", new)
            if new != text:
                changes[k] = new
    return changes


# A message may change these parts of a page only when it says something about them: the small model returns the
# whole page and now and then changes more than it was asked (a name question, other colours). The questions and the
# instructions that go with them are free.
GUARD = {
    "title": r"title|heading|call (it|the page)|name (of )?the (page|task)",
    "item_position": r"\btop\b|above|below|beside|\bside\b|under|\bleft\b|\bright\b|layout|position",
    "output": r"file|json|csv|excel|spreadsheet|export|download|column|output|receive|format|\brows?\b|\brecords?\b|per (person|user|item|answer)|each (person|user)",
    "before_start": r"before|\bstart|beginning|ask (them|people|participants|listeners|raters|annotators|volunteers|about)|"
                    r"their (own )?(age|gender|first language|native|mother tongue|accent|name|headphones)|native|headphone|"
                    r"hearing|demograph|participant|about (them|themselves)|expertise|experience",
    "look.colours": r"colou?r|dark|light|night|navy|cream|charcoal|green|sand|theme|mode",
    "look.accent": r"#[0-9a-f]{3,6}|accent|button colou?r|colou?r of",
    "look.language": r"persian|farsi|english|language|rtl|right to left",
    "look.progress": r"progress|ring|\bbar\b",
    "rules.people_per_item": r"\d+\s*(people|persons|annotators|raters|listeners|labell?ers|per)|everyone|each person|per (item|clip|text|image|picture|video|recording)",
    "rules.items_per_person": r"\d+\s*(items|clips|texts|images|pictures|videos|recordings)\s*(per|each|for|at most)|at most|limit|maximum|\bmax\b",
    "rules.random_order": r"random|shuffle|order", "rules.can_skip": r"skip", "rules.can_go_back": r"go back|change (their|an|the) answer|back",
    "rules.ask_name": r"\bname\b|\bid\b", "rules.play_to_end": r"whole|the end|full|complete|entire|play",
    "rules.no_repeat": r"repeat|twice|same sentence", "rules.practice_items": r"practi|training|warm.?up",
    "rules.final_comment": r"final|overall|at the end|last screen|end of",
    "rules.max_plays": r"\bplay|listen (to it )?(only )?(once|twice)|replay|times|once|twice",
    "rules.seconds_per_item": r"\bseconds?\b|minutes?|time limit|timer|how long|\bfast\b|quick|count ?down",
    "rules.keyboard_keys": r"\bkeys?\b|keyboard|shortcut|press",
    "charts.last_screen": r"chart|graph|plot|statistic|at the end|last screen|finish",
    "charts.results": r"chart|graph|plot|statistic|results?",
}
FILE_PARTS = {"format": r"json|csv|excel|spreadsheet|format|file type|ndjson|\blines\b",
              "rows": r"\brows?\b|per (person|user|item|answer|clip|file|recording)|each (person|user|item)|columns?|\brecords?\b|one (line|entry)",
              "include": r"include|\badd|remove|without|majority|path|group|system|\bname|time|second|plays|before|detail|also|only",
              "column_names": r"column|call|\bname|rename|\bkeys?\b|header"}
PER_PERSON_COLUMNS = r"(each|every|per|by) (user|person|annotator|rater|listener|labell?er|participant)\W*s?\b.{0,40}\bcolumns?\b|" \
                     r"\bcolumns? (for|per|of) (each|every) (user|person|annotator|rater|listener|participant)"
REWRITE = r"instruction|explain|intro|guide|wording|text at the start|tell (them|people)"
# which question a message is about: the words they share, minus the words every message has
STOP = set("the and for with from what which who how does you your they them their our can could should would will please "
           "also then just only each every all any some more less new one two people person page item items clip recording "
           "text picture image video question answer option show make let put use add set change remove delete drop get "
           "have about after before like than into this that these those there here want need would are was were been "
           "hear see read look listen".split())
ABOUT_QUESTIONS = r"question|\bbox\b|window|field|\banswers? (are|to|should|be|as)\b|option|class|label|scale|button|checkbox|tick|choice|several|multiple|required|optional"
# what a page can truly not do (a "cannot" from the model that names none of this, while something changed, is noise)
CANNOT = r"\bpaint|brush|\bmasks?\b|pixel|(follow|track)(ing)? (\w+ )?(across|through|from frame|over (the )?frames)|across frames|" \
         r"frame (to|by) frame|upload (a |their |files)|let (them|people) record|record (their|a|them)|webcam|camera|microphone|3d"
ORDINAL = r"\b(first|second|third|fourth|last|1st|2nd|3rd)\b|\bquestion\s*\d"
# a message that describes the whole page ("we should have a page that…"): what it does not name may go
WHOLE = r"\bpage (that|which|where|with|should|has|will|like)|should have a page|want (a|the|my) page|make (a|the) page|" \
        r"\bonly (ask|show|have|these)|\bjust (ask|show|have)|start (over|again)|from scratch|instead of (all|everything)"


def _words(t: str) -> set:
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in re.findall(r"[^\W\d_]{3,}", t.lower())} - STOP


# what people call each kind of question ("remove the caption box", "the rating"): blocks.py
KIND_WORDS = {b.kind: b.words for b in BL.BLOCKS} | {"buttons": "button class classe label category option",
                                                    "checkboxes": "checkbox tick class classe label category option"}
RENAME = r"\b(rename|instead|replace|call (it|this|that)|turn (it|this|that)|reword|change (it|this|that|the)|into|convert)\b"
ADDING = r"\b(also|add|another|second|extra|more|too|as well|new question|plus)\b"


def _alike(a: str, b: str) -> bool:
    """One word, give or take its ending or a typo: transcript ~ transcription ~ trasncript, emotio ~ emotion."""
    if a == b:
        return True
    if min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)):
        return True
    if min(len(a), len(b)) < 5:
        return False
    r = lambda x, y: SequenceMatcher(None, x, y).ratio()
    return max(r(a, b), r(a, b[:len(a)]), r(a[:len(b)], b)) >= .8   # a typo in a word, or in its first part


def _overlap(xs: set, ys: set) -> bool:
    return any(_alike(x, y) for x in xs for y in ys)


def _about(q: dict, words: set, kind: bool = True) -> bool:
    mine = _words(q["question"] + " " + " ".join(q.get("answers") or []))
    if kind:
        mine |= set(KIND_WORDS.get(q["type"], "").split())
    return _overlap(mine, words)


# "the title of this box": a question's words, not the page's heading
BOX_TITLE = r"\b(box|question|field|window|button|scale|label)\b"
PAGE_TITLE = r"\bpage\b.{0,25}\b(title|heading|name)\b|\b(title|heading|name) of (the |this |my |our )?(page|task|test)\b|\bheading\b"
# "why didn't you change it", "do it again": the request is the one before
RETRY = r"\b(why|did ?n'?t|did not|do ?n'?t see|not (changed|done|working)|still|again|try|do it|you forgot|nothing (changed|happened)|same as before)\b"


def _same_text(a: str, b: str) -> bool:
    a, b = " ".join(a.lower().split()), " ".join(b.lower().split())
    return len(a.split()) >= 5 and (a in b or SequenceMatcher(None, a, b).ratio() > .8)


def _guard(old: dict, new: dict, message: str, first: bool = False) -> dict:
    """The model's page content, held to what the message asks: a question it removes or changes must be one the
    message is about; other parts change only when the message says something about them."""
    new = dict(new)
    said = lambda part: re.search(GUARD[part], message, re.I) is not None and not (
        part == "title" and re.search(BOX_TITLE, message, re.I) and not re.search(PAGE_TITLE, message, re.I))
    if first:   # a description is not a title or a question ("i need a page for labeling my dataset")
        if _same_text(new["title"], message) or len(new["title"]) > 70:
            new["title"] = old["title"]
        olds, words = {q["question"].lower() for q in old["questions"]}, _words(message)
        # nor a reason to make up questions it says nothing about ("speaker age", "background noise")
        mine = re.search(r"\b(i|we|my|our|us|need|want|page|dataset|website|site)\b", message, re.I)   # about the task, not for people
        asked = lambda q: q["type"] not in BL.BASIC and re.search(BL.KIND[q["type"]].ask, message, re.I) is not None   # "a MUSHRA test"
        nq = [q for q in new["questions"] if not (mine and _same_text(q["question"], message))
              and (q["question"].lower() in olds or _about(q, words, kind=False) or asked(q))]
        new["questions"] = nq if len(nq) == len(new["questions"]) else (nq or old["questions"])
    for part in ("title", "item_position", "before_start", "output"):
        if new[part] != old[part] and not said(part) and not (first and part == "title"):
            new[part] = old[part]
    if new.get("output") != old.get("output"):   # the file, part by part: "one row per person" keeps the format
        new["output"] = {k: (v if v == old["output"].get(k) or re.search(FILE_PARTS[k], message, re.I) else old["output"].get(k))
                         for k, v in new["output"].items()}
    if not first and not re.search(WHOLE, message, re.I):
        words, generic = _words(message), re.search(ABOUT_QUESTIONS, message, re.I)
        oq, nq = old["questions"], list(new["questions"])
        texts_new = {q["question"].lower() for q in nq}
        by_text = {q["question"].lower(): q for q in oq}
        matched, edits, paired = set(), [], set()
        for i, q in enumerate(nq):   # pair each new question with the old one it came from
            o = by_text.get(q["question"].lower())
            if o is None and i < len(oq) and D._same(oq[i]["question"], oq[i]["type"], q["question"], q["type"]) \
                    and oq[i]["question"].lower() not in texts_new and id(oq[i]) not in matched:
                o = oq[i]
            if o is not None:
                matched.add(id(o))
                paired.add(id(q))
                if o != q:
                    edits.append((i, o))
        adding = re.search(ADDING, message, re.I) or not re.search(RENAME, message, re.I)
        for _, o in list(edits):
            k = next((k for k, (_, x) in enumerate(edits) if x is o), None)   # its place now: an insert moves the rest
            if k is None:
                continue
            i = edits[k][0]
            q = nq[i]
            if adding and q["question"] != o["question"] and not _about(o, words, kind=False) and _about(q, words, kind=False):
                # "…as well", "we should have a window for X": the old question stays and the new one is added
                if re.search(r"\b(after|before|below|above|under|first|then)\b", message, re.I):
                    nq.insert(i + 1, o)         # the model put the new one where the message says
                else:
                    nq[i] = o
                    nq.insert(i + 1, q)
                paired.add(id(o))
                del edits[k]
                edits = [(j + (j > i), x) for j, x in edits]
                paired.discard(id(q))
        new_ones = any(id(q) not in paired for q in nq)
        for i, o in edits:   # a change to a question the message is not about is put back
            if new_ones:     # beside a new question, only one the message names by its own words
                ok = _about(o, words, kind=False) or _about(nq[i], words, kind=False)
            else:
                ok = _about(o, words) or _about(nq[i], words) or (generic and len(edits) == 1)
            if not ok:
                nq[i] = o
                paired.add(id(o))   # still one of the page's own questions, not a made-up one
        # a question it made up: the message says nothing about it ("add a box…": a new text box is what it says)
        more = re.search(r"\b(add|new|another|also|extra|more|plus|need)\b", message, re.I)
        nq = [q for q in nq if id(q) in paired or _about(q, words, kind=False) or (more and _about(q, words))]
        added = [q for q in nq if id(q) not in paired]
        gone = [(i, o) for i, o in enumerate(oq) if id(o) not in matched]
        if added and not re.search(ADDING, message, re.I) and not gone:
            # "topics are sport, politics…": the answers of the question there is, not a second question
            for q in added:
                twin = next((j for j, o in enumerate(nq) if o in oq and o["type"] in ("buttons", "checkboxes") and q["type"] in ("buttons", "checkboxes")), None)
                if twin is not None:
                    nq[twin] = q
                    nq = [x for k, x in enumerate(nq) if not (x is q and k != twin)]
        # "the title of this box…": when the message names a question by its words, "box" alone is not about the others
        named = any(_about(o, words, kind=False) for o in oq)
        for i, o in gone:    # a question it removed that the message is not about comes back
            if not (_about(o, words, kind=not named) or (len(gone) == 1 and re.search(ORDINAL, message, re.I))):
                nq.insert(min(i, len(nq)), o)
        # "remove the caption box": it sometimes says it cannot; the message names the question, so it goes
        for m in re.finditer(r"\b(?:remove|delete|drop|get rid of|without)\s+((?:\S+\s*){1,5})", message, re.I):
            phrase = m[1].lower()
            for q in list(nq):
                if len(nq) > 1 and _about(q, _words(phrase)) and not any(a.lower() in phrase for a in q.get("answers") or []):
                    nq.remove(q)
        new["questions"] = nq or old["questions"]
        kept = {b["question"].lower() for b in new["before_start"]}   # questions before starting: the same, one by one
        for i, b in enumerate(old["before_start"]):
            if b["question"].lower() not in kept and not _about(b, words):
                new["before_start"] = new["before_start"][:i] + [b] + new["before_start"][i:]
    if new["before_start"] != old["before_start"]:   # asked once before starting: not one of the item's questions too
        asked = {b["question"].strip().lower() for b in new["before_start"]} - {b["question"].strip().lower() for b in old["before_start"]}
        olds = {q["question"].strip().lower() for q in old["questions"]}
        keep = [q for q in new["questions"] if q["question"].strip().lower() in olds or q["question"].strip().lower() not in asked]
        new["questions"] = keep or new["questions"]
    if new["instructions"] != old["instructions"] and new["questions"] == old["questions"] and not first and not re.search(REWRITE, message, re.I):
        new["instructions"] = old["instructions"]   # it rewrites them now and then for no reason
    return new


# a reply that says it changed something (nothing changed) or that the page cannot do what it can
FALSE_CLAIM = r"\b(i('ve| have)? (added|changed|removed|moved|updated|renamed|set|made|put)|cannot|can't|can not|not possible|" \
              r"only (allows?|supports?|has)|not supported|unable|not allowed)\b"
YES = r"^\W*(yes|yeah|yep|yup|ok|okay|sure|do it|go ahead|please do|correct|right|exactly|that'?s it|y|بله|آره)\b"
WHAT_NOW = r"\b(what|which)\b.{0,40}\b(have|got|already|now|on the page|in the page|questions?|labels?|there)\b|\bshow me\b|" \
           r"\b(list|summari[sz]e)\b.{0,30}\b(page|questions?|labels?)|how many questions"
def summary(v: dict) -> str:
    """The page in a few lines, for "what do we have already?"."""
    def one(q):
        if q["type"] in ("buttons", "checkboxes"):
            return f"“{q['question']}” ({'buttons' if q['type'] == 'buttons' else 'tick boxes'}: {D._and(q['answers'])})"
        if q["type"] == "scale":
            return f"“{q['question']}” (a scale from {q['from']} to {q['to']}, number buttons)"
        if q["type"] == "slider":
            return f"“{q['question']}” (a slider from {q['from']} to {q['to']}, {q.get('decimals', 2)} decimals)"
        return f"“{q['question']}” ({BL.what(q)})"
    lines = [f"The page asks {len(v['questions'])} question{'s' if len(v['questions']) != 1 else ''} with each item, "
             f"with the item {'above them' if v['item_position'] == 'top' else 'beside them'}:"]
    lines += [f"{i + 1}. {one(q)}" for i, q in enumerate(v["questions"])]
    if v["before_start"]:
        lines.append("Asked once before starting: " + D._and([f"“{b['question']}”" for b in v["before_start"]]) + ".")
    o = v.get("output") or {}
    if o:
        lines.append(f"Your results file: {D.FILE_WORDS[o['format']]}, {D.FILE_WORDS[o['rows']]}.")
    r = v["rules"]
    lines.append(f"{r['people_per_item']} {'person' if r['people_per_item'] == 1 else 'people'} per item." if r["people_per_item"]
                 else "Everyone does every item.")
    return "\n".join(lines)


CLAIM = r"\b(i('ve| have)?|now)\b.{0,12}\b(added|changed|removed|moved|updated|renamed|set|made|put|replaced|turned|reworded)\b"


def _attempt(obj: dict, tpl: dict, message: str, said: str, intro: bool, note: str = "") -> tuple:
    """One try of the model at the message, held to it by the checks. -> (new view, out, problems, raw)"""
    old, modality = current(obj), tpl["modality"]
    out = agent.page_edit(tpl, old, message, [] if intro else obj.get("chat") or [], _data_line("shared", obj),
                          first=intro, note=note)
    if out["cannot"] and not re.search(CANNOT, said, re.I):
        out["cannot"] = ""   # a "cannot" counts only when the message asks for what the page truly cannot do
    problems, raw = [], None
    try:
        new = D.clean({**old, **{k: out["content"][k] for k in agent.CONTENT if k in out["content"]}}, modality)
        raw = new
    except D.Bad as e:
        new, problems = dict(old), [str(e)]
    new = _no_unasked_kind(old, _guard(old, new, said, first=intro), said)
    if out["cannot"]:   # no question that asks for the impossible
        olds = {q["question"].lower() for q in old["questions"]}
        new["questions"] = [q for q in new["questions"] if q["question"].lower() in olds
                            or (q["type"] != "text" and not re.search(CANNOT, q["question"], re.I))] or old["questions"]   # no text box as a stand-in
    new = _one_each(new, said)
    if new != old and not problems:
        try:   # the page as a whole: one comparison at most, one question on the item itself at most
            D.build(new, obj["design"])
        except ValueError as e:
            new, problems = dict(old), [str(e)]
    return new, out, problems, raw


def _no_unasked_kind(old: dict, new: dict, said: str) -> dict:
    """A kind of question the message does not ask for (blocks.py `ask`) is not used: a slider becomes a scale ("from 1
    to 5"), a question changed into another kind stays as it was, and a new one is left out."""
    was = {q["question"].lower(): q for q in old["questions"]}
    qs = []
    for i, q in enumerate(new["questions"]):
        b = BL.KIND[q["type"]]
        o = was.get(q["question"].lower()) or (old["questions"][i] if i < len(old["questions"]) else None)
        if q["type"] in BL.BASIC or (o and o["type"] == q["type"]) or re.search(b.ask, said, re.I):
            qs.append(q)
        elif q["type"] == "slider":
            lo = max(0, min(q["from"], 10))
            qs.append({k: v for k, v in q.items() if k != "decimals"} | {"type": "scale", "from": lo, "to": max(lo + 1, min(q["to"], 11)),
                                                                          "words": q["words"] if len(q.get("words") or []) == 2 else []})
        elif o and o not in qs and not any(x["question"].lower() == o["question"].lower() for x in new["questions"] if x is not q):
            qs.append(o)   # the question it came from, as it was
    return {**new, "questions": qs or old["questions"]}


def _one_each(new: dict, said: str) -> dict:
    """A page shows its item one way: one comparison at most and one question on the item at most, not both. The small
    model splits a best-worst into two questions now and then: the one the message names stays, else the first."""
    qs = list(new["questions"])
    named = lambda q: bool(BL.KIND[q["type"]].named) and re.search(BL.KIND[q["type"]].named, said, re.I) is not None
    for group in (BL.COMPARE, BL.ON_ITEM, BL.COMPARE + BL.ON_ITEM):
        idx = [i for i, q in enumerate(qs) if BL.KIND[q["type"]].stored in group]
        if len(idx) > 1:
            keep = next((i for i in idx if named(qs[i])), idx[0])
            qs = [q for i, q in enumerate(qs) if i not in idx or i == keep]
    return {**new, "questions": qs} if qs != new["questions"] else new


TAKE_OVER = r"\b(change|turn|convert|switch|make) (it|this|the (page|test|task|question))( in)?(to| into)?\b|\bmake it\b|\binstead\b|\breplace\b"


def _take_over(old: dict, new: dict, said: str) -> dict:
    """A comparison that a message makes of the page ("change it to a MUSHRA test", "make it an A/B test") takes the
    place of the rating the page had, which the model (or the checks) often keep beside it."""
    if not re.search(TAKE_OVER, said, re.I) or not any(q["type"] in BL.COMPARE and q not in old["questions"] for q in new["questions"]):
        return new
    words = _words(said)
    qs = [q for q in new["questions"] if not (q in old["questions"] and q["type"] in ("scale", "slider") and not _about(q, words, kind=False))]
    return {**new, "questions": qs} if qs else new


# a richer kind of a question the page has: "the box should start with the transcript" changes the box, not adds one
RICHER = {"correct_text": ("text",), "rating_over_time": ("scale", "slider"), "dropdown": ("buttons",), "slider": ("scale",),
          "grid": ("scale",), "ranking": ("buttons", "checkboxes")}
COUNT_WORDS = r"\b(\d{1,2}) (images|pictures|photos|clips|recordings|videos|texts|items|versions|samples)\b"
SEVERAL_WORDS = r"\ball (of )?the (ones|images|pictures|photos|clips|videos|that)|\bseveral\b|more than one|any number|\ball that"


def _in_place(old: dict, new: dict, said: str) -> dict:
    """A new question of a richer kind takes the place of the plain question it grew from, when the message does not
    ask to add one; a new comparison gets the number of items and "pick several" the message says."""
    qs = list(new["questions"])
    for q in [q for q in qs if q not in old["questions"]]:
        if q["type"] in RICHER and not re.search(ADDING, said, re.I):
            base = [o for o in qs if o in old["questions"] and o["type"] in RICHER[q["type"]]]
            if len(base) == 1:   # the one it can only mean
                i = qs.index(base[0])
                qs.remove(q)
                qs[i] = q
        if q["type"] in ("best_worst", "pick_items"):
            m = re.search(COUNT_WORDS, said, re.I)
            n = {**q}
            if m:
                n["count"] = max(3 if q["type"] == "best_worst" else 2, min(int(m[1]), 6 if q["type"] == "best_worst" else 9))
            if q["type"] == "pick_items" and re.search(SEVERAL_WORDS, said, re.I):
                n["several"] = True
            qs[qs.index(q)] = n
    return {**new, "questions": qs} if qs != new["questions"] else new


LABELLED = ("time_ranges", "time_points", "boxes", "points", "regions", "highlights")


def _labels(tpl: dict, old: dict, new: dict, said: str) -> dict:
    """A new marking question with fewer than two labels while the message lists things ("names of people and places"):
    its labels from the small call that writes only that question (agent.block_fill), which gets them right."""
    qs = list(new["questions"])
    for i, q in enumerate(qs):
        if q["type"] in LABELLED and q not in old["questions"] and len(q.get("answers") or []) < 2 and re.search(r",| and | or ", said):
            try:
                got = agent.block_fill(tpl, {**new, "questions": [x for x in qs if x is not q]}, said, q["type"])
            except agent.Offline:
                return new
            labels = D._list(got.get("answers"), 20, 40)
            if len(labels) >= 2:
                qs[i] = {**q, "answers": labels}
    return {**new, "questions": qs} if qs != new["questions"] else new


PLACE = r"\b(before|after|above|below|under|first|top|start|beginning|between|end|last|second|third|up|down|move)\b"


def _at_end(old: dict, new: dict, said: str) -> dict:
    """Questions only added, with no word on where: they go after the ones there are (the model puts them anywhere)."""
    qs = new["questions"]
    if re.search(PLACE, said, re.I) or [q for q in qs if q in old["questions"]] != old["questions"]:
        return new
    added = [q for q in qs if q not in old["questions"]]
    return {**new, "questions": old["questions"] + added} if added else new


def _missing(new: dict, said: str, modality: str) -> list:
    """Kinds the message names by a word that can only mean them ("MUSHRA", "highlight") that the page does not have."""
    have = {q["type"] for q in new["questions"]}
    named = [b for b in BL.for_media(modality) if b.named and re.search(b.named, said, re.I)]
    return [] if any(b.kind in have for b in named) else named   # one of the kinds it names is there: that is the one


def _fill(obj: dict, tpl: dict, old: dict, new: dict, said: str, b) -> dict:
    """The page with the named kind's question in it (agent.block_fill), in place of a question the model made of the
    wrong kind (pick items for a best-worst, an A/B for a CMOS), or of the question it replaces, or added."""
    wrong = lambda q: q not in old["questions"] and q["type"] not in BL.BASIC and BL.KIND[q["type"]].group in (b.group, "compare") \
        and q["type"] != b.kind
    qs = list(new["questions"])
    at = next((i for i, q in enumerate(qs) if wrong(q)), None)
    try:
        got = agent.block_fill(tpl, {**new, "questions": [q for q in qs if not wrong(q)]}, said, b.kind)
        q = D._clean_q({**got, "type": b.kind, "required": True}, modality=tpl["modality"])
    except (agent.Offline, D.Bad):
        return new
    qs = [x for x in qs if not wrong(x)]
    if at is not None or got["replaces"] < 0:
        qs.insert(min(at if at is not None else len(qs), len(qs)), q)
    else:
        qs[got["replaces"]] = q
    out = _one_each({**new, "questions": qs}, said)
    try:
        D.build(out, obj["design"])
    except ValueError:
        return new
    return out


def _retry_note(old: dict, new: dict, raw, out: dict, said: str) -> str:
    """When its try changed nothing: what went wrong, to tell it once ("" = nothing to tell)."""
    if new != old:
        return ""
    if raw is not None and raw["title"] != old["title"] and re.search(BOX_TITLE, said, re.I) and \
            raw["questions"] == old["questions"]:
        return ("you changed page_title, but the person means a box on the page: the title of a box is its `question`. "
                "Change that question's words and keep page_title as it was.")
    if out["answer"] and re.search(CLAIM, out["answer"], re.I):
        return (f"you said “{out['answer'][:200]}”, but the page you returned is the same as before. Return the page with "
                "that change made in it.")
    if (out["cannot"] or re.search(r"\b(cannot|can't|can not|not possible|not allowed|unable)\b", out["answer"], re.I)) \
            and not re.search(CANNOT, said + " " + out["cannot"], re.I):
        return ("you said it cannot be done, but it can: you may change any question's words, type, answers and order, "
                "add and remove questions, the questions before starting, the page_title, the instructions and the "
                "results file. Do what the person asks.")
    return ""


def _chat_page(obj: dict, tpl: dict, message: str, intro: bool) -> tuple:
    """A Task sharing or Subjective test page: the model edits the page itself.
    -> (new view or None, out, problems, the model's own page before the checks or None)"""
    old, modality = current(obj), tpl["modality"]
    said = message   # what the checks hold the change to
    asked = [m["text"] for m in obj.get("chat") or [] if m["role"] == "owner"]
    if not intro and asked and len(message.split()) <= 12 and re.search(RETRY, message, re.I):
        said = asked[-1] + "\n" + message   # "why didn't you change it": the request before
    new, out, problems, raw = _attempt(obj, tpl, message, said, intro)
    note = (f"the page you returned cannot be shown: {problems[0]} Return all of the page with that fixed." if problems
            else _retry_note(old, new, raw, out, said))
    if note:   # once more, told what was wrong
        new2, out2, problems2, raw2 = _attempt(obj, tpl, message, said, intro, note)
        if new2 != old or not problems2:
            new, out, problems, raw = new2, out2, problems2, raw2
    lacks = _missing(new, said, modality)
    if lacks and not problems:   # it named a kind the page still lacks: that question alone, in a small call
        new = _fill(obj, tpl, old, new, said, lacks[0])
    if not problems:
        new = _in_place(old, _take_over(old, new, said), said)
        new = _at_end(old, _labels(tpl, old, new, said), said)
    if new.get("output") and re.search(PER_PERSON_COLUMNS, said, re.I) and new["output"]["rows"] != "item":
        new = {**new, "output": {**new["output"], "rows": "item"}}   # "each user's label as a column"
    if any(re.search(GUARD[p], said, re.I) for p in GUARD if "." in p):   # the look, rules and charts
        r = agent.rules_edit(new, modality, said)
        new = {**new, "look": dict(new["look"]), "rules": dict(new["rules"]), "charts": dict(new["charts"])}
        for k, v in r["changes"].items():
            top, sub = k.split(".")
            if k in GUARD and not re.search(GUARD[k], said, re.I):
                continue
            if top == "charts":   # no chart the message does not name
                hints = OPTION_HINTS["finishCharts" if sub == "last_screen" else "charts"]
                v = [c for c in v if c in old["charts"][sub] or re.search(hints.get(c, "."), said, re.I)]
            new[top][sub] = v
        new = D.clean(new, modality)
    return (new if new != old else None), out, problems, (raw if raw is not None and raw != old else None)


def chat(kind: str, oid: str, message: str, intro: bool = False) -> dict:
    """One chat message: the model changes the page (or its settings); a new version when anything changed. `intro`:
    the message is the description given when the page was made."""
    message = message.strip()
    if not message:
        raise ValueError("Say what to change")
    obj = _load(kind, oid)
    if kind == "shared":
        return _chat_shared(oid, message, intro)
    tpl = C.get(obj["page"]["template"])
    cur = current(obj)
    shown = {k: (v if not (isinstance(v, list) and len(v) > LONG_LIST) else f"({len(v)} items, changed in the Data tab)")
             for k, v in cur.items()}
    note = ""
    if intro:   # the description given when the page was made: two small steps, saved as one version
        first = agent.intro(tpl, cur, message)   # may raise Offline
        rest = agent.edit(tpl, {**shown, **first["changes"]}, FIRST.format(message), [], _data_line(kind, obj))
        if len(first["changes"].get("title", "")) > 60:
            first["changes"].pop("title")
        others = {k: v for k, v in rest["changes"].items() if k not in FIRST_KEYS and _mentioned(k, v, message)}
        out = {**rest, "ask": "", "changes": {**others, **first["changes"]}}
        made_up = [c for c in first["changes"].get("classes", []) if c.lower() not in message.lower()]
        if made_up:
            note = " Are these the right classes? Tell me yours and I will change them."
    else:
        out = agent.edit(tpl, shown, message, obj.get("chat") or [], _data_line(kind, obj))   # may raise Offline
    changes = {k: v for k, v in out["changes"].items() if not isinstance(shown.get(k), str) or not shown[k].startswith("(")}
    changes = _tidy(changes, cur, message)
    problems = list(dict.fromkeys(out["problems"]))[:3]
    with _lock(kind):
        obj = _load(kind, oid)
        if changes:
            try:
                obj = _new_version(kind, obj, {**current(obj), **changes}, "chat", list(changes))
            except ValueError as e:   # e.g. a scale whose top is below its bottom
                problems.append(str(e))
                changes = {}
        version = len(obj["page"]["versions"])
        # the reply says what really changed (the model sometimes claims more), or why nothing did
        if changes:
            reply = S.describe(changes, current(obj)) + note
        elif out["unsupported"]:
            reply = "This page cannot do that yet."
        elif out["ask"]:
            reply = "I need to know a little more first."
        elif problems:
            reply = "I could not make that change."
        else:
            reply = ("I could not match that to anything this page can change. Try naming the part to change (the question, "
                     "the classes, the scale, who answers…), or ask the admins for it.")
        log = obj.get("chat") or []
        log += [{"role": "owner", "text": message[:2000], "at": now_iso()},
                {"role": "assistant", "text": reply, "at": now_iso(), "changes": list(changes), "version": version,
                 **{k: out[k] for k in ("ask", "unsupported") if out[k]}, **({"problems": problems} if problems and not changes else {})}]
        obj["chat"] = log[-80:]
        _save(kind, obj)
    return {"reply": reply, "ask": out["ask"], "unsupported": out["unsupported"], "changes": changes,
            "problems": problems, "version": version, "settings": current(obj)}


def _chat_shared(oid: str, message: str, intro: bool) -> dict:
    obj = _load("shared", oid)
    tpl = C.get(obj["page"]["template"])
    pending = obj["page"].get("pending")
    if pending and pending["version"] == len(obj["page"]["versions"]) and re.search(YES, message, re.I):
        # "yes" to what it offered when it was not sure
        new, out, problems, raw = pending["view"], {"ask": "", "cannot": "", "answer": ""}, [], None
    elif re.search(WHAT_NOW, message, re.I) and not re.search(r"\b(add|remove|change|make|put|delete|rename|replace|use)\b", message, re.I):
        new, out, problems, raw = None, {"ask": "", "cannot": "", "answer": summary(current(obj))}, [], None
    else:
        new, out, problems, raw = _chat_page(obj, tpl, message, intro)   # may raise Offline
    with ST._lock:
        obj = _load("shared", oid)
        old = current(obj)
        obj["page"].pop("pending", None)
        changes, reply = [], ""
        if new:
            try:
                obj = _new_view(obj, new, "chat", D.changed(old, new))
                changes = D.changed(old, new)
                reply = D.describe(old, new)
            except ValueError as e:
                problems.append(str(e))
        version = len(obj["page"]["versions"])
        if changes and not re.search(CANNOT, out["cannot"], re.I):
            out["cannot"] = ""
        if changes or out["cannot"]:
            out["ask"] = ""
        if changes:
            made_up = [a for q in new["questions"] if q not in old["questions"] and q["type"] in ("buttons", "checkboxes")
                       for a in q["answers"] if a.lower() not in message.lower()]
            if intro and len(made_up) >= 2:
                reply += " Are these the right answers? Tell me yours and I will change them."
        elif out["cannot"] and re.search(CANNOT, message + " " + out["cannot"], re.I):
            said = out.get("answer") if out.get("answer") and not re.search(r"\bi('ve| have)? (added|changed|removed|moved)\b", out["answer"], re.I) else ""
            reply = said or f"The page cannot do that yet: {out['cannot'].rstrip('.')}."
        elif out.get("answer") and not re.search(FALSE_CLAIM, out["answer"], re.I):
            reply = out["answer"]   # a question about the page, or it is there already: the model says so in its words
        elif raw is not None and D.describe(old, raw):
            # it changed more than the message seems to ask, or something else: say what, and do it on a yes
            obj["page"]["pending"] = {"view": raw, "version": version, "at": now_iso()}
            reply = ("I am not sure I understood. Do you want this: " + D.describe(old, raw)[len("Done: "):].rstrip(".")
                     + "? Say yes and I will do it, or tell me what you want instead.")
        elif out["ask"]:
            reply = "I need to know a little more first."
        elif problems:
            reply = f"I could not make that change: {problems[0]}"
        else:
            reply = ("Nothing changed. " + summary(old) + "\nTell me what to add, remove or change, in your own words.")
        log = obj.get("chat") or []
        log += [{"role": "owner", "text": message[:2000], "at": now_iso()},
                {"role": "assistant", "text": reply, "at": now_iso(), "changes": changes, "version": version,
                 **({"ask": out["ask"]} if out["ask"] and not changes else {}),
                 **({"unsupported": out["cannot"]} if out["cannot"] and re.search(CANNOT, message + " " + out["cannot"], re.I) else {})}]
        obj["chat"] = log[-80:]
        ST._save(obj)
    real = out["cannot"] if re.search(CANNOT, message + " " + out["cannot"], re.I) else ""
    return {"reply": reply, "ask": out["ask"], "unsupported": real, "changes": changes,
            "problems": problems, "version": version, "settings": current(obj)}


def save_point(kind: str, oid: str, name: str = "") -> dict:
    """The owner saves the design: the current version is marked, with a name, to find and continue later."""
    with _lock(kind):
        obj = _load(kind, oid)
        v = obj["page"]["versions"][-1]
        v["saved"] = {"at": now_iso(), "name": name.strip()[:80]}
        return _save(kind, obj)


def restore(kind: str, oid: str, version: int) -> dict:
    """Back to any earlier version: it becomes the newest version, so nothing in between is lost."""
    with _lock(kind):
        obj = _load(kind, oid)
        vs = obj["page"]["versions"]
        if not 1 <= version <= len(vs):
            raise ValueError("There is no such version")
        if version == len(vs):
            return obj
        old = vs[version - 1]
        if kind == "shared":
            obj = _new_view(obj, old["view"], "restore", D.changed(current(obj), old["view"]))
        else:
            obj = _new_version(kind, obj, dict(old["settings"]), "restore", [k for k, v in old["settings"].items() if current(obj).get(k) != v])
        obj.setdefault("chat", []).append({"role": "assistant", "text": f"Back to version {version}, saved as version {len(obj['page']['versions'])}.",
                                           "at": now_iso(), "version": len(obj["page"]["versions"]), "undo": True})
        return _save(kind, obj)


def undo(kind: str, oid: str) -> dict:
    with _lock(kind):
        obj = _load(kind, oid)
        vs = obj["page"]["versions"]
        if len(vs) < 2:
            raise ValueError("This is the first version; there is nothing to undo")
        vs.pop()
        if kind == "shared":
            obj["design"] = vs[-1]["design"]
            _practice(obj["id"], current(obj)["rules"].get("practice_items", 0))
        else:
            obj["design"] = _compile(kind, C.get(obj["page"]["template"]), current(obj))
        obj.setdefault("chat", []).append({"role": "assistant", "text": f"Back to version {len(vs)}.", "at": now_iso(),
                                           "version": len(vs), "undo": True})
        return _save(kind, obj)


def set_settings(kind: str, oid: str, patch: dict) -> dict:
    """Settings changed by hand in the studio's settings form."""
    with _lock(kind):
        obj = _load(kind, oid)
        if kind == "shared":   # parts of the page: {"questions": [...]} or {"rules": {...}}, merged into the page
            old = current(obj)
            merged = {**old, **{k: ({**old[k], **v} if isinstance(old.get(k), dict) and isinstance(v, dict) else v)
                                for k, v in patch.items() if k in old}}
            new = D.clean(merged, obj["page"]["modality"])
            if new == old:
                return obj
            return ST._save(_new_view(obj, new, "hand", D.changed(old, new)))
        changed = [k for k, v in patch.items() if current(obj).get(k) != v]
        if not changed:
            return obj
        return _save(kind, _new_version(kind, obj, {**current(obj), **patch}, "hand", changed))


def approve(kind: str, oid: str) -> dict:
    """The owner keeps this version: it is marked approved, and the page goes live when it has its data."""
    with _lock(kind):
        obj = _load(kind, oid)
        obj["page"]["approved"] = {"version": len(obj["page"]["versions"]), "at": now_iso()}
        _save(kind, obj)
    note = ""
    try:
        obj = ST.update(oid, {"status": "live"}) if kind == "shared" else CO.update(oid, {"status": "live"})
    except ValueError as e:   # no data yet
        note = str(e)
    return {"obj": obj, "note": note}
