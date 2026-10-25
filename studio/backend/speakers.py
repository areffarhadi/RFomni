"""Speaker regions: from speaker turns (diarization, turns may overlap) to single-speaker ("pure")
speech per speaker and overlapped speech per speaker pair. The editor has the same logic in
frontend/js/editor/speakers.js (used by "Rebuild from speaker turns")."""
import re

DIAR, PURE, OVERLAP = "diarization", "pure_speech", "overlap"


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def pair_label(speakers) -> str:
    return " + ".join(sorted(set(speakers), key=natural_key))


def split_pair(label) -> list:
    return [x.strip() for x in str(label or "").split("+") if x.strip()]


def _min(a, b):
    return b if a is None else a if b is None else min(a, b)


def speaker_regions(turns, min_dur: float = 0.02):
    """turns: dicts with start, end, value (speaker), confidence. Returns (pure, overlap):
    lists of {start, end, value, speakers, confidence}; pure.value is the speaker,
    overlap.value the sorted speaker set joined with ' + '."""
    tl = []
    for t in turns:
        a, b, spk = t.get("start"), t.get("end"), t.get("value")
        if a is None or b is None or b <= a or not spk or not isinstance(spk, str):
            continue
        tl.append((float(a), float(b), spk, t.get("confidence")))
    events = sorted([(a, 1, i) for i, (a, _, _, _) in enumerate(tl)] + [(b, 0, i) for i, (_, b, _, _) in enumerate(tl)])
    active, runs, prev = {}, [], None
    for t, kind, i in events:  # at equal times ends (0) come before starts (1)
        if prev is not None and t > prev and active:
            spk = frozenset(s for s, _ in active.values())
            conf = None
            for _, c in active.values():
                conf = _min(conf, c)
            if runs and runs[-1][2] == spk and abs(runs[-1][1] - prev) < 1e-9:
                a0, _, _, c0 = runs[-1]
                runs[-1] = (a0, t, spk, _min(c0, conf))
            else:
                runs.append((prev, t, spk, conf))
        if kind == 1:
            active[i] = (tl[i][2], tl[i][3])
        else:
            active.pop(i, None)
        prev = t
    pure, over = [], []
    for a, b, spk, c in runs:
        if b - a < min_dur:
            continue
        rec = {"start": round(a, 3), "end": round(b, 3), "speakers": sorted(spk, key=natural_key),
               "confidence": None if c is None else round(float(c), 3)}
        if len(spk) == 1:
            pure.append({**rec, "value": rec["speakers"][0]})
        else:
            over.append({**rec, "value": pair_label(spk)})
    return pure, over


def _tier_segs(doc, task_id):
    ids = {t["id"] for t in doc.get("tiers", []) if t.get("task_id") == task_id or t["id"] == task_id}
    return sorted((s for s in doc.get("segments", []) if s.get("tier_id") in ids and s.get("start") is not None),
                  key=lambda s: (s["start"], s["end"]))


def regions_from_doc(doc) -> dict | None:
    """Speaker turns, pure speech and overlaps of one file. Uses the Pure speech / Overlap tiers when the
    file has them, otherwise derives them from the diarization turns. None when there is no speaker info."""
    turns = _tier_segs(doc, DIAR)
    pure, over = _tier_segs(doc, PURE), _tier_segs(doc, OVERLAP)
    if not turns and not pure and not over:
        return None
    d_pure, d_over = speaker_regions(turns) if turns else ([], [])
    has_pure = any(t.get("task_id") == PURE or t["id"] == PURE for t in doc.get("tiers", []))
    has_over = any(t.get("task_id") == OVERLAP or t["id"] == OVERLAP for t in doc.get("tiers", []))
    return {
        "turns": turns,
        "pure": [{**s, "speakers": [s["value"]]} for s in pure] if has_pure else d_pure,
        "overlap": [{**s, "speakers": split_pair(s["value"])} for s in over] if has_over else d_over,
        "derived": {"pure": not has_pure, "overlap": not has_over},
    }


def union_duration(segs) -> float:
    total, cur_a, cur_b = 0.0, None, None
    for s in sorted(segs, key=lambda s: s["start"]):
        a, b = s["start"], s["end"]
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total
