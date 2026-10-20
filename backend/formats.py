"""Import / export: ELAN (.eaf), Praat TextGrid, native JSON, Omni pipeline JSON,
SRT, WebVTT, CSV, RTTM and a JSONL training manifest."""
import csv
import io
import json
import mimetypes
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import tasks as task_reg
from .util import new_id, now_iso, slugify

PALETTE = ["#38bdf8", "#f472b6", "#fb923c", "#a78bfa", "#34d399", "#facc15", "#22d3ee", "#fb7185",
           "#818cf8", "#a3e635", "#f59e0b", "#2dd4bf"]

ALIASES = {
    "asr": ["transcription", "transcript", "asr", "utterance", "utterances", "sentence", "sentences",
            "text", "ortho", "orthography", "default"],
    "forced_alignment": ["words", "word", "alignment", "tokens", "forced_alignment"],
    "phoneme": ["phones", "phone", "phonemes", "phoneme", "ipa"],
    "diarization": ["speaker", "speakers", "diarization", "spk", "speaker_turns"],
    "ser": ["emotion", "emotions", "ser"],
    "lid": ["language", "lang", "lid"],
    "sound_quality": ["quality", "sound_quality", "audio_quality"],
    "ner": ["named_entities", "named entities", "ner", "entities"],
    "translation": ["translation", "english", "s2tt"],
    "sentiment": ["sentiment"],
}
_ALIAS_LOOKUP = {a: tid for tid, names in ALIASES.items() for a in names}


def val_str(v) -> str:
    if isinstance(v, list):
        return "; ".join(v)
    return "" if v is None else str(v)


def temporal(s) -> bool:
    return s.get("start") is not None and s.get("end") is not None


def tier_segments(doc, tier_id):
    return sorted([s for s in doc["segments"] if s["tier_id"] == tier_id and temporal(s)],
                  key=lambda s: (s["start"], s["end"]))


def find_tier(doc, *task_ids):
    for tid in task_ids:
        for t in doc["tiers"]:
            if t.get("task_id") == tid or t["id"] == tid:
                return t
    return None


def fmt_ts(t: float, sep: str = ",") -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


# =============================================================================
# Export
# =============================================================================
def export_json(doc, media) -> str:
    out = {"format": "omni-studio/1", "exported_at": now_iso(),
           "media": {k: media.get(k) for k in ("id", "name", "rel_path", "modality", "duration",
                                                "sample_rate", "width", "height", "fps")},
           "tiers": doc["tiers"], "segments": doc["segments"]}
    return json.dumps(out, ensure_ascii=False, indent=1)


