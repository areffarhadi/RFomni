"""The answer blocks a Task sharing or Subjective test page is made of: one entry per kind of question.

The owner and the page assistant (pages/agent.py) pick blocks and set them. For every kind this file says what people
do, which data it suits, how it is stored in the design, how an answer is checked, counted and scored, and how it
reads in the results file. The volunteer page draws them (frontend/task/task.js and frontend/task/blocks/*.js).

Names: the page view (pages/design.py) calls a kind by the name the owner and the model use ("time_ranges"); the design
stores it under its own type ("ranges"). Three kinds are variants of an older type: slider = rating + slider,
dropdown = choice + dropdown, correct_text = text + prefill.

Comparisons show several items on one screen (two clips, all versions of a sentence…). The answer to a screen is split
into one answer per item ("this clip was preferred", "scored 73"), so counts, scores per system and the results file
stay per item.
"""
import json
import random
import re
from dataclasses import dataclass

ALL = ("voice", "text", "image", "video")
STEPS = (1, 0.1, 0.01)   # a slider's step: whole numbers, one or two decimals


@dataclass(frozen=True)
class Block:
    kind: str           # its name in the page view
    stored: str         # its type in the design
    group: str          # basic, any, compare, time, picture, text
    media: tuple        # the data it suits (a page's modality)
    does: str           # what people do, in a line (the model's list, the owner's editor)
    keys: tuple = ()    # the view settings it uses besides question, help and required
    ask: str = ""       # how people ask for it: a new question of this kind needs these words in the message
    words: str = ""     # words that name it in a message ("remove the boxes")
    named: str = ""     # words that can only mean this kind ("MUSHRA"): when the model's page lacks it, it is told so


