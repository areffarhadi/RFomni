"""The results file of a shared task, in the form its owner chose (design["output"]), and a sample of it.

    {"format": csv | json | jsonl,
     "rows": answer | item | person,     # one record per answer (item x person), per item (each person's answer
                                         # under their own key), or per person (their answers to every item)
     "include": [path, group, system, name, before_start, time, seconds, plays, majority, checks],
                                         # majority: the most common answer, or the mean of a number; checks: how many
                                         # items with known answers each person got right
     "names": {field id: column name}}   # a question's column; by default a short form of its words

The studio shows the sample (made-up answers, real item names when there are items) so the owner sees the file
before anyone answers, and can change it in the chat or by hand.
"""
import csv
import io
import json
import random
from collections import Counter

from . import blocks as BL
from .util import slugify

FORMATS = {"csv": "text/csv", "json": "application/json", "jsonl": "application/x-ndjson"}
ROWS = ("answer", "item", "person")
EXTRAS = ("path", "group", "system", "name", "before_start", "time", "seconds", "plays", "majority", "checks")
DEFAULT = {"format": "csv", "rows": "answer", "include": ["name", "before_start", "time", "seconds"], "names": {}}


def clean(o) -> dict:
    o = o if isinstance(o, dict) else {}
    names = o.get("names") if isinstance(o.get("names"), dict) else {}
    return {"format": o.get("format") if o.get("format") in FORMATS else DEFAULT["format"],
            "rows": o.get("rows") if o.get("rows") in ROWS else DEFAULT["rows"],
            "include": [x for x in dict.fromkeys(o.get("include") or []) if x in EXTRAS] if "include" in o else list(DEFAULT["include"]),
            "names": {str(k)[:40]: str(v).strip()[:60] for k, v in list(names.items())[:24] if str(v).strip()}}


def columns(design: dict) -> dict:
    """field id -> its column name: the owner's, or a short form of the question."""
    names, out, used = clean(design.get("output"))["names"], {}, set()
    for f in design["fields"]:
        c = names.get(f["id"]) or slugify(f["label"], 28).replace("-", "_") or f["id"]
        while c in used:
            c += "_2"
        used.add(c)
        out[f["id"]] = c
    return out


def _checks(answers: list, items: list) -> dict:
    gold = {i["id"]: i["gold"] for i in items if i.get("gold")}
    out: dict = {}
    for a in answers:
        g = gold.get(a["item"])
        if g:
            v = a["values"].get(g["field"])
            r = out.setdefault(a["session"], [0, 0])
            r[0] += (sorted(v) if isinstance(v, list) else v) == g["value"]
            r[1] += 1
    return out


def _records(design: dict, answers: list, items: list, sessions: dict) -> list:
    o = clean(design.get("output"))
    inc, fields, intro = set(o["include"]), design["fields"], design.get("intro_fields") or []
    col = columns(design)
    by_id = {i["id"]: i for i in items}
    chk = _checks(answers, items) if "checks" in inc else {}
    twice = {n for n, c in Counter(i["name"] for i in items).items() if c > 1}
    name = lambda iid: (lambda it: (it.get("rel_path") or it["name"]) if it.get("name") in twice else it.get("name", iid))(by_id.get(iid, {"name": iid}))

    def item_part(iid: str) -> dict:
        it = by_id.get(iid, {})
        rec = {"item": name(iid)}   # the same file name in two folders (versions of one sentence): its path
        for k, src in (("path", lambda: it.get("rel_path") or it.get("abs_path") or ""), ("group", lambda: it.get("group") or ""),
                       ("system", lambda: it.get("system") or "")):
            if k in inc:
                rec[k] = src()
        return rec

    def person_part(sid: str, name: str) -> dict:
        rec = {}
        if "name" in inc:
            rec["name"] = name or ""
        if "before_start" in inc and intro:
            info = (sessions.get(sid) or {}).get("info") or {}
            rec["before_start"] = {f["label"]: info.get(f["id"], "") for f in intro}
        if "checks" in inc and chk:
            rec["checks_right"] = f"{chk[sid][0]} of {chk[sid][1]}" if sid in chk else ""
        return rec

    order = {i["id"]: n for n, i in enumerate(items)}
    answers = sorted(answers, key=lambda a: (order.get(a["item"], 1e9), a["at"]))
    by_item: dict = {}
    for a in answers:
        by_item.setdefault(a["item"], []).append(a)

    def majority(aa: list) -> dict:
        out = {}
        for f in fields:
            vals = [a["values"].get(f["id"]) for a in aa if a["values"].get(f["id"]) not in (None, "")]
            if (f["type"] == "rating" and f.get("slider")) or f["type"] in ("number", "ab_scale", "mushra"):   # the mean
                out[col[f["id"]]] = round(sum(map(float, vals)) / len(vals), 2) if vals else None
            elif f["type"] in ("choice", "rating") + tuple(BL.OUTCOMES):
                votes = Counter(BL.keys(f, v)[0] for v in vals)
                out[col[f["id"]]] = votes.most_common(1)[0][0] if votes else None
        return out

    def compared(a: dict) -> dict:   # a comparison: the other items on its screen
        return {"compared_with": [name(i) for i in a["with"]]} if a.get("with") is not None else {}

    out = []
    if o["rows"] == "answer":
        for a in answers:
            rec = {**item_part(a["item"]), "person": a["session"], **person_part(a["session"], a.get("name", ""))}
            if "time" in inc:
                rec["answered_at"] = a["at"]
            if "seconds" in inc:
                rec["seconds"] = round(a["ms"] / 1000, 1) if a.get("ms") else None
            if "plays" in inc:
                rec["plays"] = a.get("plays")
            rec.update(compared(a))
            rec.update({col[f["id"]]: a["values"].get(f["id"]) for f in fields})
            if "majority" in inc:
                rec["majority"] = majority(by_item[a["item"]])
            out.append(rec)
    elif o["rows"] == "item":
        for iid, aa in by_item.items():
            rec = item_part(iid)
            for f in fields:
                rec[col[f["id"]]] = {a["session"]: a["values"].get(f["id"]) for a in aa}
            if "majority" in inc:
                rec["majority"] = majority(aa)
            out.append(rec)
    else:
        by_person: dict = {}
        for a in answers:
            by_person.setdefault(a["session"], []).append(a)
        for sid, aa in by_person.items():
            rec = {"person": sid, **person_part(sid, aa[0].get("name", ""))}
            rec["answers"] = {name(a["item"]) + (f" ({a['screen'][:4]})" if a.get("screen") else ""): {
                **compared(a), **{col[f["id"]]: a["values"].get(f["id"]) for f in fields},
                **({"majority": majority(by_item[a["item"]])} if "majority" in inc else {})} for a in aa}
            out.append(rec)
    return out


