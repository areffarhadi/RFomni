"""Project statistics: dataset make-up, per-task label distributions, review progress,
how often reviewers changed the model's output, and speaker / overlap figures."""
import csv
import io
import re
from collections import Counter, defaultdict

from . import speakers as spk
from . import tasks as task_reg
from .util import now_iso

LEN_BUCKETS = [(0, 5, "< 5 s"), (5, 15, "5–15 s"), (15, 30, "15–30 s"), (30, 60, "30–60 s"),
               (60, 300, "1–5 min"), (300, 900, "5–15 min"), (900, float("inf"), "> 15 min")]
WORD_RE = re.compile(r"\w+", re.UNICODE)


def _dur(s):
    a, b = s.get("start"), s.get("end")
    return (b - a) if a is not None and b is not None and b > a else 0.0


def _values(v):
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    return [str(v)] if v not in (None, "") else []


class _TaskAcc:
    def __init__(self, key, tier, task):
        self.key = key
        self.task = task
        self.tier = tier
        self.output = (task or tier).get("output", "text")
        self.files = set()
        self.n = self.reviewed = self.low = 0
        self.conf_n, self.conf_sum = 0, 0.0
        self.conf_hist = [0] * 10
        self.sources = Counter()
        self.duration = 0.0
        self.lab_n, self.lab_d, self.lab_f = Counter(), Counter(), defaultdict(set)
        self.empty = 0
        self.words = self.chars = 0
        self.vocab = set()
        self.scores = []
        self.people = self.kp_vis = self.kp_all = 0
        self.per_image = Counter()
        self.accepted = self.changed = self.time_changed = self.geom_changed = self.added = 0
        self.shift_sum, self.shift_n = 0.0, 0
        self.confusion = Counter()
        self.editors = Counter()

    def add(self, s, mid, low):
        self.n += 1
        self.files.add(mid)
        if s.get("reviewed"):
            self.reviewed += 1
        c = s.get("confidence")
        if c is not None:
            self.conf_n += 1
            self.conf_sum += c
            self.conf_hist[min(9, max(0, int(c * 10)))] += 1
            if not s.get("reviewed") and c < low:
                self.low += 1
        src = (s.get("source") or "?").split("+")[0]
        self.sources[src] += 1
        d = _dur(s)
        self.duration += d
        vals = _values(s.get("value"))
        if self.output in ("label", "tags", "boxes", "keypoints"):
            if not vals:
                self.lab_n["(no label)"] += 1
                self.lab_d["(no label)"] += d
                self.lab_f["(no label)"].add(mid)
            for v in vals:
                self.lab_n[v] += 1
                self.lab_d[v] += d
                self.lab_f[v].add(mid)
        elif self.output == "text":
            text = " ".join(vals)
            if not text.strip():
                self.empty += 1
            w = WORD_RE.findall(text.lower())
            self.words += len(w)
            self.chars += len(text)
            if len(self.vocab) < 200000:
                self.vocab.update(w)
        elif self.output == "score":
            try:
                self.scores.append(float(vals[0]))
            except (IndexError, ValueError):
                self.empty += 1
        g = s.get("geom")
        if g:
            self.per_image[mid] += 1
            if g.get("type") == "keypoints":
                self.people += 1
                pts = g.get("points") or []
                self.kp_all += len(pts)
                self.kp_vis += sum(1 for p in pts if len(p) > 2 and p[2])
        # human checks of model output
        meta = s.get("meta") or {}
        if meta.get("edited_by"):
            self.editors[meta["edited_by"]] += 1
        if "model_source" in meta:
            mv = meta.get("model_value")
            if mv is not None and _values(mv) != vals:
                self.changed += 1
                if self.output == "label" and len(self.confusion) < 2000:
                    self.confusion[(" ".join(_values(mv)) or "(no label)", " ".join(vals) or "(no label)")] += 1
            elif "model_geom" in meta:
                self.geom_changed += 1
            ms, me = meta.get("model_start"), meta.get("model_end")
            if ms is not None and s.get("start") is not None:
                shift = abs(ms - s["start"]) + abs((me or 0) - (s.get("end") or 0))
                if shift > 0.005:
                    self.time_changed += 1
                    self.shift_sum += shift / 2
                    self.shift_n += 1
        elif src == "human":
            self.added += 1
        elif s.get("reviewed"):
            self.accepted += 1

    def result(self, applicable):
        t = self.task or {}
        out = {
            "id": self.key, "name": t.get("name") or self.tier.get("name"), "title": t.get("title") or self.tier.get("title") or self.tier.get("name"),
            "color": t.get("color") or self.tier.get("color"), "output": self.output,
            "granularity": t.get("granularity") or self.tier.get("granularity"), "category": t.get("category"),
            "files": len(self.files), "applicable": applicable, "segments": self.n, "reviewed": self.reviewed,
            "low": self.low, "duration": round(self.duration, 2),
            "mean_conf": round(self.conf_sum / self.conf_n, 3) if self.conf_n else None, "conf_hist": self.conf_hist,
            "sources": dict(self.sources),
            "checks": {"accepted": self.accepted, "changed": self.changed, "time_changed": self.time_changed,
                       "geom_changed": self.geom_changed, "added": self.added,
                       "mean_shift_ms": round(1000 * self.shift_sum / self.shift_n) if self.shift_n else None,
                       "agreement": round(self.accepted / (self.accepted + self.changed), 3) if self.accepted + self.changed else None,
                       "confusions": [{"model": a, "human": b, "n": n} for (a, b), n in self.confusion.most_common(8)],
                       "editors": dict(self.editors)},
        }
        if self.output in ("label", "tags", "boxes", "keypoints"):
            classes = list(t.get("labels") or [])
            seen = list(self.lab_n)
            order = sorted(seen, key=lambda v: (-self.lab_n[v], v))
            missing = [c for c in classes if c not in self.lab_n] if self.output != "keypoints" else []
            out["labels"] = [{"value": v, "count": self.lab_n[v], "duration": round(self.lab_d[v], 2), "files": len(self.lab_f[v]),
                              "in_classes": v in classes} for v in order[:60]] + \
                            [{"value": v, "count": 0, "duration": 0, "files": 0, "in_classes": True} for v in missing]
            out["n_labels"] = len(seen)
        if self.output == "text":
            nonempty = max(1, self.n - self.empty)
            out["text"] = {"words": self.words, "chars": self.chars, "vocab": len(self.vocab), "empty": self.empty,
                           "avg_words": round(self.words / nonempty, 1),
                           "words_per_min": round(self.words / (self.duration / 60), 1) if self.duration > 30 else None}
        if self.output == "score" and self.scores:
            lo, hi = float(t.get("score_min", 1)), float(t.get("score_max", 5))
            bins = [0] * 8
            for v in self.scores:
                k = int((v - lo) / max(1e-9, hi - lo) * 8)
                bins[min(7, max(0, k))] += 1
            out["score"] = {"mean": round(sum(self.scores) / len(self.scores), 2), "min": min(self.scores),
                            "max": max(self.scores), "lo": lo, "hi": hi, "hist": bins}
        if self.per_image:
            out["spatial"] = {"images": len(self.per_image), "per_image": round(sum(self.per_image.values()) / len(self.per_image), 2),
                              "max_per_image": max(self.per_image.values())}
            if self.people:
                out["spatial"].update({"people": self.people, "visible_kp": round(self.kp_vis / max(1, self.kp_all), 3)})
        return out