SLIDER_ASK = r"slid|knob|\bbar\b|continu|any (number|value|point)|decimal|drag|in between|\d\.\d|x\.x"
BLOCKS = [
    Block("buttons", "choice", "basic", ALL, "pick one answer", ("answers",), words="button class label category option choice"),
    Block("checkboxes", "multi", "basic", ALL, "pick several answers", ("answers",), words="checkbox tick class label category option several"),
    Block("scale", "rating", "basic", ALL, "a row of number buttons from `from` to `to` (words = one per point, or two for the ends)",
          ("from", "to", "words"), words="scale rating score rate"),
    Block("slider", "rating", "basic", ALL, "a sliding bar people drag to any number from `from` to `to` (decimals 0-2; words = the "
          "two ends)", ("from", "to", "words", "decimals"), SLIDER_ASK, "slider sliding bar knob scale rating score rate continuous"),
    Block("text", "text", "basic", ALL, "a box to write in (big_box for long answers; hint = grey text inside it)", ("big_box", "hint"),
          words="box caption transcript transcription comment writing write translation sentence window field area space"),
    Block("dropdown", "choice", "any", ALL, "pick one answer from a long list (up to 200 answers: languages, countries…)", ("answers",),
          r"drop.?down|menu|long list|select (from|one of)|list of (all )?(the )?(languages|countries|cities)|\b\d{2,} (options|answers|classes|languages|categories)",
          "dropdown menu list", r"drop.?down"),
    Block("number", "number", "any", ALL, "type a number from `from` to `to` (decimals 0-3; hint = the unit after it, like years)",
          ("from", "to", "decimals", "hint"),
          r"\bnumber\b|how many|\bcount\b|\bage\b|how (old|long|much|far|fast)|amount|numeric|digits|percent|%", "number count amount"),
    Block("ranking", "rank", "any", ALL, "put all the answers in order, best first", ("answers",),
          r"\brank|in order|order (them|these|the)|prioriti|sort (them|these|the)|from best to worst|most to least", "rank ranking order",
          r"\brank"),
    Block("grid", "grid", "any", ALL, "rate several aspects (answers = the aspects) on one scale from `from` to `to` (words = one per "
          "point, or two for the ends)", ("answers", "from", "to", "words"),
          r"\bgrid|aspects?|matrix|dimensions|several (scales|ratings)|(rate|score) (it |them )?(on|for) (several|each|these|multiple|different)|for each (aspect|quality|criteri)",
          "grid aspect matrix dimension"),
    Block("correct_text", "text", "any", ("voice", "text", "video"), "a box that starts with the item's own text (a transcript or a "
          "sentence) for people to correct (big_box, hint)", ("big_box", "hint"),
          r"correct|\bfix|\bedit|post.?edit|proofread|check the (transcript|text|sentence)|start(s)? with the (text|transcript)|pre.?fill",
          "correct correction fix edit transcript", r"start(s)? with the (automatic |asr )?(transcript|text)|pre.?fill|only correct"),
    # comparisons: several items on one screen
    Block("ab_choice", "ab_choice", "compare", ALL, "A/B test: two items side by side, A and B; people pick the better one (same = "
          "they may also say \"no difference\")", ("same",),
          r"\ba ?/ ?b\b|\bab\b|a.b test|compar|which (one )?(is|sounds|looks) better|prefer|side by side|two (clips|samples|images|pictures|videos|texts|versions|recordings)|pairwise|\bpairs?\b",
          "compare comparison pair prefer better side", r"\ba ?/ ?b\b|\bab test|a-b test|pairwise|side by side"),
    Block("ab_scale", "ab_scale", "compare", ALL, "CMOS test: two items, A and B; one question where people say how much better B "
          "is than A, from -3 (A much better) to +3 (B much better)", (),
          r"cmos|comparison mean|how much better|much better|slightly better|-3|−3|\+3|strength of (the )?preference|compar.{0,40}scale",
          "cmos compare comparison better scale", r"\bcmos\b|comparison mean opinion"),
    Block("abx", "abx", "compare", ("voice", "video", "image"), "ABX test: A, B and X, which is one of them; people say whether X "
          "is A or B (can they tell them apart?)", (),
          r"\babx\b|which (one )?(is|matches) x|(hear|see|tell) (a |the |any )?difference|discriminat|tell (them|the two) apart",
          "abx difference", r"\babx\b"),
    Block("mushra", "mushra", "compare", ("voice",), "MUSHRA test: every version of one sentence on one screen with the reference; "
          "people score each version from 0 to 100. One question does all of it", (),
          r"mushra|multiple stimuli|hidden reference|against (the |a )?reference|anchor", "mushra reference anchor", r"\bmushra\b"),
    Block("best_worst", "best_worst", "compare", ALL, "best-worst: `count` items (3-6) on one screen; one question where people pick "
          "both the best and the worst", ("count",), r"best.{0,15}worst|worst.{0,15}best|max.?diff", "best worst",
          r"best.{0,15}worst|worst.{0,15}best|max.?diff"),
    Block("pick_items", "pick_items", "compare", ALL, "pick items: `count` items (2-9) on one screen; people pick the one that fits "
          "the question (several = they may pick more than one)", ("count", "several"),
          r"\bpick (the|one|all|which|out)|which of (these|the) (\w+ )?(images|pictures|clips|videos|texts|items|photos|recordings)|grid of|"
          r"(choose|select) (the|one|all) (\w+ )?(images?|pictures?|clips?|photos?|videos?)|among (these|them)|odd one out",
          "pick choose among grid", r"pick (out )?(all )?the (ones|images|pictures|clips|photos|videos|recordings) (that|with|which|where)|odd one out"),
    # on the item itself
    Block("time_ranges", "ranges", "time", ("voice", "video"), "mark parts of the recording or video on its timeline and label each "
          "part (answers = the labels; none = no labels)", ("answers",),
          r"time ?ranges?|segment|mark (the )?(parts?|sections?|where|when|start|regions?)|start and end|start.{0,10}end time|from when|"
          r"timestamps?|diari[sz]|who (speaks|is speaking|talks) when|when (\w+ )?(speaks|starts|happens|talks)|onset|interval",
          "time range segment part section when", r"time ?ranges?|\bsegments?\b|start and end|who (speaks|talks|is speaking) when|diari[sz]"),
    Block("time_points", "moments", "time", ("voice", "video"), "moments: people tap the moment something happens in the recording or "
          "video (a laugh, a door, a beat) and label each (answers = the labels)", ("answers",),
          r"moments?|points? in time|time ?points?|instant|exact time|(tap|click|press) (a key |a button )?(when|each time|every time)|"
          r"each time|every time|beats?\b",
          "moment point time mark tap", r"\btaps?\b|each time|every time|moments? (when|where|something)"),
    Block("rating_over_time", "trace", "time", ("voice", "video"), "rating over time: a slider people keep moving while the clip "
          "plays, saved moment by moment, from `from` to `to` (words = the two ends)", ("from", "to", "words"),
          r"over time|while (it )?(plays|playing|listening|watching)|continuous(ly)? (rat|trac|annotat)|\btrac(e|ing)\b|moment.?by.?moment|"
          r"during the (clip|video|recording)|real.?time|as it (plays|goes)",
          "over time trace continuous while playing", r"while (it|the (clip|video|recording|audio))? ?(plays|is playing)|over time|"
          r"moment by moment|at each moment"),
    Block("boxes", "boxes", "picture", ("image", "video"), "draw boxes around things in the picture (or a paused video frame) and "
          "label each (answers = the labels, one for each kind of thing: car, person…)", ("answers",),
          r"bounding|\bdraw|box(es)? (around|over|on)|rectangles?|locat|detect|where (in|on) the (picture|image|photo|frame)",
          "box boxes draw rectangle bounding", r"bounding|boxes? around|draw (a )?box"),
    Block("points", "points", "picture", ("image", "video"), "click points on the picture (or a paused video frame) and label each "
          "(answers = the labels): key points, counting things", ("answers",),
          r"\bpoints?\b|click (on|where)|\bdots?\b|key ?points?|landmarks?|count (the|each|how)|\bpins?\b", "point dot click landmark"),
    Block("regions", "shapes", "picture", ("image", "video"), "outline regions of the picture (or a paused video frame) with corners "
          "and label each (answers = the labels)", ("answers",),
          r"polygon|outline|region|shapes?|segmentation|trace (the )?(edge|border|outline)|area of", "region outline polygon shape area",
          r"polygon|outline"),
    Block("highlights", "spans", "text", ("text",), "highlight words or phrases in the text and label each (answers = the labels, "
          "one for each kind of thing people mark: \"names of people, places and dates\" = person, place, date)", ("answers",),
          r"highlight|mark (the |each |any )?(words?|phrases?|names?|entit|errors?|mistakes?|parts? of the text)|select (the )?(words?|phrases?)|"
          r"\bspans?\b|entit|\bner\b|underline|tag (the |each )?words?", "highlight word phrase span entity", r"highlight"),
]
NAMES = {"buttons": "Buttons", "checkboxes": "Tick boxes", "scale": "Scale", "slider": "Slider", "text": "Text box",
         "dropdown": "Dropdown list", "number": "Number", "ranking": "Ranking", "grid": "Rating grid", "correct_text": "Correct the text",
         "ab_choice": "A or B", "ab_scale": "A/B scale (CMOS)", "abx": "ABX", "mushra": "MUSHRA", "best_worst": "Best and worst",
         "pick_items": "Pick items", "time_ranges": "Time ranges", "time_points": "Moments", "rating_over_time": "Rating over time",
         "boxes": "Boxes", "points": "Points", "regions": "Regions", "highlights": "Highlights"}
