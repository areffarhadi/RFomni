"""The settings of the default pages, and how they become a page.

A default page has flat settings ("classes", "scaleMax", "comment", ...). Each setting has a kind, a plain description
(the small model reads it) and limits. `clean()` checks and converts a value; `compile_task()` turns the settings of a
Task sharing or Subjective test page into a shared-task design (shared_tasks.validate_design), `compile_collect()`
those of a Data collection page into a collection design (collect.validate_design).

Families (what kind of answer the page asks for):
  classify  pick one or several classes            rate     a rating scale (MOS and the like)
  write     free text: transcript, caption, ...    record   people record prompts (Data collection)
"""
import re

from ..util import slugify

THEMES = ["forest", "sand", "dark", "light", "navy", "cream", "charcoal"]


class Bad(ValueError):
    """A value a setting does not accept; the message says why, in plain words."""


def S(kind: str, desc: str, **limits) -> dict:
    return {"kind": kind, "desc": desc, **limits}


# ----------------------------------------------------------------------------- the settings
# questions people answer once, before they start (Task sharing and Subjective tests)
ABOUT = {
    "native speaker": {"label": "Are you a native speaker of this language?", "type": "choice", "options": ["Yes", "No"], "required": True},
    "first language": {"label": "What is your first language?", "type": "text", "required": False},
    "age": {"label": "Your age", "type": "choice", "options": ["under 18", "18–29", "30–44", "45–59", "60 or older"], "required": False},
    "gender": {"label": "Your gender", "type": "choice", "options": ["Woman", "Man", "Other", "Prefer not to say"], "required": False},
    "headphones": {"label": "What are you listening with?", "type": "choice", "options": ["Headphones", "Speakers", "Phone or laptop speaker"], "required": True},
    "hearing problems": {"label": "Do you have any known hearing problems?", "type": "choice", "options": ["No", "Yes", "Not sure"], "required": False},
    "expertise": {"label": "How familiar are you with this kind of task?", "type": "choice", "options": ["New to it", "Some experience", "Expert"], "required": False},
}
RESULT_CHARTS = ["answers", "scores", "systems", "agreement", "timeline", "time-per-item", "people"]
FINISH_CHARTS = ["my-answers", "everyone", "my-time"]

COMMON = {
    "title": S("text", "the page title people see", max=100),
    "instructions": S("long", "what to do and how to decide, shown before people start (2 to 6 short sentences)", max=1500),
    "randomOrder": S("bool", "each person gets the items in a different random order"),
    "allowSkip": S("bool", "people may skip an item they cannot answer"),
    "allowBack": S("bool", "people may go back and change an earlier answer"),
    "askName": S("bool", "ask people for their name or ID before they start"),
    "askAbout": S("some", "questions people answer once before they start", options=list(ABOUT)),
    "peoplePerItem": S("int", "how many different people answer each item; 0 = everyone answers every item", lo=0, hi=20),
    "itemsPerPerson": S("int", "the most items one person is given; 0 = no limit", lo=0, hi=100000),
    "comment": S("one", "a comment box under each item's questions", options=["off", "optional", "required"]),
    "finalComment": S("bool", "a box for overall comments on the last screen"),
    "practiceItems": S("int", "the first N items are practice: everyone gets them first and they are not scored", lo=0, hi=10),
    "progress": S("one", "how people see their progress: a bar or a ring", options=["bar", "ring"]),
    "finishCharts": S("some", "charts on the last screen people see: my-answers = their own answers, everyone = how "
                      "everyone answered, my-time = how long they took per item", options=FINISH_CHARTS),
    "charts": S("some", "charts on the owner's results page: answers = counts per class, scores = how often each score "
                "was given, systems = mean score per system, agreement = how often people agree, timeline = answers per "
                "day, time-per-item = how long people take, people = items done per person", options=RESULT_CHARTS),
    "theme": S("one", "colour mode of the page", options=THEMES),
    "accent": S("color", "accent colour as #rrggbb; empty = the colour mode's own"),
    "language": S("one", "language of the page's buttons: en = English, fa = Persian (right to left)", options=["en", "fa"]),
}

# more questions asked with each item, after the page's main one: the kinds the volunteer page can show
QTYPES = {"buttons": "choice", "checkboxes": "multi", "scale": "rating", "text": "text"}

