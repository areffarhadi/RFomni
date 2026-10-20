"""Placeholder engine used until a model is connected.

Segment boundaries are real (energy-based VAD on the audio), but every value is a
placeholder (lorem-ipsum text, random labels, random confidence). All output is
stored with source="mock" so it is easy to spot and to replace later.
"""
import hashlib

import numpy as np

from ..config import SR
from .base import AnnotateContext, Engine

LOREM = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut "
         "labore et dolore magna aliqua enim ad minim veniam quis nostrud exercitation ullamco laboris "
         "nisi aliquip ex ea commodo consequat duis aute irure in reprehenderit voluptate velit esse "
         "cillum fugiat nulla pariatur excepteur sint occaecat cupidatat non proident sunt culpa qui "
         "officia deserunt mollit anim id est laborum").split()

CAPTIONS = {
    "audio_caption": "(mock) A person speaks in a quiet room while faint background noise is audible.",
    "video_caption": "(mock) A person sits facing the camera and talks; the background stays mostly static.",
    "image_caption": "(mock) A person is shown in an indoor setting looking towards the camera.",
    "detailed_caption": "(mock) The image shows a person in the foreground, softly lit, with a plain background. "
                        "Colors are muted and no text is visible.",
    "face_caption": "(mock) Adult with a neutral expression, gaze directed at the camera, head slightly tilted.",
    "speech_caption": "(mock) Calm, mid-pitched voice at a moderate pace with steady intensity.",
    "dense_caption": "(mock) The person continues talking and gestures with one hand.",
    "action": "(mock) talking to the camera",
    "accent": "(mock) neutral accent",
}

PHONE_MAP = {"a": "a", "b": "b", "c": "k", "d": "d", "e": "ɛ", "f": "f", "g": "ɡ", "h": "h", "i": "ɪ",
             "j": "dʒ", "k": "k", "l": "l", "m": "m", "n": "n", "o": "ɔ", "p": "p", "q": "k", "r": "ɹ",
             "s": "s", "t": "t", "u": "ʊ", "v": "v", "w": "w", "x": "ks", "y": "j", "z": "z"}


def _seed(*parts) -> int:
    return int.from_bytes(hashlib.sha1("|".join(parts).encode()).digest()[:8], "little")


def _conf(rng) -> float:
    return round(float(1.0 - rng.beta(1.6, 5.5)), 3)


def energy_vad(x: np.ndarray, max_len: float = 12.0) -> list:
    """Simple energy VAD on 10 ms frames. Returns [(start, end)] in seconds."""
    hop = SR // 100
    n = len(x) // hop
    if n < 5:
        return []
    X = x[: n * hop].reshape(n, hop)
    db = 10 * np.log10(np.einsum("ij,ij->i", X, X) / hop + 1e-10)
    db = np.convolve(db, np.ones(5) / 5, mode="same")
    lo, hi = np.percentile(db, 10), np.percentile(db, 97)
    if hi < -65:
        return []
    if hi - lo < 8:
        speech = np.ones(n, bool)
    else:
        speech = db > lo + 0.35 * (hi - lo)
    # runs of True
    d = np.diff(np.concatenate([[0], speech.astype(np.int8), [0]]))
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    segs = []
    for s, e in zip(starts, ends):
        if segs and s - segs[-1][1] < 30:  # merge gaps < 300 ms
            segs[-1][1] = e
        else:
            segs.append([s, e])
    out = []
    for s, e in segs:
        if e - s < 25:  # drop < 250 ms
            continue
        s, e = max(0, s - 5), min(n, e + 5)
        # split long regions at the quietest frame near each cut point
        pieces = [s]
        L = max_len * 100
        while e - pieces[-1] > L:
            a = int(pieces[-1] + L * 0.6)
            b = int(min(e - 50, pieces[-1] + L))
            cut = a + int(np.argmin(db[a:b])) if b > a else int(pieces[-1] + L)
            pieces.append(cut)
        pieces.append(e)
        out.extend((pieces[i] / 100, pieces[i + 1] / 100) for i in range(len(pieces) - 1))
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a >= 0.2]