GROUPS = {"basic": "Questions", "any": "More questions", "compare": "Comparisons", "time": "On the recording or video",
          "picture": "On the picture", "text": "On the text"}
KIND = {b.kind: b for b in BLOCKS}
KINDS = tuple(KIND)
BASIC = ("buttons", "checkboxes", "scale", "text")   # always fine: the page had them from the start
MARKS = ("ranges", "moments", "boxes", "points", "shapes", "spans")   # labelled marks on the item
COMPARE = ("ab_choice", "ab_scale", "abx", "mushra", "best_worst", "pick_items")
ON_ITEM = MARKS + ("trace",)
FIELD_TYPES = ("choice", "multi", "rating", "text", "number", "rank", "grid", "trace") + MARKS + COMPARE
STORED = {b.stored: b.kind for b in BLOCKS if b.stored not in ("choice", "rating", "text")} | {
    "choice": "buttons", "multi": "checkboxes", "rating": "scale", "text": "text"}
CMOS = 3   # A/B strength: -3 (A much better) to +3 (B much better)
REF = re.compile(r"^(ref|reference|original|natural|ground.?truth|gt|target|clean)$", re.I)


def kind_of(f: dict) -> str:
    """The view name of a stored question."""
    if f["type"] == "rating" and f.get("slider"):
        return "slider"
    if f["type"] == "choice" and f.get("dropdown"):
        return "dropdown"
    if f["type"] == "text" and f.get("prefill"):
        return "correct_text"
    return STORED[f["type"]]


def menu(modality: str) -> list:
    """The kinds the owner can pick in the page editor, for this data."""
    plain = lambda t: re.sub(r"\s*\([^)]*\)", "", t).replace("`count` items", "several items").replace("from `from` to `to`", "between two numbers")
    return [{"kind": b.kind, "name": NAMES[b.kind], "group": GROUPS[b.group], "does": plain(b.does), "keys": list(b.keys)}
            for b in for_media(modality)]


def for_media(modality: str) -> list:
    """The blocks a page with this data may use (all of them when the data is not known)."""
    return [b for b in BLOCKS if not modality or modality in b.media]


def is_compare(f: dict) -> bool:
    return f["type"] in COMPARE


def screen_size(f: dict) -> int:
    """How many items a comparison shows (MUSHRA: every version of the sentence, up to 12)."""
    return {"best_worst": f.get("count", 4), "pick_items": f.get("count", 4), "mushra": 12}.get(f["type"], 2)


# ----------------------------------------------------------------------------- stored questions
def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return default


def _num(v, lo, hi, default):
    try:
        v = float(v)
        return default if v != v else max(lo, min(hi, v))
    except (TypeError, ValueError):
        return default


def _opts(v, n: int, size: int = 60) -> list:
    return list(dict.fromkeys(str(o).strip()[:size] for o in v or [] if str(o).strip()))[:n]


def _words(f: dict) -> dict:
    return {"min_label": str(f.get("min_label") or "").strip()[:40], "max_label": str(f.get("max_label") or "").strip()[:40]}