MUST_PLAY = S("bool", "the answers unlock only after the recording or video has played to the end")
FAMILIES = {
    "classify": {
        "question": S("text", "the question shown with each item", max=200),
        "classes": S("list", "the answer options people choose from", min_items=2, max_items=20, max_len=40),
        "multiple": S("bool", "people may pick several classes instead of one"),
        "notSure": S("bool", "add a 'Not sure' option"),
        "other": S("bool", "add an 'Other' option with a box to say what"),
        "classHelp": S("long", "short definitions of the classes, shown under the question", max=300),
        "confidence": S("bool", "also ask how sure people are, from 1 (a guess) to 5 (certain)"),
        "mustPlay": MUST_PLAY,
    },
    "rate": {
        "question": S("text", "the question people answer with the scale", max=200),
        "scaleMin": S("int", "the lowest point of the scale", lo=0, hi=10),
        "scaleMax": S("int", "the highest point of the scale", lo=1, hi=11),
        "pointLabels": S("list", "words under the scale points, low to high: one word per point, or two words for "
                         "the two ends; empty = numbers only", max_items=11, max_len=30),
        "secondQuestion": S("text", "a second question answered on the same scale; empty = none", max=200),
        "mustPlay": MUST_PLAY,
        "noRepeat": S("bool", "never the same sentence twice in a row (for clips of one sentence by several systems)"),
    },
    "write": {
        "question": S("text", "what people write, e.g. 'Write down exactly what is said'", max=200),
        "placeholder": S("text", "grey hint text inside the empty box", max=120),
        "multiline": S("bool", "a big box for long answers instead of one line"),
        "extraRating": S("text", "an extra 1 to 5 rating question asked with each item (e.g. How clear is the "
                         "speech?); empty = none", max=200),
        "mustPlay": MUST_PLAY,
    },
}

INFO = {"name": "name", "gender": "gender", "age": "age", "first language": "first_language", "accent": "accent"}
RECORD = {
    "title": COMMON["title"], "instructions": COMMON["instructions"],
    "consent": S("long", "the consent sentence people must agree to before recording", max=1500),
    "mode": S("one", "read = people read each prompt aloud; answer = each prompt is a question people answer out loud",
              options=["read", "answer"]),
    "prompts": S("list", "the sentences to read, or the questions to answer", min_items=1, max_items=500, max_len=500),
    "languageName": S("text", "the language of the prompts, e.g. English", max=40),
    "ways": S("list", "ways to say each prompt, e.g. happy, sad, whisper; empty = one plain way", max_items=12, max_len=40),
    "wayOrder": S("one", "together = each prompt in all its ways one after another; shuffled = mixed",
                  options=["together", "shuffled"]),
    "minSeconds": S("int", "the shortest recording accepted, in seconds", lo=1, hi=60),
    "maxSeconds": S("int", "the longest recording, in seconds", lo=2, hi=300),
    "sampleRate": S("one", "recording quality in samples per second: 16000 for speech, 48000 for full quality",
                    options=["16000", "48000"]),
    "askAbout": S("some", "what people are asked about themselves", options=list(INFO)),
    "peoplePerPrompt": S("int", "how many people record each prompt in each way; 0 = everyone records everything", lo=0, hi=1000),
    "allowSkip": COMMON["allowSkip"], "theme": COMMON["theme"], "accent": COMMON["accent"],
}


def spec(family: str, modality: str = "") -> dict:
    """All settings of a family of page, by name. Playing to the end only exists for recordings and video."""
    if family == "record":
        return dict(RECORD)
    out = {**FAMILIES[family], **COMMON}
    if modality not in ("voice", "video"):
        out.pop("mustPlay", None)
        out.pop("noRepeat", None)
    return out


# ----------------------------------------------------------------------------- checking values
def _items(v) -> list:
    if isinstance(v, str):  # "happy, sad, angry" or one per line
        v = re.split(r"\n|;|,(?![^()]*\))", v)
    if not isinstance(v, list):
        raise Bad("should be a list")
    return [str(x).strip() for x in v if str(x).strip()]


_QWORDS = {"choice": "buttons", "button": "buttons", "radio": "buttons", "one": "buttons", "single": "buttons",
           "multi": "checkboxes", "checkbox": "checkboxes", "tick boxes": "checkboxes", "several": "checkboxes",
           "rating": "scale", "number": "scale", "box": "text", "free text": "text", "write": "text"}


def _flag(v, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "on", "1")