def export_eaf(doc, media, media_path: Path) -> str:
    root = ET.Element("ANNOTATION_DOCUMENT", {
        "AUTHOR": "Omni Studio", "DATE": now_iso(), "FORMAT": "3.0", "VERSION": "3.0",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:noNamespaceSchemaLocation": "http://www.mpi.nl/tools/elan/EAFv3.0.xsd"})
    header = ET.SubElement(root, "HEADER", {"MEDIA_FILE": "", "TIME_UNITS": "milliseconds"})
    mime = mimetypes.guess_type(str(media_path))[0] or ("video/mp4" if media.get("modality") == "video" else "audio/x-wav")
    if mime == "audio/wav":
        mime = "audio/x-wav"
    ET.SubElement(header, "MEDIA_DESCRIPTOR", {
        "MEDIA_URL": Path(media_path).resolve().as_uri(), "MIME_TYPE": mime,
        "RELATIVE_MEDIA_URL": "./" + Path(media_path).name})
    order = ET.SubElement(root, "TIME_ORDER")
    slots, n_slot, n_ann = {}, 0, 0

    def slot(t):
        nonlocal n_slot
        ms = int(round(t * 1000))
        if ms not in slots:
            n_slot += 1
            slots[ms] = f"ts{n_slot}"
        return slots[ms]

    tier_elems = []
    for t in doc["tiers"]:
        segs = tier_segments(doc, t["id"])
        tel = ET.Element("TIER", {"LINGUISTIC_TYPE_REF": "default-lt", "TIER_ID": t["name"]})
        for s in segs:
            n_ann += 1
            ann = ET.SubElement(tel, "ANNOTATION")
            al = ET.SubElement(ann, "ALIGNABLE_ANNOTATION", {
                "ANNOTATION_ID": f"a{n_ann}", "TIME_SLOT_REF1": slot(s["start"]), "TIME_SLOT_REF2": slot(s["end"])})
            ET.SubElement(al, "ANNOTATION_VALUE").text = val_str(s["value"])
        tier_elems.append(tel)
    for ms in sorted(slots):
        ET.SubElement(order, "TIME_SLOT", {"TIME_SLOT_ID": slots[ms], "TIME_VALUE": str(ms)})
    ET.SubElement(header, "PROPERTY", {"NAME": "lastUsedAnnotationId"}).text = str(n_ann)
    # ELAN expects time slot ids in increasing time order; renumber to be safe
    remap = {old: f"ts{i + 1}" for i, (ms, old) in enumerate(sorted(slots.items()))}
    for el in order:
        el.set("TIME_SLOT_ID", remap[el.get("TIME_SLOT_ID")])
    for tel in tier_elems:
        for al in tel.iter("ALIGNABLE_ANNOTATION"):
            al.set("TIME_SLOT_REF1", remap[al.get("TIME_SLOT_REF1")])
            al.set("TIME_SLOT_REF2", remap[al.get("TIME_SLOT_REF2")])
        root.append(tel)
    ET.SubElement(root, "LINGUISTIC_TYPE", {"GRAPHIC_REFERENCES": "false", "LINGUISTIC_TYPE_ID": "default-lt",
                                            "TIME_ALIGNABLE": "true"})
    for stereo, desc in [
        ("Time_Subdivision", "Time subdivision of parent annotation's time interval, no time gaps allowed within this interval"),
        ("Symbolic_Subdivision", "Symbolic subdivision of a parent annotation. Annotations refering to the same parent are ordered"),
        ("Symbolic_Association", "1-1 association with a parent annotation"),
        ("Included_In", "Time alignable annotations within the parent annotation's time interval, gaps are allowed")]:
        ET.SubElement(root, "CONSTRAINT", {"DESCRIPTION": desc, "STEREOTYPE": stereo})
    ET.indent(root, "    ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def export_textgrid(doc, media) -> str:
    dur = float(media.get("duration") or 0)
    for s in doc["segments"]:
        if temporal(s):
            dur = max(dur, s["end"])

    def q(x):
        return '"' + str(x).replace('"', '""') + '"'

    tiers_out = []
    for t in doc["tiers"]:
        segs = tier_segments(doc, t["id"])
        if not segs:
            continue
        intervals, cur = [], 0.0
        for s in segs:
            a, b = max(s["start"], cur), s["end"]
            if b <= a:
                continue  # Praat interval tiers cannot overlap
            if a > cur + 1e-6:
                intervals.append((cur, a, ""))
            intervals.append((a, b, val_str(s["value"])))
            cur = b
        if cur < dur - 1e-6:
            intervals.append((cur, dur, ""))
        tiers_out.append((t["name"], intervals))
    lines = ['File type = "ooTextFile"', 'Object class = "TextGrid"', "", "xmin = 0 ", f"xmax = {dur} ",
             "tiers? <exists> ", f"size = {len(tiers_out)} ", "item []: "]
    for i, (name, ivs) in enumerate(tiers_out, 1):
        lines += [f"    item [{i}]:", '        class = "IntervalTier" ', f"        name = {q(name)} ",
                  "        xmin = 0 ", f"        xmax = {dur} ", f"        intervals: size = {len(ivs)} "]
        for j, (a, b, txt) in enumerate(ivs, 1):
            lines += [f"        intervals [{j}]:", f"            xmin = {round(a, 4)} ",
                      f"            xmax = {round(b, 4)} ", f"            text = {q(txt)} "]
    return "\n".join(lines) + "\n"


def _subtitle_tier(doc):
    return find_tier(doc, "asr", "translation", "dense_caption", "speech_caption") or next(
        (t for t in doc["tiers"] if t["output"] == "text" and t.get("granularity") not in ("word", "phone", "file")
         and tier_segments(doc, t["id"])), None)


def export_srt(doc, media):
    t = _subtitle_tier(doc)
    if not t:
        return None
    spk = find_tier(doc, "diarization")
    out = []
    for i, s in enumerate(tier_segments(doc, t["id"]), 1):
        txt = val_str(s["value"])
        if spk:
            who = overlap_value(doc, spk["id"], s["start"], s["end"])
            if who:
                txt = f"[{who}] {txt}"
        out.append(f"{i}\n{fmt_ts(s['start'])} --> {fmt_ts(s['end'])}\n{txt}\n")
    return "\n".join(out)


def export_vtt(doc, media):
    t = _subtitle_tier(doc)
    if not t:
        return None
    out = ["WEBVTT", ""]
    spk = find_tier(doc, "diarization")
    for s in tier_segments(doc, t["id"]):
        txt = val_str(s["value"])
        who = overlap_value(doc, spk["id"], s["start"], s["end"]) if spk else None
        if who:
            txt = f"<v {who}>{txt}"
        out += [f"{fmt_ts(s['start'], '.')} --> {fmt_ts(s['end'], '.')}", txt, ""]
    return "\n".join(out)


def export_rttm(doc, media):
    t = find_tier(doc, "diarization")
    if not t:
        return None
    uri = slugify(Path(media["name"]).stem, 80) or media["id"]
    rows = [f"SPEAKER {uri} 1 {s['start']:.3f} {s['end'] - s['start']:.3f} <NA> <NA> {val_str(s['value']) or 'spk'} <NA> <NA>"
            for s in tier_segments(doc, t["id"])]
    return "\n".join(rows) + "\n"


CSV_FIELDS = ["file", "media_id", "modality", "tier", "task_id", "start", "end", "duration", "value",
              "confidence", "source", "reviewed", "geom"]


def csv_rows(doc, media):
    tiers = {t["id"]: t for t in doc["tiers"]}
    for s in doc["segments"]:
        t = tiers.get(s["tier_id"], {})
        dur = round(s["end"] - s["start"], 3) if temporal(s) else ""
        yield {"file": media.get("rel_path") or media["name"], "media_id": media["id"],
               "modality": media["modality"], "tier": t.get("name", s["tier_id"]), "task_id": t.get("task_id") or "",
               "start": "" if s["start"] is None else s["start"], "end": "" if s["end"] is None else s["end"],
               "duration": dur, "value": val_str(s["value"]),
               "confidence": "" if s["confidence"] is None else s["confidence"],
               "source": s.get("source", ""), "reviewed": int(bool(s.get("reviewed"))),
               "geom": json.dumps(s["geom"]) if s.get("geom") else ""}


def export_csv(pairs) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    w.writeheader()
    for media, doc in pairs:
        for row in csv_rows(doc, media):
            w.writerow(row)
    return buf.getvalue()


def overlap_value(doc, tier_id, a, b):
    best, best_ov = None, 0.0
    for s in doc["segments"]:
        if s["tier_id"] != tier_id or not temporal(s):
            continue
        ov = min(b, s["end"]) - max(a, s["start"])
        if ov > best_ov:
            best, best_ov = s, ov
    return val_str(best["value"]) if best else None


def spatial_items(doc, tier):
    """Boxes / keypoints of a tier for the manifest, in pixels when the image size is known."""
    out = []
    for s in doc["segments"]:
        if s["tier_id"] != tier["id"] or not s.get("geom"):
            continue
        g = s["geom"]
        item = {"label": val_str(s["value"]), "confidence": s.get("confidence"), "reviewed": bool(s.get("reviewed"))}
        if g["type"] == "box":
            item["box"] = [g["x"], g["y"], g["w"], g["h"]]
        else:
            item["keypoints"] = g["points"]
            if g.get("box"):
                item["box"] = g["box"]
        out.append(item)
    return out


def manifest_records(doc, media, path: str, anchor: str = "asr") -> list:
    """One record per anchor segment, with the overlapping value of every other tier.
    Good starting point for training data (e.g. Qwen3-Omni fine-tuning).
    Images: one record with file-level values plus boxes / keypoints (normalised 0-1, x y w h)."""
    tiers = doc["tiers"]
    spatial = {t["id"] for t in tiers if t.get("output") in ("boxes", "keypoints")}
    file_level = {t["name"]: val_str(s["value"]) for t in tiers for s in doc["segments"]
                  if t["id"] not in spatial and s["tier_id"] == t["id"] and (not temporal(s) or t.get("granularity") == "file")}
    for t in tiers:
        if t["id"] in spatial:
            items = spatial_items(doc, t)
            if items:
                file_level[t["name"]] = items
    base = {"media": path, "media_id": media["id"], "modality": media["modality"]}
    if media["modality"] == "image" or not tiers:
        return [{**base, **file_level}] if file_level else []
    at = find_tier(doc, anchor, "asr", "vad") or next(
        (t for t in tiers if t.get("granularity") in ("segment", "window") and tier_segments(doc, t["id"])), None)
    if not at:
        return [{**base, **file_level}] if file_level else []
    recs = []
    for s in tier_segments(doc, at["id"]):
        r = {**base, "start": s["start"], "end": s["end"], at["name"]: val_str(s["value"]),
             "reviewed": bool(s.get("reviewed"))}
        for t in tiers:
            if t["id"] == at["id"] or t["name"] in file_level:
                continue
            if t.get("granularity") in ("word", "phone"):
                units = [{"text": val_str(u["value"]), "start": u["start"], "end": u["end"]}
                         for u in tier_segments(doc, t["id"])
                         if u["start"] >= s["start"] - 0.01 and u["end"] <= s["end"] + 0.01]
                if units:
                    r[t["name"]] = units
            else:
                v = overlap_value(doc, t["id"], s["start"], s["end"])
                if v is not None:
                    r[t["name"]] = v
        r.update(file_level)
        recs.append(r)
    return recs


COCO_KP = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
           "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
           "left_knee", "right_knee", "left_ankle", "right_ankle"]
COCO_SKELETON = [[16, 14], [14, 12], [17, 15], [15, 13], [12, 13], [6, 12], [7, 13], [6, 7], [6, 8], [7, 9],
                 [8, 10], [9, 11], [2, 3], [1, 2], [1, 3], [2, 4], [3, 5], [4, 6], [5, 7]]


def export_coco(pairs) -> str:
    """COCO-format JSON with every box and pose of the given (media, doc) image pairs (pixel coordinates)."""
    images, anns, cats = [], [], {}

    def cat(name, kp=False):
        if name not in cats:
            c = {"id": len(cats) + 1, "name": name, "supercategory": "person" if kp or name == "person" else "object"}
            if kp:
                c.update(keypoints=COCO_KP, skeleton=COCO_SKELETON)
            cats[name] = c
        elif kp and "keypoints" not in cats[name]:
            cats[name].update(keypoints=COCO_KP, skeleton=COCO_SKELETON)
        return cats[name]["id"]

    for iid, (media, doc) in enumerate([p for p in pairs if p[0]["modality"] == "image"], 1):
        W, H = media.get("width") or 1, media.get("height") or 1
        images.append({"id": iid, "file_name": media.get("rel_path") or media["name"], "width": W, "height": H})
        tiers = {t["id"]: t for t in doc["tiers"]}
        for s in doc["segments"]:
            g, t = s.get("geom"), tiers.get(s["tier_id"], {})
            if not g:
                continue
            a = {"id": len(anns) + 1, "image_id": iid, "iscrowd": 0, "tier": t.get("name"), "task_id": t.get("task_id"),
                 "score": s.get("confidence"), "source": s.get("source"), "reviewed": bool(s.get("reviewed"))}
            if g["type"] == "box":
                b = [g["x"] * W, g["y"] * H, g["w"] * W, g["h"] * H]
                a.update(category_id=cat(val_str(s["value"]) or "object"), bbox=[round(v, 2) for v in b],
                         area=round(b[2] * b[3], 2))
            else:
                pts = g["points"]
                kp = []
                for x, y, v in pts:
                    kp += [round(x * W, 2), round(y * H, 2), int(v)] if v else [0, 0, 0]
                vis = [(x * W, y * H) for x, y, v in pts if v]
                if g.get("box"):
                    bx = g["box"]
                    b = [bx[0] * W, bx[1] * H, bx[2] * W, bx[3] * H]
                elif vis:
                    xs, ys = [p[0] for p in vis], [p[1] for p in vis]
                    b = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
                else:
                    b = [0, 0, 0, 0]
                a.update(category_id=cat(val_str(s["value"]) or "person", kp=True), keypoints=kp,
                         num_keypoints=sum(1 for p in pts if p[2]), bbox=[round(v, 2) for v in b], area=round(b[2] * b[3], 2))
            anns.append(a)
    return json.dumps({"info": {"description": "Omni Studio export", "date_created": now_iso()},
                       "images": images, "annotations": anns, "categories": list(cats.values())}, ensure_ascii=False, indent=1)


# =============================================================================
# Import
# =============================================================================
def _tier_def(name: str, used: set, idx: int, task_id=None):
    task = task_reg.get_task(task_id) if task_id else None
    if task_id is None:
        key = re.sub(r"[^a-z]+", "_", name.lower()).strip("_")
        task_id = _ALIAS_LOOKUP.get(key) or _ALIAS_LOOKUP.get(name.lower())
        if task_id is None and key.startswith("speaker"):
            task_id = "diarization"
        task = task_reg.get_task(task_id) if task_id else None
    if task and task["id"] not in used:
        tid = task["id"]
        tier = {"id": tid, "task_id": tid, "name": task["name"], "title": task.get("title"), "color": task["color"],
                "output": task["output"], "granularity": task.get("granularity"), "labels": task.get("labels", []),
                "parent": task.get("parent"), "source": "import"}
        if name.lower() not in (task["name"].lower(), task["id"]):
            tier["title"] = f"{task.get('title')} ({name})"
    else:
        tid = "t_" + slugify(name, 30).replace("-", "_")
        while tid in used:
            tid += "_" + new_id(nbytes=1)
        tier = {"id": tid, "task_id": None, "name": name[:40], "title": name, "color": PALETTE[idx % len(PALETTE)],
                "output": "text", "granularity": "segment", "labels": [], "parent": None, "source": "import"}
    used.add(tid)
    return tier


def import_eaf(data: bytes) -> dict:
    root = ET.fromstring(data)
    slots = {}
    for ts in root.iter("TIME_SLOT"):
        if ts.get("TIME_VALUE") is not None:
            slots[ts.get("TIME_SLOT_ID")] = int(ts.get("TIME_VALUE")) / 1000.0
    ann_times = {}
    tiers, segs, used = [], [], set()
    tier_elems = list(root.iter("TIER"))
    # alignable annotations first so that reference annotations can inherit times
    for tel in tier_elems:
        for al in tel.iter("ALIGNABLE_ANNOTATION"):
            a, b = slots.get(al.get("TIME_SLOT_REF1")), slots.get(al.get("TIME_SLOT_REF2"))
            if a is not None and b is not None:
                ann_times[al.get("ANNOTATION_ID")] = (a, b)
    for _ in range(5):
        for tel in tier_elems:
            for ref in tel.iter("REF_ANNOTATION"):
                if ref.get("ANNOTATION_ID") not in ann_times and ref.get("ANNOTATION_REF") in ann_times:
                    ann_times[ref.get("ANNOTATION_ID")] = ann_times[ref.get("ANNOTATION_REF")]
    for i, tel in enumerate(tier_elems):
        tier = _tier_def(tel.get("TIER_ID") or f"tier{i + 1}", used, i)
        n = 0
        for ann in tel:
            for el in ann:
                times = ann_times.get(el.get("ANNOTATION_ID"))
                if not times:
                    continue
                v = el.findtext("ANNOTATION_VALUE") or ""
                value = [x.strip() for x in v.split(";") if x.strip()] if tier["output"] == "tags" else v
                segs.append({"id": new_id("s"), "tier_id": tier["id"], "start": times[0], "end": times[1],
                             "value": value, "confidence": None, "source": "import", "reviewed": False})
                n += 1
        tiers.append(tier)
    return {"tiers": tiers, "segments": segs}


def import_textgrid(text: str) -> dict:
    text = re.sub(r"\[\s*\d*\s*\]", "", text)
    toks = re.findall(r'"(?:[^"]|"")*"|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|<exists>|<absent>', text)
    pos = 0

    def nxt():
        nonlocal pos
        t = toks[pos]
        pos += 1
        return t[1:-1].replace('""', '"') if t.startswith('"') else t

    if nxt() != "ooTextFile" or nxt() != "TextGrid":
        raise ValueError("not a TextGrid")
    nxt(), nxt()  # xmin xmax
    if nxt() != "<exists>":
        return {"tiers": [], "segments": []}
    n_tiers = int(float(nxt()))
    tiers, segs, used = [], [], set()
    for i in range(n_tiers):
        cls, name = nxt(), nxt()
        nxt(), nxt()
        n = int(float(nxt()))
        tier = _tier_def(name, used, i)
        tiers.append(tier)
        for _ in range(n):
            if cls == "IntervalTier":
                a, b, txt = float(nxt()), float(nxt()), nxt()
            else:
                a = float(nxt())
                b, txt = a, nxt()
            if txt.strip():
                segs.append({"id": new_id("s"), "tier_id": tier["id"], "start": a, "end": b, "value": txt,
                             "confidence": None, "source": "import", "reviewed": False})
    return {"tiers": tiers, "segments": segs}


def import_omni_json(d: dict) -> dict:
    """The vLLM-omni pipeline output: {"sentences": [{alignment, text, speaker, emotion, sound_quality, ner}]}"""
    used = set()
    T = {k: _tier_def(k, used, i, task_id=k) for i, k in enumerate(
        ["asr", "forced_alignment", "diarization", "ser", "sound_quality", "ner"])}
    for t in T.values():
        t["source"] = "omni-json"
    segs = []

    def add(tier, a, b, v, conf=None):
        segs.append({"id": new_id("s"), "tier_id": T[tier]["id"], "start": a, "end": b, "value": v,
                     "confidence": conf, "source": "model", "reviewed": False})

    for s in d.get("sentences", []):
        al = s.get("alignment") or {}
        a, b = al.get("start_sec"), al.get("end_sec")
        if a is None or b is None:
            continue
        add("asr", a, b, s.get("text", ""))
        for w in al.get("words") or []:
            if w.get("start_sec") is not None:
                add("forced_alignment", w["start_sec"], w["end_sec"], w.get("word", ""))
        if s.get("speaker"):
            add("diarization", a, b, s["speaker"])
        emo = s.get("emotion") or {}
        if emo.get("label"):
            add("ser", a, b, emo["label"], emo.get("confidence"))
        q = s.get("sound_quality") or {}
        if q.get("level"):
            add("sound_quality", a, b, q["level"], q.get("clarity_confidence"))
        ents = (s.get("ner") or {}).get("entities") or []
        if ents:
            confs = [e.get("confidence") for e in ents if e.get("confidence") is not None]
            add("ner", a, b, [f"{e.get('text')} [{e.get('type')}]" for e in ents],
                min(confs) if confs else None)
    present = {s["tier_id"] for s in segs}
    return {"tiers": [t for t in T.values() if t["id"] in present], "segments": segs}


def import_any(filename: str, data: bytes) -> dict:
    name = filename.lower()
    if name.endswith(".eaf"):
        return import_eaf(data)
    if name.endswith(".textgrid"):
        for enc in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                return import_textgrid(data.decode(enc))
            except (UnicodeDecodeError, ValueError, IndexError):
                continue
        raise ValueError("could not parse TextGrid")
    if name.endswith(".json"):
        d = json.loads(data.decode("utf-8-sig"))
        if isinstance(d, dict) and "sentences" in d:
            return import_omni_json(d)
        if isinstance(d, dict) and "tiers" in d and "segments" in d:
            return {"tiers": d["tiers"], "segments": [{**s, "id": s.get("id") or new_id("s")} for s in d["segments"]]}
        raise ValueError("unknown JSON annotation format")
    raise ValueError("supported: .eaf, .TextGrid, .json")


def merge_import(doc: dict, imported: dict) -> dict:
    """Replace tiers that share an id with the imported ones, keep everything else."""
    ids = {t["id"] for t in imported["tiers"]}
    tiers = [t for t in doc["tiers"] if t["id"] not in ids] + imported["tiers"]
    segs = [s for s in doc["segments"] if s["tier_id"] not in ids] + imported["segments"]
    return {**doc, "tiers": tiers, "segments": segs}