def stored(f: dict, typ: str, label: str) -> dict:
    """The settings of a stored question of this type (besides id, type, label, required and help), checked.
    Raises ValueError when the question cannot be shown."""
    if typ in ("choice", "multi", "rank"):
        drop = typ == "choice" and bool(f.get("dropdown"))
        opts = _opts(f.get("options"), 200 if drop else 12 if typ == "rank" else 20)
        if len(opts) < 2:
            raise ValueError(f"“{label}” needs at least two {'options' if typ != 'rank' else 'answers to put in order'}")
        return {"options": opts, **({"dropdown": True} if drop else {})}
    if typ == "rating":
        slider = bool(f.get("slider"))   # a sliding bar: any number from min to max, not only the points
        lo = _int(f.get("min"), 0, 100 if slider else 10, 1)
        hi = _int(f.get("max"), lo + 1, 100 if slider else 11, max(lo + 1, 5))
        out = {"min": lo, "max": hi, **_words(f)}
        if slider:
            out.update(slider=True, step=f.get("step") if f.get("step") in STEPS else 0.01)
        words = [str(w).strip()[:30] for w in f.get("labels") or []]
        if len(words) == hi - lo + 1 and not slider:  # a word under each point (MOS: Bad, Poor, Fair, Good, Excellent)
            out["labels"] = words
        return out
    if typ == "text":
        return {"multiline": bool(f.get("multiline", True)), "placeholder": str(f.get("placeholder") or "").strip()[:120],
                **({"prefill": True} if f.get("prefill") else {})}
    if typ == "number":
        lo = _num(f.get("min"), -1e9, 1e9, 0)
        return {"min": lo, "max": _num(f.get("max"), lo, 1e9, max(lo, 100)), "decimals": _int(f.get("decimals"), 0, 3, 0),
                "unit": str(f.get("unit") or "").strip()[:20]}
    if typ == "grid":
        rows = _opts(f.get("rows"), 12)
        if len(rows) < 2:
            raise ValueError(f"“{label}” needs at least two aspects to rate")
        lo = _int(f.get("min"), 0, 10, 1)
        hi = _int(f.get("max"), lo + 1, 11, max(lo + 1, 5))
        words = [str(w).strip()[:30] for w in f.get("labels") or []]
        return {"rows": rows, "min": lo, "max": hi, "labels": words if len(words) == hi - lo + 1 else [], **_words(f)}
    if typ in MARKS:
        return {"labels": _opts(f.get("labels"), 20, 40)}
    if typ == "trace":
        lo = _int(f.get("min"), 0, 99, 0)
        return {"min": lo, "max": _int(f.get("max"), lo + 1, 100, max(lo + 1, 10)), **_words(f)}
    if typ == "ab_choice":
        return {"same": bool(f.get("same", True))}
    if typ == "best_worst":
        return {"count": _int(f.get("count"), 3, 6, 4)}
    if typ == "pick_items":
        return {"count": _int(f.get("count"), 2, 9, 4), "several": bool(f.get("several"))}
    return {}   # ab_scale, abx, mushra: nothing to set


def check_page(fields: list) -> None:
    """A page shows its item one way: at most one comparison, at most one block that works on the item, not both."""
    cmp_ = [f["label"] for f in fields if f["type"] in COMPARE]
    on = [f["label"] for f in fields if f["type"] in ON_ITEM]
    if len(cmp_) > 1:
        raise ValueError(f"A page can have one comparison, not two (“{cmp_[0]}” and “{cmp_[1]}”)")
    if len(on) > 1:
        raise ValueError(f"A page can have one question that marks the item itself, not two (“{on[0]}” and “{on[1]}”)")
    if cmp_ and on:
        raise ValueError(f"A comparison (“{cmp_[0]}”) and marking the item (“{on[0]}”) cannot be on one page")


def decimals(f: dict) -> int:
    """How many digits after the point a slider's (or a number box's) answers keep."""
    if f["type"] == "number":
        return f.get("decimals", 0)
    return {1: 0, 0.1: 1, 0.01: 2}.get(f.get("step", 1), 2)