class MockEngine(Engine):
    """No model connected: generates placeholder annotations (real VAD timing, fake values)."""
    name = "mock"
    title = "Mock engine (no model connected)"
    source = "mock"
    connected = False

    # ---------------- spans ----------------
    def _speech(self, ctx: AnnotateContext) -> list:
        if "speech" not in ctx.cache:
            x = ctx.audio
            spans = energy_vad(x) if len(x) else []
            if not spans and ctx.duration:
                spans = self._windows(ctx.duration, 5.0)
            ctx.cache["speech"] = spans
        return ctx.cache["speech"]

    @staticmethod
    def _windows(dur: float, win: float) -> list:
        out, t = [], 0.0
        while t < dur - 1e-3:
            out.append((round(t, 3), round(min(dur, t + win), 3)))
            t += win
        return out

    @staticmethod
    def _events(dur: float, rng, contiguous=False) -> list:
        out, t = [], (0.0 if contiguous else float(rng.uniform(0, 3)))
        while t < dur - 0.3:
            d = float(rng.uniform(2, 10)) if contiguous else float(rng.uniform(0.5, 4))
            out.append((round(t, 3), round(min(dur, t + d), 3)))
            t += d + (0 if contiguous else float(rng.uniform(1, 10)))
        return out

    # ---------------- values ----------------
    @staticmethod
    def _lorem(rng, n) -> str:
        words = list(rng.choice(LOREM, size=max(1, n)))
        words[0] = words[0].capitalize()
        return " ".join(words) + "."

    def _label(self, task, rng, i=0, feature=None) -> str:
        labels = task.get("labels") or []
        tid = task["id"]
        if tid == "diarization" or (not labels and task["output"] == "label"):
            return f"SPEAKER_{feature if feature is not None else i % 2}"
        if tid == "lid":
            return "en" if rng.random() < 0.85 else str(rng.choice(labels))
        if len(labels) == 1:
            return labels[0]
        w = np.full(len(labels), 0.5 / max(1, len(labels) - 1))
        w[0] = 0.5
        return str(rng.choice(labels, p=w / w.sum()))

    def _value(self, task, rng, start, end, i=0, feature=None):
        out = task["output"]
        if out == "label":
            return self._label(task, rng, i, feature)
        if out == "tags":
            labels = task.get("labels") or ["tag"]
            if task["id"] == "ner":
                k = int(rng.integers(0, 3))
                return [f"{' '.join(rng.choice(LOREM, 2)).title()} [{rng.choice(labels)}]" for _ in range(k)]
            k = int(rng.integers(1, min(3, len(labels)) + 1))
            return [str(t) for t in rng.choice(labels, size=k, replace=False)]
        if out == "score":
            lo, hi = task.get("score_min", 1), task.get("score_max", 5)
            return f"{rng.uniform(lo, hi):.1f}"
        # text
        if task["id"] in CAPTIONS:
            return CAPTIONS[task["id"]]
        dur = (end - start) if (start is not None and end is not None) else 4.0
        n = int(min(40, max(1, round(dur * rng.uniform(2.0, 3.0)))))
        if task["id"] == "asr":
            return self._lorem(rng, n)
        if task["id"] == "translation":
            return "(mock EN) " + self._lorem(rng, n)
        if task["id"] == "ocr":
            return "" if rng.random() < 0.6 else "(mock) ON-SCREEN TEXT"
        return f"(mock) {task['name']}: " + self._lorem(rng, min(n, 14))

    # ---------------- diarization feature ----------------
    def _speaker_ids(self, ctx, spans, rng) -> list:
        x = ctx.audio
        if not len(x) or not spans:
            return [0] * len(spans)
        feats = []
        for a, b in spans:
            seg = x[int(a * SR): int(b * SR)]
            feats.append(float(np.mean(np.abs(np.diff(np.sign(seg))))) if len(seg) > 1 else 0.0)
        k = int(rng.integers(1, 4)) if len(spans) > 3 else 1
        if k == 1:
            return [0] * len(spans)
        qs = np.quantile(feats, np.linspace(0, 1, k + 1)[1:-1])
        return [int(np.searchsorted(qs, f)) for f in feats]

    # ---------------- main ----------------
    def annotate(self, task: dict, ctx: AnnotateContext) -> list:
        rng = np.random.default_rng(_seed(ctx.media["id"], task["id"]))
        mod = ctx.media["modality"]
        dur = ctx.duration
        gran = "file" if mod == "image" else task.get("granularity", "segment")

        if gran == "file":
            s, e = (None, None) if mod == "image" else (0.0, round(dur, 3))
            return [{"start": s, "end": e, "value": self._value(task, rng, s, e), "confidence": _conf(rng)}]

        if gran in ("word", "phone"):
            return self._sub_units(task, ctx, rng, gran)

        if gran == "segment":
            spans = self._speech(ctx)
        elif gran == "window":
            spans = self._windows(dur, float(task.get("window_sec", 5)))
        else:  # event
            spans = self._events(dur, rng, contiguous=task["id"] == "shots")

        feats = self._speaker_ids(ctx, spans, rng) if task["id"] == "diarization" else [None] * len(spans)
        return [{"start": a, "end": b, "value": self._value(task, rng, a, b, i, f), "confidence": _conf(rng)}
                for i, ((a, b), f) in enumerate(zip(spans, feats))]

    def _sub_units(self, task, ctx, rng, gran) -> list:
        """Words: split the parent transcript proportionally. Phones: split each word into letters."""
        if gran == "word":
            parents = [s for s in ctx.tier_segments(task.get("parent") or "asr")
                       if s.get("start") is not None and isinstance(s.get("value"), str)]
            if not parents:
                parents = [{"start": a, "end": b, "value": self._lorem(rng, int(max(1, (b - a) * 2.5)))}
                           for a, b in self._speech(ctx)]
            units = [(p["start"], p["end"], p["value"].split()) for p in parents]
        else:
            parents = [s for s in ctx.tier_segments(task.get("parent") or "forced_alignment")
                       if s.get("start") is not None and isinstance(s.get("value"), str)]
            if not parents:
                parents = self._sub_units({**task, "parent": "asr"}, ctx, rng, "word")
            units = []
            for p in parents:
                letters = [c for c in p["value"].lower() if c.isalpha()]
                units.append((p["start"], p["end"], [PHONE_MAP.get(c, c) for c in letters]))
        out = []
        for a, b, toks in units:
            if not toks or b <= a:
                continue
            w = np.array([len(t) + (2 if gran == "word" else 1) for t in toks], float)
            edges = a + (b - a) * np.concatenate([[0], np.cumsum(w) / w.sum()])
            for i, tok in enumerate(toks):
                out.append({"start": round(float(edges[i]), 3), "end": round(float(edges[i + 1]), 3),
                            "value": tok, "confidence": _conf(rng)})
        return out