def project_stats(store, pid: str, only_tasks=None) -> dict:
    proj = store.get_project(pid)
    low = proj.get("settings", {}).get("low_confidence", 0.6)
    reg = task_reg.tasks_by_id()
    allowed = set(only_tasks) if only_tasks else None
    items = store.list_media(pid)

    by_mod, dur_mod, status = Counter(), Counter(), Counter()
    length = [0] * len(LEN_BUCKETS)
    size = 0
    accs: dict[str, _TaskAcc] = {}
    sp = {"files": 0, "speech": 0.0, "pure": 0.0, "overlap": 0.0, "turns": 0, "n_speakers": Counter(),
          "pairs_n": Counter(), "pairs_d": Counter(), "spk_pure": Counter(), "spk_over": Counter(), "spk_files": defaultdict(set),
          "per_file": [], "derived": False}
    for m in items:
        mod = m.get("modality", "?")
        by_mod[mod] += 1
        status[m.get("status", "new")] += 1
        size += m.get("size") or 0
        d = m.get("duration") or 0
        if mod in ("audio", "video"):
            dur_mod[mod] += d
            for i, (a, b, _) in enumerate(LEN_BUCKETS):
                if a <= d < b:
                    length[i] += 1
                    break
        if not (m.get("summary") or {}).get("n_segments"):
            continue
        doc = store.load_doc(pid, m["id"])
        tiers = {t["id"]: t for t in doc.get("tiers", [])}
        for s in doc.get("segments", []):
            tier = tiers.get(s.get("tier_id"))
            if not tier:
                continue
            key = tier.get("task_id") or tier["id"]
            if allowed is not None and key not in allowed:
                continue
            acc = accs.get(key)
            if acc is None:
                acc = accs[key] = _TaskAcc(key, tier, reg.get(key))
            acc.add(s, m["id"], low)
        if allowed is not None and not allowed & {spk.DIAR, spk.PURE, spk.OVERLAP}:
            continue
        r = spk.regions_from_doc(doc)
        if not r:
            continue
        sp["files"] += 1
        sp["derived"] |= r["derived"]["overlap"]
        sp["turns"] += len(r["turns"])
        speech = spk.union_duration(r["turns"]) if r["turns"] else spk.union_duration(r["pure"] + r["overlap"])
        pure = sum(_dur(x) for x in r["pure"])
        over = spk.union_duration(r["overlap"]) if r["overlap"] else 0.0
        names = {x["value"] for x in r["turns"] if isinstance(x.get("value"), str)} or \
                {n for x in r["pure"] + r["overlap"] for n in x["speakers"]}
        sp["speech"] += speech
        sp["pure"] += pure
        sp["overlap"] += over
        sp["n_speakers"][len(names)] += 1
        for x in r["pure"]:
            sp["spk_pure"][x["value"]] += _dur(x)
            sp["spk_files"][x["value"]].add(m["id"])
        for x in r["overlap"]:
            sp["pairs_n"][x["value"]] += 1
            sp["pairs_d"][x["value"]] += _dur(x)
            for n in x["speakers"]:
                sp["spk_over"][n] += _dur(x)
                sp["spk_files"][n].add(m["id"])
        sp["per_file"].append({"media_id": m["id"], "name": m["name"], "modality": mod, "speakers": len(names),
                               "speech": round(speech, 2), "pure": round(pure, 2), "overlap": round(over, 2),
                               "ratio": round(over / speech, 4) if speech else 0, "overlaps": len(r["overlap"])})

    applicable = {}
    for key, acc in accs.items():
        mods = (acc.task or {}).get("modalities") or ["audio", "video", "image"]
        applicable[key] = sum(by_mod[x] for x in mods)
    rank = {k: i for i, k in enumerate(proj.get("tier_order") or proj.get("tasks") or [])}
    task_out = sorted((a.result(applicable[k]) for k, a in accs.items()), key=lambda t: (rank.get(t["id"], 999), t["title"] or ""))
    # project tasks that have no output yet still get a row
    for tid in proj.get("tasks", []):
        if tid in accs or tid not in reg or (allowed is not None and tid not in allowed):
            continue
        t = reg[tid]
        task_out.append({"id": tid, "name": t["name"], "title": t["title"], "color": t.get("color"), "output": t["output"],
                         "granularity": t.get("granularity"), "category": t.get("category"), "files": 0,
                         "applicable": sum(by_mod[x] for x in t.get("modalities", [])), "segments": 0, "reviewed": 0,
                         "low": 0, "duration": 0, "mean_conf": None, "conf_hist": [0] * 10, "sources": {}, "checks": None})

    segs = sum(t["segments"] for t in task_out)
    reviewed = sum(t["reviewed"] for t in task_out)
    sources = Counter()
    for t in task_out:
        sources.update(t["sources"])
    editors = Counter()
    for t in task_out:
        if t.get("checks"):
            editors.update(t["checks"]["editors"])

    speakers = None
    if sp["files"]:
        names = set(sp["spk_pure"]) | set(sp["spk_over"])
        speakers = {
            "files": sp["files"], "turns": sp["turns"], "speech": round(sp["speech"], 2), "pure": round(sp["pure"], 2),
            "overlap": round(sp["overlap"], 2), "overlap_ratio": round(sp["overlap"] / sp["speech"], 4) if sp["speech"] else 0,
            "derived": sp["derived"],
            "n_speakers": [{"speakers": k, "files": v} for k, v in sorted(sp["n_speakers"].items())],
            "pairs": [{"pair": p, "count": sp["pairs_n"][p], "duration": round(sp["pairs_d"][p], 2)}
                      for p in sorted(sp["pairs_d"], key=lambda p: -sp["pairs_d"][p])[:20]],
            "per_speaker": [{"speaker": n, "pure": round(sp["spk_pure"][n], 2), "overlap": round(sp["spk_over"][n], 2),
                             "files": len(sp["spk_files"][n])} for n in sorted(names, key=spk.natural_key)][:40],
            "per_file": sorted(sp["per_file"], key=lambda f: -f["ratio"])[:50],
        }

    out = {
        "generated_at": now_iso(), "project": {"id": proj["id"], "name": proj["name"]}, "low_confidence": low,
        "dataset": {"files": len(items), "by_modality": dict(by_mod), "duration": round(sum(dur_mod.values()), 2),
                    "words": sum(m.get("words") or 0 for m in items if m.get("modality") == "text"),
                    "duration_by_modality": {k: round(v, 2) for k, v in dur_mod.items()}, "size": size,
                    "status": dict(status),
                    "length_hist": [{"label": lab, "files": n} for (_, _, lab), n in zip(LEN_BUCKETS, length)]},
        "review": {"segments": segs, "reviewed": reviewed, "sources": dict(sources), "editors": dict(editors),
                   "low": sum(t["low"] for t in task_out)},
        "tasks": task_out, "speakers": speakers,
    }
    out["warnings"] = _warnings(out)
    return out