# ----------------------------------------------------------------------------- answers
def _f(v, lo=None, hi=None, nd=2):
    """A finite number in range, rounded; None when it is not."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")) or (lo is not None and v < lo) or (hi is not None and v > hi):
        return None
    return round(v, nd)


def _label(f: dict, x) -> str | None:
    """A mark's label: one of the question's labels, or "" when it has none."""
    labels = f.get("labels") or []
    x = str(x or "")
    return x if (x in labels if labels else True) else None


def _marks(f: dict, v, item: dict) -> list | None:
    if not isinstance(v, list):
        return None
    dur = item.get("duration")
    top = (float(dur) + 1) if isinstance(dur, (int, float)) and dur > 0 else None   # a little slack past the end
    out = []
    for m in v[:500]:
        if not isinstance(m, dict) or (lab := _label(f, m.get("label"))) is None:
            continue
        t = f["type"]
        if t == "ranges":
            a, b = _f(m.get("start"), 0, top), _f(m.get("end"), 0, top)
            if a is not None and b is not None and b > a:
                out.append({"start": a, "end": b, "label": lab})
        elif t == "moments":
            x = _f(m.get("t"), 0, top)
            if x is not None:
                out.append({"t": x, "label": lab})
        elif t == "spans":
            text = item.get("text") or ""
            a, b = _int(m.get("start"), 0, len(text), -1), _int(m.get("end"), 0, len(text), -1)
            if 0 <= a < b and not any(a < o["end"] and o["start"] < b for o in out):   # no overlaps
                out.append({"start": a, "end": b, "text": text[a:b], "label": lab})
        else:   # boxes, points, shapes: fractions of the picture; on a video, the time of the frame
            mark = {}
            if t == "boxes":
                x, y, w, h = (_f(m.get(k), 0, 1, 4) for k in ("x", "y", "w", "h"))
                if None in (x, y, w, h) or w <= 0 or h <= 0 or x + w > 1.0001 or y + h > 1.0001:
                    continue
                mark = {"x": x, "y": y, "w": w, "h": h}
            elif t == "points":
                x, y = _f(m.get("x"), 0, 1, 4), _f(m.get("y"), 0, 1, 4)
                if x is None or y is None:
                    continue
                mark = {"x": x, "y": y}
            else:
                pts = [[_f(p[0], 0, 1, 4), _f(p[1], 0, 1, 4)] for p in (m.get("points") or [])[:200]
                       if isinstance(p, (list, tuple)) and len(p) == 2]
                if len(pts) < 3 or any(None in p for p in pts):
                    continue
                mark = {"points": pts}
            if item.get("kind") == "video" and (tt := _f(m.get("t"), 0, top)) is not None:
                mark["t"] = tt
            out.append({**mark, "label": lab})
    key = {"ranges": "start", "moments": "t", "spans": "start"}.get(f["type"])
    return sorted(out, key=lambda m: m[key]) if key else out


def check(f: dict, v, item: dict, screen: dict | None = None):
    """An answer to one question, cleaned; None when it is missing or not valid. For a comparison, `screen` is what
    was shown ({"items": [ids], "x": id}) and the answer is to the whole screen (split() makes it per item)."""
    t = f["type"]
    if v is None or v == "" or v == [] or v == {}:
        return None
    if t == "choice":
        return v if v in f["options"] else None
    if t == "multi":
        return [o for o in f["options"] if o in (v if isinstance(v, list) else [])] or None
    if t == "rating":
        x = _f(v, f["min"], f["max"], 6)
        return None if x is None else round(x, decimals(f)) if f.get("slider") else x
    if t == "number":
        x = _f(v, f["min"], f["max"], 6)
        return None if x is None else round(x, f["decimals"]) if f.get("decimals") else int(round(x))
    if t == "text":
        return str(v).strip()[:5000] or None
    if t == "rank":   # every answer, each once
        return list(v) if isinstance(v, list) and sorted(map(str, v)) == sorted(f["options"]) else None
    if t == "grid":
        if not isinstance(v, dict):
            return None
        out = {r: int(x) for r in f["rows"] if (x := _f(v.get(r), f["min"], f["max"], 0)) is not None}
        return out if len(out) == len(f["rows"]) else None   # every aspect rated
    if t == "trace":
        dur = item.get("duration")
        top = (float(dur) + 1) if isinstance(dur, (int, float)) and dur > 0 else None
        pts = {}
        for p in v[:5000] if isinstance(v, list) else []:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                tt, x = _f(p[0], 0, top), _f(p[1], f["min"], f["max"])
                if tt is not None and x is not None:
                    pts[tt] = x
        return [[k, pts[k]] for k in sorted(pts)] if len(pts) >= 2 else None
    if t in MARKS:
        return _marks(f, v, item) or None
    ids = (screen or {}).get("items") or []
    if t == "ab_choice":
        return v if v in ids[:2] or (v == "same" and f.get("same")) else None
    if t == "ab_scale":
        x = _f(v, -CMOS, CMOS, 0)
        return None if x is None else int(x)
    if t == "abx":
        return v if v in ids[:2] else None
    if t == "mushra":
        out = {i: x for i in ids if isinstance(v, dict) and (x := _f(v.get(i), 0, 100, 0)) is not None}
        return {i: int(x) for i, x in out.items()} if len(out) == len(ids) else None   # every version scored
    if t == "best_worst":
        if isinstance(v, dict) and v.get("best") in ids and v.get("worst") in ids and v["best"] != v["worst"]:
            return {"best": v["best"], "worst": v["worst"]}
        return None
    if t == "pick_items":
        got = [i for i in ids if i in (v if isinstance(v, list) else [v])]
        return got if got and (f.get("several") or len(got) == 1) else None
    return None


def split(f: dict, v, screen: dict) -> dict:
    """A comparison's answer to a screen, as one answer per item shown: {item id: value}."""
    ids, t = screen["items"], f["type"]
    if t == "ab_choice":
        return {i: "same" if v == "same" else "better" if v == i else "worse" for i in ids}
    if t == "ab_scale":   # how much better this item was than the other
        return {ids[0]: -v, ids[1]: v}
    if t == "abx":
        return {i: "correct" if v == screen.get("x") else "wrong" for i in ids}
    if t == "mushra":
        return dict(v)
    if t == "best_worst":
        return {i: "best" if i == v["best"] else "worst" if i == v["worst"] else "neither" for i in ids}
    if t == "pick_items":
        return {i: "picked" if i in v else "not picked" for i in ids}
    return {i: v for i in ids}