def _question(q) -> dict:
    """One of moreQuestions, checked: {question, type, options | scaleMin + scaleMax, required}."""
    if isinstance(q, str):   # "Which emotion do you hear? happy, sad, angry"
        m = re.match(r"\s*(.+?\?)\s*(.*)$", q, re.S)
        q = {"question": m[1], "options": m[2]} if m else {"question": q}
    if not isinstance(q, dict):
        raise Bad("each question needs its words and a type")
    text = str(q.get("question") or q.get("label") or q.get("text") or "").strip()[:200]
    if not text:
        raise Bad("each question needs its words")
    t = str(q.get("type") or "").strip().lower()
    t = _QWORDS.get(t, t)
    opts = list(dict.fromkeys(x[:60] for x in _items(q.get("options") or [])))[:20]
    if t not in QTYPES:
        t = "buttons" if len(opts) >= 2 else "text"
    out = {"question": text, "type": t, "required": _flag(q.get("required"), t != "text")}
    if t in ("buttons", "checkboxes"):
        if len(opts) < 2:
            raise Bad(f"“{text}” needs at least two answers")
        out["options"] = opts
    elif t == "scale":
        try:
            lo = max(0, min(10, int(float(q.get("scaleMin", 1) if q.get("scaleMin") not in (None, "") else 1))))
            hi = max(lo + 1, min(11, int(float(q.get("scaleMax", 5) if q.get("scaleMax") not in (None, "") else 5))))
        except (TypeError, ValueError):
            raise Bad(f"“{text}” needs a scale from one whole number to a higher one")
        out.update(scaleMin=lo, scaleMax=hi)
    return out


def clean(s: dict, v):
    """A value for setting `s`, converted and checked; raises Bad."""
    k = s["kind"]
    if k == "questions":
        qs = v if isinstance(v, list) else [v] if v else []
        out, seen = [], set()
        for q in qs:
            q = _question(q)
            if q["question"].lower() not in seen:
                seen.add(q["question"].lower())
                out.append(q)
        if len(out) > s["max_items"]:
            raise Bad(f"can have at most {s['max_items']}")
        return out
    if k in ("text", "long"):
        if isinstance(v, list):
            v = (", " if k == "text" else "\n").join(map(str, v))
        return str(v if v is not None else "").strip()[: s["max"]]
    if k == "int":
        try:
            n = int(float(str(v).strip()))
        except ValueError:
            raise Bad("should be a whole number")
        if not s["lo"] <= n <= s["hi"]:
            raise Bad(f"should be from {s['lo']} to {s['hi']}")
        return n
    if k == "bool":
        if isinstance(v, bool):
            return v
        t = str(v).strip().lower()
        if t in ("true", "yes", "on", "1"):
            return True
        if t in ("false", "no", "off", "0", ""):
            return False
        raise Bad("should be true or false")
    if k == "one":
        t = str(v).strip().lower()
        for o in s["options"]:
            if t == o.lower():
                return o
        raise Bad(f"should be one of {', '.join(s['options'])}")
    if k == "color":
        t = str(v or "").strip()
        if t and not re.fullmatch(r"#[0-9a-fA-F]{6}", t):
            raise Bad("should be a colour like #2f6fe4, or empty")
        return t.lower()
    if k == "some":
        out = []
        for x in _items(v):
            hit = next((o for o in s["options"] if o.lower() == x.lower()), None)
            if not hit:
                raise Bad(f"“{x}” is not one of {', '.join(s['options'])}")
            if hit not in out:
                out.append(hit)
        return out
    if k == "list":
        out = list(dict.fromkeys(x[: s["max_len"]] for x in _items(v)))
        if len(out) > s["max_items"]:
            raise Bad(f"can have at most {s['max_items']}")
        if len(out) < s.get("min_items", 0):
            raise Bad(f"needs at least {s['min_items']}")
        return out
    raise Bad("unknown kind of setting")


def check(family: str, modality: str, settings: dict) -> dict:
    """Every setting of a page, checked; unknown names are dropped. Raises Bad naming the setting."""
    sp = spec(family, modality)
    out = {}
    for name, value in settings.items():
        if name in sp:
            try:
                out[name] = clean(sp[name], value)
            except Bad as e:
                raise Bad(f"{name} {e}")
    if family == "rate" and out.get("scaleMax", 5) <= out.get("scaleMin", 1):
        raise Bad("scaleMax should be higher than scaleMin")
    if family == "record" and out.get("maxSeconds", 15) <= out.get("minSeconds", 1):
        raise Bad("maxSeconds should be longer than minSeconds")
    return out