def _warnings(st) -> list:
    w = []
    for t in st["tasks"]:
        labs = [l for l in t.get("labels") or [] if l["count"]]
        if t["output"] in ("label", "boxes") and t["segments"] >= 20 and labs:
            top = labs[0]
            share = top["count"] / t["segments"]
            if share >= 0.7 and len(t.get("labels") or []) > 1:
                w.append({"level": "warn", "task": t["id"], "text": f"{t['title']}: '{top['value']}' is {share:.0%} of all segments (imbalanced classes)."})
            missing = [l["value"] for l in t.get("labels") or [] if not l["count"] and l.get("in_classes")]
            if missing and len(t.get("labels") or []) <= 12:  # long class lists (e.g. 20 languages) are not expected to be complete
                w.append({"level": "info", "task": t["id"], "text": f"{t['title']}: no examples yet of {', '.join(repr(m) for m in missing[:6])}."})
            rare = [l["value"] for l in labs if l["count"] < 5]
            if t["segments"] >= 100 and rare:
                w.append({"level": "info", "task": t["id"], "text": f"{t['title']}: fewer than 5 examples of {', '.join(repr(r) for r in rare[:6])}."})
        if t["segments"] >= 20 and t["low"] / t["segments"] > 0.3:
            w.append({"level": "warn", "task": t["id"], "text": f"{t['title']}: {t['low'] / t['segments']:.0%} of the segments have low confidence and are not reviewed."})
        if t["segments"] == 0 and t["applicable"]:
            w.append({"level": "info", "task": t["id"], "text": f"{t['title']}: not run yet."})
    mock = st["review"]["sources"].get("mock", 0)
    if mock:
        w.append({"level": "info", "task": None, "text": f"{mock} segments are placeholders from the mock engine (no model connected)."})
    new = st["dataset"]["status"].get("new", 0)
    if new:
        w.append({"level": "info", "task": None, "text": f"{new} file{'s' if new > 1 else ''} without any annotation."})
    return w


def stats_csv(st) -> str:
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["section", "task", "item", "count", "duration_s", "files", "share"])
    for t in st["tasks"]:
        wr.writerow(["task", t["id"], "segments", t["segments"], t["duration"], t["files"], ""])
        wr.writerow(["task", t["id"], "reviewed", t["reviewed"], "", "", round(t["reviewed"] / t["segments"], 4) if t["segments"] else ""])
        for l in t.get("labels") or []:
            wr.writerow(["label", t["id"], l["value"], l["count"], l["duration"], l["files"],
                         round(l["count"] / t["segments"], 4) if t["segments"] else ""])
    sp = st.get("speakers")
    if sp:
        for k in ("speech", "pure", "overlap"):
            wr.writerow(["speakers", "", k, "", sp[k], sp["files"], round(sp[k] / sp["speech"], 4) if sp["speech"] else ""])
        for p in sp["pairs"]:
            wr.writerow(["overlap_pair", "", p["pair"], p["count"], p["duration"], "", ""])
        for f in sp["per_file"]:
            wr.writerow(["overlap_file", "", f["name"], f["overlaps"], f["overlap"], "", f["ratio"]])
    return buf.getvalue()