OUTCOMES = {"ab_choice": ["better", "same", "worse"], "abx": ["correct", "wrong"], "best_worst": ["best", "neither", "worst"],
            "pick_items": ["picked", "not picked"]}
SCORE = {"ab_choice": {"better": 1, "same": .5, "worse": 0}, "abx": {"correct": 1, "wrong": 0},
         "best_worst": {"best": 1, "neither": 0, "worst": -1}, "pick_items": {"picked": 1, "not picked": 0}}
SCORE_NAME = {"rating": "mean", "number": "mean", "trace": "mean over time", "grid": "mean of the aspects",
              "ab_choice": "share preferred", "ab_scale": "CMOS", "abx": "share correct", "mushra": "MUSHRA score",
              "best_worst": "best minus worst", "pick_items": "share picked"}


def keys(f: dict, v) -> list:
    """What an answer adds to the answer counts: its answer, its points, its first place or its marks' labels.
    An answer of another shape (given before the question changed kind) adds nothing."""
    try:
        return _keys(f, v)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        return []


def _keys(f: dict, v) -> list:
    t = f["type"]
    if v is None:
        return []
    if t == "multi":
        return list(v)
    if t in ("rating", "ab_scale", "mushra"):
        x = float(round(v)) if f.get("slider") or t == "mushra" else float(v)
        return [str(int(x)) if x.is_integer() else str(x)]
    if t == "rank":
        return [v[0]] if v else []
    if t in MARKS:
        return [m["label"] or "mark" for m in v]
    if t in ("text", "number", "grid", "trace") or isinstance(v, (list, dict)):
        return []
    return [str(v)]


def score(f: dict, v) -> float | None:
    """An answer as a number to average (scores per system), or None for kinds without one (or another shape)."""
    try:
        return _score(f, v)
    except (TypeError, ValueError, KeyError, AttributeError, ZeroDivisionError):
        return None


def _score(f: dict, v) -> float | None:
    t = f["type"]
    if v is None:
        return None
    if t in ("rating", "number", "ab_scale", "mushra"):
        return float(v)
    if t in SCORE:
        return SCORE[t].get(v) if isinstance(v, str) else None
    if t == "grid":
        return sum(v.values()) / len(v) if v else None
    if t == "trace":
        return sum(x for _, x in v) / len(v) if v else None
    return None


def cell(v) -> str:
    """An answer in one cell of a CSV file: lists joined with "; ", marks and other structures as compact JSON."""
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(map(str, v)) if all(isinstance(x, (str, int, float)) for x in v) else json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return v


def sample(f: dict, rnd: random.Random, item: dict) -> object:
    """A made-up answer, for the sample results file the owner sees before anyone answers."""
    t, labels = f["type"], f.get("labels") or [""]
    if t == "choice":
        return rnd.choice(f["options"])
    if t == "multi":
        return rnd.sample(f["options"], min(len(f["options"]), rnd.choice((1, 2))))
    if t == "rating":
        return round(rnd.uniform(f["min"], f["max"]), decimals(f)) if f.get("slider") else rnd.randint(f["min"], f["max"])
    if t == "number":
        return round(rnd.uniform(f["min"], min(f["max"], f["min"] + 100)), f.get("decimals", 0))
    if t == "text":
        return "(the corrected text)" if f.get("prefill") else "(what they wrote)" if f.get("required") or rnd.random() < .5 else ""
    if t == "rank":
        return rnd.sample(f["options"], len(f["options"]))
    if t == "grid":
        return {r: rnd.randint(f["min"], f["max"]) for r in f["rows"]}
    if t == "trace":
        return [[i / 4, round(rnd.uniform(f["min"], f["max"]), 2)] for i in range(6)]
    if t == "ranges":
        a = round(rnd.uniform(0, 2), 2)
        return [{"start": a, "end": round(a + rnd.uniform(.5, 2), 2), "label": rnd.choice(labels)}]
    if t == "moments":
        return [{"t": round(rnd.uniform(0, 4), 2), "label": rnd.choice(labels)}]
    if t == "boxes":
        return [{"x": .21, "y": .3, "w": .25, "h": .4, "label": rnd.choice(labels)}]
    if t == "points":
        return [{"x": round(rnd.random(), 4), "y": round(rnd.random(), 4), "label": rnd.choice(labels)}]
    if t == "shapes":
        return [{"points": [[.2, .2], [.5, .25], [.35, .6]], "label": rnd.choice(labels)}]
    if t == "spans":
        text = item.get("text") or "a sample sentence"
        end = text.find(" ") if " " in text else len(text)
        return [{"start": 0, "end": end, "text": text[:end], "label": rnd.choice(labels)}]
    if t == "ab_scale":
        return rnd.randint(-CMOS, CMOS)
    if t == "mushra":
        return rnd.randint(20, 100)
    if t in OUTCOMES:
        return rnd.choice(OUTCOMES[t])
    return None