def _flat(rec: dict, pre: str = "") -> dict:
    out = {}
    for k, v in rec.items():
        key = f"{pre}{k}"
        if isinstance(v, dict):
            out.update(_flat(v, key + "."))
        else:
            out[key] = BL.cell(v)
    return out


def render(design: dict, answers: list, items: list, sessions: dict) -> tuple:
    """(text, media type, file extension) of the results file."""
    o = clean(design.get("output"))
    recs = _records(design, answers, items, sessions)
    if o["format"] == "json":
        return json.dumps(recs, ensure_ascii=False, indent=2) + "\n", FORMATS["json"], "json"
    if o["format"] == "jsonl":
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), FORMATS["jsonl"], "jsonl"
    flat = [_flat(r) for r in recs]
    head = list(dict.fromkeys(k for r in flat for k in r)) or ["item", "person"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=head, restval="")
    w.writeheader()
    w.writerows(flat)
    return buf.getvalue(), FORMATS["csv"], "csv"


SAMPLE_NAMES = {"audio": "clip_{}.wav", "video": "clip_{}.mp4", "image": "photo_{}.jpg", "text": "text_{}"}


def sample(design: dict, items: list, kind: str = "audio") -> tuple:
    """The file as it would look with three people and three items: made-up answers, real item names when there are
    items. -> (text, extension)"""
    rnd = random.Random(7)
    its = [i for i in items if not i.get("practice")][:3] or [
        {"id": f"i{n}", "name": SAMPLE_NAMES.get(kind, "item_{}").format(n), "rel_path": SAMPLE_NAMES.get(kind, "item_{}").format(n),
         "group": "", "system": ""} for n in (1, 2, 3)]
    people = ["7NDH-42LU", "QK3P-9TWA", "M2XF-6RBE"]
    names = ["Sara", "Ali", "Mina"]
    sessions = {}
    for sid in people:
        info = {}
        for f in design.get("intro_fields") or []:
            info[f["id"]] = rnd.choice(f["options"]) if f.get("options") else "…"
        sessions[sid] = {"info": info}
    answers = []
    cmp_ = next((f for f in design["fields"] if f["type"] in BL.COMPARE), None)
    for k, it in enumerate(its):
        for n, sid in enumerate(people):
            vals = {f["id"]: BL.sample(f, rnd, it) for f in design["fields"]}
            answers.append({"item": it["id"], "session": sid, "name": names[n], "values": vals,
                            "at": f"2026-11-10T10:{10 + 7 * n:02d}:0{len(answers) % 10}+00:00", "ms": rnd.randint(3000, 15000),
                            "plays": rnd.randint(1, 3),
                            **({"screen": f"s{k}{n}", "with": [its[(k + 1) % len(its)]["id"]]} if cmp_ else {})})
    text, _, ext = render(design, answers, its, sessions)
    return text, ext