# ----------------------------------------------------------------------------- settings -> page
def compile_task(tpl: dict, s: dict) -> dict:
    """The shared-task design (shared_tasks.validate_design) of a Task sharing or Subjective test page."""
    fam = tpl["family"]
    fields = []
    if fam == "classify":
        opts = list(s["classes"]) + (["Not sure"] if s.get("notSure") else []) + (["Other"] if s.get("other") else [])
        fields.append({"id": "label", "type": "multi" if s.get("multiple") else "choice", "label": s["question"],
                       "options": opts, "help": s.get("classHelp", ""), "required": True})
        if s.get("other"):
            fields.append({"id": "other", "type": "text", "label": "If other, what?", "multiline": False, "required": False})
        if s.get("confidence"):
            fields.append({"id": "confidence", "type": "rating", "label": "How sure are you?", "min": 1, "max": 5,
                           "min_label": "a guess", "max_label": "certain", "required": True})
    elif fam == "rate":
        lo, hi = s["scaleMin"], s["scaleMax"]
        words = s.get("pointLabels") or []
        per_point = words if len(words) == hi - lo + 1 else []
        ends = [words[0], words[-1]] if words else ["", ""]   # the ends keep their words even when the scale changes length
        for i, q in enumerate([s["question"], s.get("secondQuestion", "")]):
            if q:
                fields.append({"id": "score" if not i else "score_2", "type": "rating", "label": q, "min": lo, "max": hi,
                               "min_label": ends[0], "max_label": ends[1], "labels": per_point, "required": True})
    elif fam == "write":
        fields.append({"id": "text", "type": "text", "label": s["question"], "placeholder": s.get("placeholder", ""),
                       "multiline": bool(s.get("multiline", True)), "required": True})
        if s.get("extraRating"):
            fields.append({"id": "rating", "type": "rating", "label": s["extraRating"], "min": 1, "max": 5,
                           "min_label": "", "max_label": "", "required": True})
    if s.get("comment", "off") != "off":
        fields.append({"id": "comment", "type": "text", "label": "Comment", "multiline": False,
                       "required": s["comment"] == "required",
                       "placeholder": "Anything worth saying about this item" + (" (optional)" if s["comment"] == "optional" else "")})
    lang = s.get("language", "en")
    return {
        "title": s.get("title", ""), "summary": tpl.get("summary", ""), "instructions": s.get("instructions", ""),
        "fields": fields,
        "intro_fields": [{"id": slugify(k, 24).replace("-", "_"), **ABOUT[k]} for k in s.get("askAbout") or []],
        "look": {"theme": s.get("theme", "forest"), "accent": s.get("accent", ""), "layout": tpl.get("layout", "side"),
                 "dir": "rtl" if lang == "fa" else "ltr", "ui_lang": lang, "progress": s.get("progress", "bar")},
        "settings": {"labels_per_item": s.get("peoplePerItem", 1), "max_per_session": s.get("itemsPerPerson", 0),
                     "allow_skip": s.get("allowSkip", True), "shuffle": s.get("randomOrder", True),
                     "ask_name": s.get("askName", False), "must_listen": s.get("mustPlay", False),
                     "no_repeat": s.get("noRepeat", False), "allow_back": s.get("allowBack", False),
                     "final_comment": s.get("finalComment", False), "finish_charts": s.get("finishCharts") or [],
                     "result_charts": s.get("charts") or []},
    }


def compile_collect(tpl: dict, s: dict) -> dict:
    """The collection design (collect.validate_design) of a Data collection page."""
    name = s.get("languageName") or "English"
    code = slugify(name, 12) or "lang"
    ways = s.get("ways") or []
    return {
        "title": s.get("title", ""), "summary": tpl.get("summary", ""), "instructions": s.get("instructions", ""),
        "consent": s.get("consent", ""),
        "languages": [{"code": code, "name": name, "prompts": [{"id": f"p{i + 1}", "text": t} for i, t in enumerate(s["prompts"])]}],
        "conditions": [{"id": slugify(w, 20).replace("-", "_") or f"w{i + 1}", "label": w, "hint": ""} for i, w in enumerate(ways)]
        or [{"id": "plain", "label": "Plain", "hint": "In your normal voice."}],
        "look": {"theme": s.get("theme", "forest"), "accent": s.get("accent", "")},
        "settings": {"sample_rate": int(s.get("sampleRate", "16000")), "min_seconds": s.get("minSeconds", 1),
                     "max_seconds": s.get("maxSeconds", 15), "per_pair": s.get("peoplePerPrompt", 0),
                     "info": [INFO[k] for k in s.get("askAbout") or []], "info_required": [],
                     "allow_skip": s.get("allowSkip", True), "condition_order": s.get("wayOrder", "together"),
                     "prompt_kind": "question" if s.get("mode") == "answer" else "sentence"},
    }