# ----------------------------------------------------------------------------- the page view
def to_view(f: dict) -> dict:
    """A stored question in the page view's words (pages/design.py)."""
    k = kind_of(f)
    q = {"question": f["label"], "type": k, "answers": [], "help": f.get("help", ""), "required": bool(f.get("required"))}
    if k in ("buttons", "checkboxes", "dropdown", "ranking"):
        q["answers"] = list(f["options"])
    elif k == "scale":
        words = f.get("labels") or ([f.get("min_label", ""), f.get("max_label", "")] if f.get("min_label") or f.get("max_label") else [])
        q.update({"from": f["min"], "to": f["max"], "words": words})
    elif k in ("slider", "rating_over_time"):
        words = [f.get("min_label", ""), f.get("max_label", "")] if f.get("min_label") or f.get("max_label") else []
        q.update({"from": f["min"], "to": f["max"], "words": words})
        if k == "slider":
            q["decimals"] = decimals(f)
    elif k in ("text", "correct_text"):
        q.update({"big_box": bool(f.get("multiline", True)), "hint": f.get("placeholder", "")})
    elif k == "number":
        q.update({"from": f["min"], "to": f["max"], "decimals": f.get("decimals", 0), "hint": f.get("unit", "")})
    elif k == "grid":
        words = f.get("labels") or ([f.get("min_label", ""), f.get("max_label", "")] if f.get("min_label") or f.get("max_label") else [])
        q.update({"answers": list(f["rows"]), "from": f["min"], "to": f["max"], "words": words})
    elif f["type"] in MARKS:
        q["answers"] = list(f.get("labels") or [])
    elif k == "ab_choice":
        q["same"] = bool(f.get("same", True))
    elif k in ("best_worst", "pick_items"):
        q["count"] = f.get("count", 4)
        if k == "pick_items":
            q["several"] = bool(f.get("several"))
    return q


def _list(v, n: int, size: int) -> list:
    if isinstance(v, str):
        v = re.split(r"\n|;|,", v)
    return list(dict.fromkeys(str(x).strip()[:size] for x in (v or []) if str(x).strip()))[:n]


def _bool(v, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "on", "1")


def clean_view(q: dict, k: str, text: str) -> dict:
    """The settings of a view question of kind k, checked and in range. Raises ValueError with a plain reason."""
    long_list = 200 if k == "dropdown" else 12 if k in ("ranking", "grid") else 20
    answers = _list(q.get("answers") or q.get("options") or q.get("labels"), long_list, 40 if KIND[k].group in ("time", "picture", "text") else 60)
    if k in ("buttons", "checkboxes", "dropdown", "ranking", "grid") and len(answers) < 2:
        raise ValueError(f"“{text}” needs at least two {'aspects' if k == 'grid' else 'answers'}")
    out = {}
    if "answers" in KIND[k].keys:
        out["answers"] = answers
    if k in ("scale", "grid"):
        nums = [int(a) for a in answers if re.fullmatch(r"-?\d+", a)] if k == "scale" else []   # it sometimes lists the points
        lo = _int(q.get("from"), 0, 10, min(nums) if nums else 1)
        hi = _int(q.get("to"), lo + 1, 11, max(nums) if nums and max(nums) > lo else max(lo + 1, 5))
        words = _list(q.get("words"), 11, 30)
        if words == [str(n) for n in range(lo, hi + 1)]:   # the numbers are on the scale already
            words = []
        out.update({"from": lo, "to": hi, "words": words})
    elif k in ("slider", "rating_over_time"):   # any number from one end to the other
        lo = _int(q.get("from"), 0, 99, 0)
        hi = _int(q.get("to"), lo + 1, 100, max(lo + 1, 10))
        words = _list(q.get("words"), 2, 30)
        out.update({"from": lo, "to": hi, "words": words if len(words) == 2 else []})
        if k == "slider":
            out["decimals"] = _int(q.get("decimals"), 0, 2, 2)
    elif k == "number":
        lo = _num(q.get("from"), -1e9, 1e9, 0)
        hi = _num(q.get("to"), lo, 1e9, max(lo, 100))
        out.update({"from": int(lo) if float(lo).is_integer() else lo, "to": int(hi) if float(hi).is_integer() else hi,
                    "decimals": _int(q.get("decimals"), 0, 3, 0), "hint": str(q.get("hint") or "").strip()[:20]})
    elif k in ("text", "correct_text"):
        out.update({"big_box": _bool(q.get("big_box"), True), "hint": str(q.get("hint") or "").strip()[:120]})
    elif k == "ab_choice":
        out["same"] = _bool(q.get("same"), True)
    elif k == "best_worst":
        out["count"] = _int(q.get("count"), 3, 6, 4)
    elif k == "pick_items":
        out.update({"count": _int(q.get("count"), 2, 9, 4), "several": _bool(q.get("several"), False)})
    return out


def to_design(q: dict) -> dict:
    """A (clean) view question's stored settings (besides id, label, required and help), with its stored type."""
    k = q["type"]
    b = KIND[k]
    f = {"type": b.stored}
    words = q.get("words") or []
    ends = {"min_label": words[0] if len(words) == 2 else "", "max_label": words[1] if len(words) == 2 else ""}
    if k in ("buttons", "checkboxes", "ranking", "dropdown"):
        f["options"] = q["answers"]
        if k == "dropdown":
            f["dropdown"] = True
    elif k in ("scale", "grid"):
        n = q["to"] - q["from"] + 1
        f.update({"min": q["from"], "max": q["to"], "labels": words if len(words) == n else [],
                  "min_label": words[0] if words and len(words) in (2, n) else "", "max_label": words[-1] if words and len(words) in (2, n) else ""})
        if k == "grid":
            f["rows"] = q["answers"]
    elif k == "slider":
        f.update({"min": q["from"], "max": q["to"], "slider": True, "step": {0: 1, 1: 0.1}.get(q.get("decimals"), 0.01), **ends})
    elif k == "rating_over_time":
        f.update({"min": q["from"], "max": q["to"], **ends})
    elif k == "number":
        f.update({"min": q["from"], "max": q["to"], "decimals": q.get("decimals", 0), "unit": q.get("hint", "")})
    elif k in ("text", "correct_text"):
        f.update({"multiline": q.get("big_box", True), "placeholder": q.get("hint", "")})
        if k == "correct_text":
            f["prefill"] = True
    elif b.stored in MARKS:
        f["labels"] = q.get("answers") or []
    elif k == "ab_choice":
        f["same"] = q.get("same", True)
    elif k in ("best_worst", "pick_items"):
        f["count"] = q.get("count", 4)
        if k == "pick_items":
            f["several"] = q.get("several", False)
    return f


def _and(xs: list) -> str:
    xs = [str(x) for x in xs]
    return "none" if not xs else xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def what(q: dict) -> str:
    """A view question's kind and settings in a few plain words ("pick one: yes, no")."""
    k = q["type"]
    labelled = lambda s: f"{s}, labels: {_and(q['answers'])}" if q.get("answers") else s
    if k in ("buttons", "checkboxes", "dropdown"):
        return f"{dict(checkboxes='pick several: ', dropdown='a list: ').get(k, '')}{_and(q['answers'])}"
    if k == "scale":
        return f"scale {q['from']} to {q['to']}"
    if k == "slider":
        d = q.get("decimals", 2)
        return f"a slider from {q['from']} to {q['to']}" + (f", answers like {q['from'] + (q['to'] - q['from']) * .725:.{d}f}" if d else ", whole numbers")
    if k == "text":
        return "a text box"
    if k == "correct_text":
        return "a text box that starts with the item's text"
    if k == "number":
        return f"a number from {q['from']} to {q['to']}" + (f" {q['hint']}" if q.get("hint") else "")
    if k == "ranking":
        return f"put in order: {_and(q['answers'])}"
    if k == "grid":
        return f"rate {_and(q['answers'])} from {q['from']} to {q['to']}"
    if k == "ab_choice":
        return "A or B, the better one" + (", or no difference" if q.get("same") else "")
    if k == "ab_scale":
        return "A and B, from -3 to +3"
    if k == "abx":
        return "is X A or B"
    if k == "mushra":
        return "every version scored 0 to 100 against the reference"
    if k == "best_worst":
        return f"the best and the worst of {q.get('count', 4)}"
    if k == "pick_items":
        return f"pick {'the ones' if q.get('several') else 'the one'} that fit, of {q.get('count', 4)}"
    if k == "rating_over_time":
        return f"a rating from {q['from']} to {q['to']} while it plays"
    return labelled({"time_ranges": "time ranges", "time_points": "moments in time", "boxes": "boxes on the picture",
                     "points": "points on the picture", "regions": "outlined regions", "highlights": "highlighted words"}[k])