# ----------------------------------------------------------------------------- what changed, in plain words
def _and(xs: list) -> str:
    xs = [str(x) for x in xs]
    return "none" if not xs else xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def describe(changes: dict, s: dict) -> str:
    """The changes in plain words, from the settings themselves (the chat shows this, not the model's own claim, so it
    never says something changed that did not): "Done: question “…” · classes: a, b and c · 3 people per item."."""
    on = lambda v, yes, no: yes if v else no
    nice = {"answers": "answer counts", "scores": "scores", "systems": "score per system", "agreement": "agreement",
            "timeline": "answers per day", "time-per-item": "time per item", "people": "items per person",
            "my-answers": "their own answers", "everyone": "everyone's answers", "my-time": "their time per item"}
    say = {
        "title": lambda v: f"title “{v}”", "instructions": lambda v: "new instructions", "question": lambda v: f"question “{v}”",
        "classes": lambda v: f"classes: {_and(v)}", "multiple": lambda v: on(v, "several classes allowed", "one class only"),
        "notSure": lambda v: on(v, "a “Not sure” option", "no “Not sure”"), "other": lambda v: on(v, "“Other” with a box", "no “Other”"),
        "classHelp": lambda v: "class definitions" if v else "no class definitions",
        "confidence": lambda v: on(v, "a “how sure” question", "no “how sure” question"),
        "mustPlay": lambda v: on(v, "answers after playing to the end", "answers any time"),
        "pointLabels": lambda v: f"scale words: {_and(v)}" if v else "numbers only on the scale",
        "secondQuestion": lambda v: f"second question “{v}”" if v else "no second question",
        "noRepeat": lambda v: on(v, "no sentence twice in a row", "sentences may repeat"), "placeholder": lambda v: "hint text in the box",
        "multiline": lambda v: on(v, "a big answer box", "a one-line answer box"),
        "extraRating": lambda v: f"extra rating “{v}”" if v else "no extra rating",
        "randomOrder": lambda v: on(v, "random order", "the same order for everyone"),
        "allowSkip": lambda v: on(v, "skipping allowed", "no skipping"), "allowBack": lambda v: on(v, "going back allowed", "no going back"),
        "askName": lambda v: on(v, "a name or ID asked", "no name asked"),
        "askAbout": lambda v: f"asked first: {_and(v)}" if v else "nothing asked first",
        "peoplePerItem": lambda v: f"{v} {'person' if v == 1 else 'people'} per item" if v else "everyone does every item",
        "itemsPerPerson": lambda v: f"at most {v} items per person" if v else "no limit per person",
        "comment": lambda v: {"off": "no comment box", "optional": "an optional comment box", "required": "a required comment box"}[v],
        "finalComment": lambda v: on(v, "an overall comment at the end", "no overall comment"),
        "practiceItems": lambda v: f"{v} practice items" if v else "no practice items", "progress": lambda v: f"progress as a {v}",
        "finishCharts": lambda v: f"last-screen charts: {_and([nice[x] for x in v])}" if v else "no last-screen charts",
        "charts": lambda v: f"results charts: {_and([nice[x] for x in v])}" if v else "no results charts",
        "theme": lambda v: f"{dict(forest='Green', dark='Night', light='Day').get(v, v.capitalize())} colours",
        "accent": lambda v: f"accent colour {v}" if v else "the mode's own accent", "language": lambda v: "page in Persian" if v == "fa" else "page in English",
        "consent": lambda v: "new consent text", "mode": lambda v: on(v == "answer", "people answer questions out loud", "people read prompts aloud"),
        "prompts": lambda v: f"{len(v)} {'prompt' if len(v) == 1 else 'prompts'}", "languageName": lambda v: f"language: {v}",
        "ways": lambda v: f"ways: {_and(v)}" if v else "one plain way", "wayOrder": lambda v: "the ways mixed" if v == "shuffled" else "the ways in turn",
        "minSeconds": lambda v: f"shortest recording {v} s", "maxSeconds": lambda v: f"longest recording {v} s",
        "sampleRate": lambda v: f"recording at {int(v) // 1000} kHz",
        "peoplePerPrompt": lambda v: f"{v} people per prompt" if v else "everyone records everything",
    }
    parts = [f"scale {s.get('scaleMin', 1)} to {s.get('scaleMax', 5)}"] if "scaleMin" in changes or "scaleMax" in changes else []
    parts += [say[k](v) for k, v in changes.items() if k in say]
    return f"Done: {' · '.join(parts)}." if parts else ""
