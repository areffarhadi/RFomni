"""Generate pseudo media + pseudo labels to preview the UI with realistic-looking content.

    .venv/bin/python scripts/make_pseudo.py

Creates workspace/pseudo_data/ (speech from the macOS `say` voices, generated videos and images)
and a project "Pseudo showcase" whose tiers are filled with plausible labels derived from the
scripts used to generate the media (so transcripts, speakers, languages, slide text and events
line up with what you hear and see). Labels are stored as source="model" with made-up confidences.
"""
import math
import re
import subprocess
import sys
import tempfile
import wave
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import config, tasks as task_reg  # noqa: E402
from backend.main import _new_item, store  # noqa: E402
from backend.speakers import speaker_regions  # noqa: E402
from backend.util import new_id  # noqa: E402

SR = 16000
OUT = config.WORKSPACE / "pseudo_data"
PID = "pseudo-showcase"
rng = np.random.default_rng(7)

VOICES = {  # voice -> (lang, gender)
    "Samantha": ("en", "female"), "Daniel": ("en", "male"), "Karen": ("en", "female"), "Rishi": ("en", "male"),
    "Fred": ("en", "male"), "Moira": ("en", "female"), "Anna": ("de", "female"), "Thomas": ("fr", "male"),
    "Mónica": ("es", "female"), "Alice": ("it", "female"), "Kyoko": ("ja", "female"),
}
RATE = {"neutral": 175, "happy": 195, "surprised": 200, "sad": 145, "angry": 205, "frustrated": 190}

FONT_DIR = Path("/System/Library/Fonts/Supplemental")


def font(size, bold=False):
    for name in (["Arial Bold.ttf"] if bold else []) + ["Arial.ttf", "Helvetica.ttc"]:
        for d in (FONT_DIR, Path("/System/Library/Fonts"), Path("/Library/Fonts")):
            if (d / name).exists():
                return ImageFont.truetype(str(d / name), size)
    return ImageFont.load_default(size=size)


def conf(mu=0.9, sd=0.05, lo=0.05, hi=0.99):
    return round(float(np.clip(rng.normal(mu, sd), lo, hi)), 3)


# =============================================================================
# Audio building blocks
# =============================================================================
def tts(voice, text, emotion="neutral"):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "u.wav"
        subprocess.run(["say", "-v", voice, "-r", str(RATE.get(emotion, 175)), "--file-format=WAVE",
                        "--data-format=LEI16@16000", "-o", str(f), text], check=True)
        with wave.open(str(f)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    env = np.convolve(np.abs(x), np.ones(160) / 160, "same")
    idx = np.nonzero(env > 0.01)[0]
    if len(idx):
        x = x[max(0, idx[0] - 400): idx[-1] + 400]
    return x * (0.6 / max(1e-4, np.abs(x).max()))


def jingle(sec=3.0):
    t = np.arange(int(sec * SR)) / SR
    out = np.zeros_like(t)
    chords = [(261.6, 329.6, 392.0), (196.0, 246.9, 392.0), (220.0, 261.6, 329.6), (174.6, 220.0, 349.2)]
    step = sec / len(chords)
    for i, ch in enumerate(chords):
        m = (t >= i * step) & (t < (i + 1) * step)
        tt = t[m] - i * step
        envl = np.minimum(1, tt / 0.02) * np.exp(-tt * 1.6)
        for f in ch:
            out[m] += np.sin(2 * np.pi * f * tt) * envl * 0.12 + np.sin(2 * np.pi * 2 * f * tt) * envl * 0.03
    return out * np.minimum(1, (sec - t) / 0.3)


def applause(sec=3.0):
    n = int(sec * SR)
    out = np.zeros(n)
    t = np.arange(n) / SR
    dens = np.sin(np.pi * t / sec) ** 0.6
    for _ in range(int(55 * sec)):
        at = rng.uniform(0, sec)
        if rng.random() > dens[int(at * SR) - 1]:
            continue
        L = int(0.012 * SR)
        s = int(at * SR)
        clap = rng.normal(0, 1, L) * np.exp(-np.arange(L) / (0.002 * SR))
        out[s: s + L] += clap[: max(0, min(L, n - s))] * rng.uniform(0.05, 0.15)
    return out


def noise(n, level=0.02, color="pink"):
    w = rng.normal(0, 1, n)
    if color == "brown":
        b = np.cumsum(w)
        b -= np.convolve(b, np.ones(2000) / 2000, "same")
        w = b / (np.abs(b).max() + 1e-9)
    else:
        w = np.convolve(w, np.ones(6) / 6, "same")
        w /= np.abs(w).max()
    return w * level


def chirp():
    t = np.arange(int(0.14 * SR)) / SR
    f = 3500 + 1500 * np.sin(np.pi * t / t[-1])
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.sin(np.pi * t / t[-1]) * 0.12


class Track:
    """Builds a mono recording and remembers what was placed where (the 'ground truth')."""

    def __init__(self):
        self.parts = []
        self.t = 0.0
        self.utts = []
        self.events = []  # (start, end, tag)
        self.speakers = {}

    def gap(self, sec):
        self.parts.append(np.zeros(int(sec * SR)))
        self.t += sec

    def sound(self, x, tag):
        self.events.append((self.t, self.t + len(x) / SR, tag))
        self.parts.append(x)
        self.t += len(x) / SR

    def say(self, voice, text, emotion="neutral", translation=None, entities=(), tokens=None, **extra):
        x = tts(voice, text, emotion)
        if voice not in self.speakers:
            self.speakers[voice] = f"SPEAKER_{len(self.speakers)}"
        lang, gender = VOICES[voice]
        self.utts.append({"start": round(self.t, 3), "end": round(self.t + len(x) / SR, 3), "text": text, "voice": voice,
                          "speaker": self.speakers[voice], "lang": lang, "gender": gender, "emotion": emotion,
                          "translation": translation, "entities": list(entities), "tokens": tokens, **extra})
        self.parts.append(x)
        self.t += len(x) / SR

    def render(self, bg=None):
        y = np.concatenate(self.parts).astype(np.float32)
        if bg is not None:
            y = y + bg(len(y))
        return np.clip(y, -1, 1).astype(np.float32)


def write_wav(path, x):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


# =============================================================================
# Drawing
# =============================================================================
def face(d, cx, cy, r, skin="#f1c7a5", hair="#3b2a20", expr="neutral", mouth=0.0, blink=False, shirt="#3b82f6", bust=True):
    if bust:
        d.ellipse([cx - r * 1.5, cy + r * 0.75, cx + r * 1.5, cy + r * 2.9], fill=shirt)          # shoulders
        d.rectangle([cx - r * 0.28, cy + r * 0.6, cx + r * 0.28, cy + r * 1.0], fill=skin)          # neck
    d.ellipse([cx - r * 1.02, cy - r * 1.12, cx + r * 1.02, cy + r * 0.55], fill=hair)         # hair back
    d.ellipse([cx - r * 0.82, cy - r * 0.95, cx + r * 0.82, cy + r * 0.92], fill=skin)         # face
    d.chord([cx - r * 0.86, cy - r * 1.02, cx + r * 0.86, cy - r * 0.05], 180, 360, fill=hair)  # fringe
    ey, ex, er = cy - r * 0.12, r * 0.32, r * (0.11 if expr == "surprised" else 0.085)
    for sx in (-1, 1):
        x = cx + sx * ex
        if blink:
            d.line([x - er, ey, x + er, ey], fill="#2b1d16", width=max(2, int(r * 0.04)))
        else:
            d.ellipse([x - er * 1.3, ey - er, x + er * 1.3, ey + er], fill="white")
            d.ellipse([x - er * 0.6, ey - er * 0.6, x + er * 0.6, ey + er * 0.6], fill="#2b1d16")
        by = ey - r * (0.27 if expr == "surprised" else 0.2)
        tilt = {"sad": 0.06, "angry": -0.07, "frustrated": -0.05}.get(expr, 0) * r
        d.line([x - er * 1.6, by - sx * tilt, x + er * 1.6, by + sx * tilt], fill=hair, width=max(2, int(r * 0.06)))
    if expr == "happy":
        d.ellipse([cx - r * 0.62, cy + r * 0.12, cx - r * 0.38, cy + r * 0.3], fill="#f4a3a3")
        d.ellipse([cx + r * 0.38, cy + r * 0.12, cx + r * 0.62, cy + r * 0.3], fill="#f4a3a3")
    my = cy + r * 0.45
    if expr == "surprised" or mouth > 0.55:
        o = r * (0.12 + 0.12 * min(1, mouth))
        d.ellipse([cx - o * 0.8, my - o, cx + o * 0.8, my + o], fill="#7a2e2e")
    elif expr == "happy":
        h_ = r * (0.12 + 0.18 * mouth)
        d.chord([cx - r * 0.3, my - h_, cx + r * 0.3, my + h_], 0, 180, fill="#7a2e2e")
    elif expr == "sad":
        d.arc([cx - r * 0.24, my, cx + r * 0.24, my + r * 0.25], 200, 340, fill="#7a2e2e", width=max(2, int(r * 0.05)))
    else:
        h_ = r * 0.2 * mouth
        if h_ > 1:
            d.ellipse([cx - r * 0.2, my - h_, cx + r * 0.2, my + h_], fill="#7a2e2e")
        else:
            d.line([cx - r * 0.2, my, cx + r * 0.2, my], fill="#7a2e2e", width=max(2, int(r * 0.05)))


def gradient(w, h, top, bottom):
    a, b = np.array(Image.new("RGB", (1, 1), top)).reshape(3), np.array(Image.new("RGB", (1, 1), bottom)).reshape(3)
    t = np.linspace(0, 1, h)[:, None, None]
    return Image.fromarray(((1 - t) * a + t * b).repeat(w, axis=1).astype(np.uint8))


def street(w, h, car_x=None, t=0.0):
    img = gradient(w, h, "#7cc4f0", "#d9eefb")
    d = ImageDraw.Draw(img)
    s = w / 1280
    d.ellipse([1050 * s, 60 * s, 1150 * s, 160 * s], fill="#ffe27a")
    for cx, cy in ((200 + 15 * t, 120), (560 + 10 * t, 80), (900 + 12 * t, 150)):
        for dx, dy, rr in ((0, 0, 40), (40, 10, 34), (-40, 12, 30)):
            d.ellipse([(cx + dx - rr) * s, (cy + dy - rr) * s, (cx + dx + rr) * s, (cy + dy + rr) * s], fill="white")
    bx = 0
    cols = ["#9ca3af", "#b45309", "#64748b", "#a16207", "#475569", "#94a3b8"]
    for i, (bw, bh) in enumerate(((180, 330), (220, 420), (160, 280), (240, 380), (200, 460), (190, 300))):
        x0, y0 = bx * s, (560 - bh) * s
        d.rectangle([x0, y0, (bx + bw) * s, 560 * s], fill=cols[i])
        for wy in range(int(560 - bh + 20), 540, 40):
            for wx in range(bx + 18, bx + bw - 20, 36):
                d.rectangle([wx * s, wy * s, (wx + 18) * s, (wy + 22) * s], fill="#fde68a" if (wx + wy + i) % 3 else "#e5e7eb")
        bx += bw + 18
    d.rectangle([0, 560 * s, w, 600 * s], fill="#cbd5e1")
    d.rectangle([0, 600 * s, w, h], fill="#374151")
    for x in range(0, 1280, 120):
        d.rectangle([x * s, 700 * s, (x + 60) * s, 708 * s], fill="#f8fafc")
    d.rectangle([1120 * s, 430 * s, 1128 * s, 560 * s], fill="#1f2937")
    d.rectangle([1060 * s, 400 * s, 1190 * s, 440 * s], fill="#15803d")
    d.text((1070 * s, 405 * s), "MAIN ST", fill="white", font=font(int(26 * s), True))
    d.rectangle([90 * s, 460 * s, 104 * s, 570 * s], fill="#78350f")
    for dx, dy, rr in ((0, -40, 50), (-35, -10, 40), (35, -10, 40)):
        d.ellipse([(97 + dx - rr) * s, (430 + dy - rr) * s, (97 + dx + rr) * s, (430 + dy + rr) * s], fill="#16a34a")
    px = 760 * s
    d.ellipse([px - 14 * s, 470 * s, px + 14 * s, 498 * s], fill="#f1c7a5")
    d.rectangle([px - 14 * s, 498 * s, px + 14 * s, 540 * s], fill="#ef4444")
    d.line([px - 8 * s, 540 * s, px - 10 * s, 575 * s], fill="#1f2937", width=int(6 * s))
    d.line([px + 8 * s, 540 * s, px + 10 * s, 575 * s], fill="#1f2937", width=int(6 * s))
    if car_x is not None:
        cx = car_x * s
        d.rounded_rectangle([cx, 620 * s, cx + 260 * s, 690 * s], radius=int(18 * s), fill="#dc2626")
        d.rounded_rectangle([cx + 50 * s, 575 * s, cx + 190 * s, 630 * s], radius=int(16 * s), fill="#b91c1c")
        d.rectangle([cx + 62 * s, 585 * s, cx + 118 * s, 622 * s], fill="#bfdbfe")
        d.rectangle([cx + 124 * s, 585 * s, cx + 178 * s, 622 * s], fill="#bfdbfe")
        for wx in (55, 205):
            d.ellipse([cx + (wx - 26) * s, 665 * s, cx + (wx + 26) * s, 717 * s], fill="#111827")
            d.ellipse([cx + (wx - 11) * s, 680 * s, cx + (wx + 11) * s, 702 * s], fill="#9ca3af")
    return img


# =============================================================================
# Video writer (H.264 + AAC, plays in the browser)
# =============================================================================
def write_video(path, draw_frame, audio, fps=25, size=(640, 360)):
    path.parent.mkdir(parents=True, exist_ok=True)
    dur = len(audio) / SR
    n = int(dur * fps)
    c = av.open(str(path), "w")
    vs = c.add_stream("libx264", rate=fps, options={"crf": "24", "preset": "veryfast"})
    vs.width, vs.height, vs.pix_fmt = size[0], size[1], "yuv420p"
    ast = c.add_stream("aac", rate=SR, layout="mono")
    pos = 0
    for i in range(n):
        frame = av.VideoFrame.from_image(draw_frame(i / fps).convert("RGB"))
        frame.pts, frame.time_base = i, Fraction(1, fps)
        for p in vs.encode(frame):
            c.mux(p)
        while pos < min(len(audio), (i + 1) / fps * SR):
            chunk = audio[pos: pos + 1024]
            if len(chunk) < 1024:
                chunk = np.pad(chunk, (0, 1024 - len(chunk)))
            af = av.AudioFrame.from_ndarray(chunk.reshape(1, -1).astype(np.float32), format="fltp", layout="mono")
            af.sample_rate, af.pts, af.time_base = SR, pos, Fraction(1, SR)
            for p in ast.encode(af):
                c.mux(p)
            pos += 1024
    for p in vs.encode():
        c.mux(p)
    for p in ast.encode():
        c.mux(p)
    c.close()


def envelope(audio, fps=25):
    hop = SR // fps
    n = len(audio) // hop
    e = np.sqrt((audio[: n * hop].reshape(n, hop) ** 2).mean(1))
    return np.clip(e / (e.max() + 1e-9) * 1.8, 0, 1)


def active(utts, t):
    for u in utts:
        if u["start"] <= t < u["end"]:
            return u
    return None


# =============================================================================
# Pseudo labels
# =============================================================================
DIG = [("tion", "ʃən"), ("th", "θ"), ("sh", "ʃ"), ("ch", "tʃ"), ("ng", "ŋ"), ("ee", "iː"), ("oo", "uː"), ("ou", "aʊ"),
       ("ai", "eɪ"), ("ay", "eɪ"), ("ea", "iː"), ("qu", "kw"), ("ck", "k"), ("ph", "f"), ("wh", "w")]
ONE = {"a": "æ", "e": "ɛ", "i": "ɪ", "o": "ɒ", "u": "ʌ", "y": "i", "c": "k", "j": "dʒ", "x": "ks", "r": "ɹ", "g": "ɡ",
       "é": "e", "è": "ɛ", "à": "a", "ü": "y", "ö": "ø", "ä": "ɛ", "ó": "o", "í": "i", "á": "a", "ú": "u", "ñ": "ɲ"}


def g2p(word):
    w, out, i = re.sub(r"[^a-zà-ÿñ]", "", word.lower()), [], 0
    while i < len(w):
        for k, v in DIG:
            if w.startswith(k, i):
                out.append(v)
                i += len(k)
                break
        else:
            if not (i and w[i] == w[i - 1] and w[i] not in "aeiou"):
                out.append(ONE.get(w[i], w[i]))
            i += 1
    return out


EMO_DESC = {"neutral": "calm, even tone", "happy": "warm, upbeat tone with a smile", "surprised": "raised pitch, sounds surprised",
            "frustrated": "tense and impatient", "angry": "loud, sharp and irritated", "sad": "soft, low energy"}
PACE = {"neutral": "moderate", "happy": "slightly fast", "surprised": "quick", "frustrated": "fast", "angry": "fast", "sad": "slow"}


class Ann:
    def __init__(self):
        self.by_task = task_reg.tasks_by_id()
        self.tiers, self.segs = {}, []

    def add(self, task_id, start, end, value, c=None, source="model", reviewed=False, meta=None, geom=None):
        if task_id not in self.tiers:
            t = self.by_task[task_id]
            self.tiers[task_id] = {"id": t["id"], "task_id": t["id"], "name": t["name"], "title": t["title"], "color": t["color"],
                                   "output": t["output"], "granularity": t["granularity"], "labels": t.get("labels", []),
                                   "parent": t.get("parent"), "source": "model", "run": {"engine": "pseudo"}}
        s = {"id": new_id("s"), "tier_id": task_id, "start": start, "end": end, "value": value,
             "confidence": conf() if c is None else c, "source": source, "reviewed": reviewed}
        if meta:
            s["meta"] = meta
        if geom:
            s["geom"] = geom
        self.segs.append(s)
        return s

    def speech(self, utts, audio=None, asr_conf=(0.92, 0.04), noisy=False, quality="good", skip_phones=False, errors=None):
        errors = errors or {}
        # 10 ms energy envelope: word boundaries are snapped to energy dips, like a real aligner would
        db = None
        if audio is not None:
            n = len(audio) // 160
            fr = audio[: n * 160].reshape(n, 160)
            db = np.convolve(10 * np.log10((fr ** 2).mean(1) + 1e-9), np.ones(3) / 3, "same")
        for k, u in enumerate(utts):
            a, b = u["start"], u["end"]
            text = errors.get(k, u["text"])
            self.add("asr", a, b, text, conf(*asr_conf))
            toks = u["tokens"] or text.split()
            wts = np.array([len(re.sub(r"\W", "", t)) + 1.5 for t in toks])
            edges = a + (b - a) * np.concatenate([[0], np.cumsum(wts) / wts.sum()])
            bad = set()
            for i in range(1, len(toks)):
                lo, hi = edges[i - 1] + 0.05, edges[i + 1] - 0.05
                if db is not None and hi > lo:
                    w0, w1 = max(lo, edges[i] - 0.12), min(hi, edges[i] + 0.12)
                    f0, f1 = int(w0 * 100), int(w1 * 100)
                    if f1 > f0:
                        edges[i] = (f0 + int(np.argmin(db[f0:f1]))) / 100 + 0.005
                if rng.random() < 0.15 and hi > lo:  # deliberate alignment error to correct by hand
                    edges[i] = float(np.clip(edges[i] + rng.choice([-1, 1]) * rng.uniform(0.07, 0.15), lo, hi))
                    bad |= {i - 1, i}
            for i, tok in enumerate(toks):
                wa, wb = round(float(edges[i]), 3), round(float(edges[i + 1]), 3)
                c = conf(0.42, 0.08) if i in bad else conf(0.88 if noisy else 0.93, 0.05)
                self.add("forced_alignment", wa, wb, tok, c)
                if not skip_phones and u["lang"] != "ja":
                    ph = g2p(tok)
                    if ph:
                        pe = np.linspace(wa, wb, len(ph) + 1)
                        for j, p in enumerate(ph):
                            self.add("phoneme", round(float(pe[j]), 3), round(float(pe[j + 1]), 3), p, conf(0.8 if noisy else 0.86, 0.1))
            self.add("diarization", a, b, u["speaker"], conf(0.9, 0.06))
            self.add("gender", a, b, u["gender"], conf(0.95, 0.03))
            self.add("lid", a, b, u["lang"], conf(0.96, 0.03) if len(toks) > 3 else conf(0.7, 0.1))
            if u["translation"]:
                self.add("translation", a, b, u["translation"], conf(0.86, 0.06))
            emo_conf = {"neutral": (0.84, 0.08), "happy": (0.8, 0.1), "frustrated": (0.52, 0.08), "angry": (0.74, 0.1),
                        "surprised": (0.6, 0.12), "sad": (0.55, 0.1)}[u["emotion"]]
            self.add("ser", a, b, u["emotion"], conf(*emo_conf))
            pace = PACE[u["emotion"]]
            self.add("speech_caption", a, b,
                     f"{u['gender'].capitalize()} voice, {EMO_DESC[u['emotion']]}, {pace} pace.", conf(0.75, 0.1))
            if u["entities"]:
                self.add("ner", a, b, u["entities"], conf(0.85, 0.08))
            q = quality if isinstance(quality, str) else quality(k)
            self.add("sound_quality", a, b, q, conf(0.8, 0.1) if q != "good" else conf(0.93, 0.03))

    def sed(self, track, extra=()):
        runs = []
        for u in track.utts:
            if runs and u["start"] - runs[-1][1] < 1.5:
                runs[-1][1] = u["end"]
            else:
                runs.append([u["start"], u["end"]])
        evs = [(a, b, "speech") for a, b in runs] + list(track.events) + list(extra)
        for a, b, tag in sorted(evs):
            self.add("sed", round(a, 3), round(b, 3), [tag] if isinstance(tag, str) else tag, conf(0.82, 0.1))

    def windows(self, task_id, dur, win, fn):
        t = 0.0
        while t < dur - 1e-3:
            a, b = round(t, 3), round(min(dur, t + win), 3)
            v = fn(a, b)
            if v is not None:
                self.add(task_id, a, b, v[0] if isinstance(v, tuple) else v, v[1] if isinstance(v, tuple) else None)
            t += win

    def speaker_regions(self):
        """Pure speech + overlap tiers from the diarization turns (what the model would return)."""
        turns = [s for s in self.segs if s["tier_id"] == "diarization"]
        pure, over = speaker_regions(turns)
        for r in pure:
            self.add("pure_speech", r["start"], r["end"], r["value"], r["confidence"])
        for r in over:
            self.add("overlap", r["start"], r["end"], r["value"], conf(0.7, 0.12))

    def doc(self):
        return {"tiers": list(self.tiers.values()), "segments": self.segs}


# =============================================================================
# The files
# =============================================================================
def build_interview():
    tr = Track()
    tr.sound(jingle(3.0), "music")
    tr.gap(0.4)
    S, D = "Samantha", "Daniel"
    lines = [
        (S, "Welcome back to Signals and Systems, the podcast about how machines listen.", "happy", ["Signals and Systems [MISC]"]),
        (S, "Today I am joined by Daniel Hughes from the University of Zurich.", "neutral", ["Daniel Hughes [PERSON]", "University of Zurich [ORG]"]),
        (D, "Thanks for having me. It is great to be here.", "happy", []),
        (S, "Your team just released a model that annotates speech, video and images at once. How did that start?", "neutral", []),
        (D, "Honestly, it started out of frustration. We spent months labeling data by hand in ELAN.", "frustrated", ["ELAN [MISC]"]),
        (D, "So last October we decided to let one omni model do the first pass.", "neutral", ["last October [DATE]"]),
        (S, "And does it actually work?", "surprised", []),
        (D, "For transcription and speaker turns, surprisingly well. Emotions are harder.", "neutral", []),
        (D, "The trick is that people only correct the segments the model is unsure about.", "happy", []),
        (S, "That sounds like a huge time saver. Thank you, Daniel.", "happy", ["Daniel [PERSON]"]),
    ]
    for i, (v, txt, emo, ents) in enumerate(lines):
        tr.say(v, txt, emo, entities=ents)
        tr.gap(0.35 if lines[min(i + 1, len(lines) - 1)][0] == v else 0.7)
    tr.sound(applause(3.2), "applause")
    tr.gap(0.5)
    audio = tr.render(bg=lambda n: noise(n, 0.002))
    path = OUT / "podcasts" / "signals_and_systems_ep12.wav"
    write_wav(path, audio)
    an = Ann()
    an.speech(tr.utts, audio=audio, errors={5: "So last October we decided to let one omni model do the first past."})
    an.sed(tr)
    an.add("audio_caption", 0.0, round(len(audio) / SR, 3),
           "A short podcast intro jingle, then a woman interviews a man with a British accent about annotation models; the episode ends with applause.", 0.88)
    # pretend a human already reviewed the first part and fixed one ASR error
    for s in an.segs:
        if s["start"] is not None and s["end"] < tr.utts[3]["end"] + 0.01 and s["tier_id"] in ("asr", "forced_alignment", "diarization", "ser"):
            s["reviewed"] = True
    for s in an.segs:
        if s["tier_id"] == "asr" and s["value"].endswith("first past."):
            s.update(value=tr.utts[5]["text"], source="human", reviewed=True,
                     meta={"model_source": "model", "model_value": "So last October we decided to let one omni model do the first past."})
    return path, an


def build_call():
    tr = Track()
    tr.gap(0.6)
    K, R = "Karen", "Rishi"
    lines = [
        (K, "Good morning, you are speaking with Karen from Northwind Telecom. How can I help?", "neutral", ["Karen [PERSON]", "Northwind Telecom [ORG]"]),
        (R, "Hi, my internet has been down since Monday and nobody has called me back.", "frustrated", ["Monday [DATE]"]),
        (K, "I am really sorry about that. Can you confirm your account number for me?", "neutral", []),
        (R, "It is four five two, nine nine one. This is the third time I am calling!", "angry", []),
        (K, "Thank you. I can see an outage in Melbourne. A technician will visit tomorrow morning.", "neutral", ["Melbourne [GPE]", "tomorrow morning [DATE]"]),
        (R, "Okay, fine. Thanks.", "sad", []),
    ]
    for v, txt, emo, ents in lines:
        tr.say(v, txt, emo, entities=ents)
        tr.gap(0.5)
    audio = tr.render(bg=lambda n: noise(n, 0.045) + noise(n, 0.03, "brown"))
    path = OUT / "calls" / "support_call_0423_noisy.wav"
    write_wav(path, audio)
    an = Ann()
    an.speech(tr.utts, audio=audio, asr_conf=(0.62, 0.12), noisy=True, quality=lambda k: ["fair", "poor"][k % 2],
              errors={0: "Good morning, you are speaking with Karen from North wind Telecom. How can I help?",
                      3: "It is four five two, nine nine one. This is the third time I am calling."})
    an.sed(tr, extra=[(0.0, round(len(audio) / SR, 3), "noise")])
    an.add("audio_caption", 0.0, round(len(audio) / SR, 3),
           "A noisy phone call: a female support agent with an Australian accent helps an increasingly frustrated male customer.", 0.71)
    return path, an


def build_panel():
    tr = Track()
    lines = [
        ("Samantha", "Welcome to our multilingual panel on language technology.", "happy", None, []),
        ("Anna", "Danke für die Einladung. Ich arbeite an Spracherkennung für Dialekte.", "neutral",
         "Thanks for the invitation. I work on speech recognition for dialects.", []),
        ("Thomas", "Bonjour à tous. Notre équipe à Paris étudie les émotions dans la voix.", "neutral",
         "Hello everyone. Our team in Paris studies emotions in the voice.", ["Paris [GPE]"]),
        ("Mónica", "Hola. En Madrid usamos modelos multimodales para subtitular vídeos.", "happy",
         "Hello. In Madrid we use multimodal models to subtitle videos.", ["Madrid [GPE]"]),
        ("Alice", "Buongiorno! A Milano raccogliamo dati di parlato spontaneo.", "happy",
         "Good morning! In Milan we collect spontaneous speech data.", ["Milano [GPE]"]),
        ("Samantha", "Wonderful. Let us start with the first question.", "neutral", None, []),
    ]
    tr.gap(0.5)
    for v, txt, emo, trans, ents in lines:
        tr.say(v, txt, emo, translation=trans, entities=ents)
        tr.gap(0.8)
    audio = tr.render(bg=lambda n: noise(n, 0.003))
    path = OUT / "podcasts" / "multilingual_panel.wav"
    write_wav(path, audio)
    an = Ann()
    an.speech(tr.utts, audio=audio, asr_conf=(0.88, 0.06))
    an.sed(tr)
    an.add("audio_caption", 0.0, round(len(audio) / SR, 3),
           "A panel discussion where five speakers introduce themselves in English, German, French, Spanish and Italian.", 0.84)
    return path, an


def build_japanese():
    tr = Track()
    tr.gap(0.4)
    text = "今日の東京は晴れのち曇り、最高気温は二十三度です。"
    toks = ["今日", "の", "東京", "は", "晴れ", "のち", "曇り", "最高", "気温", "は", "二十三", "度", "です"]
    tr.say("Kyoko", text, "neutral", translation="Today in Tokyo it will be sunny, later cloudy, with a high of 23 degrees.",
           entities=["東京 [GPE]"], tokens=toks)
    tr.gap(0.5)
    tr.say("Kyoko", "傘は必要ありません。", "happy", translation="You will not need an umbrella.", tokens=["傘", "は", "必要", "ありません"])
    tr.gap(0.5)
    audio = tr.render()
    path = OUT / "podcasts" / "weather_tokyo_ja.wav"
    write_wav(path, audio)
    an = Ann()
    an.speech(tr.utts, audio=audio, skip_phones=True)
    an.sed(tr)
    an.add("audio_caption", 0.0, round(len(audio) / SR, 3), "A female presenter reads a short weather forecast in Japanese.", 0.9)
    return path, an


def build_debate():
    """Three people talking over each other: overlapping turns, a back-channel inside a turn."""
    lines = [  # (voice, text, emotion, gap before it; negative = starts before the previous speaker has finished)
        ("Karen", "Let me be clear, the data shows that labelling everything by hand does not scale.", "neutral", 0.5),
        ("Fred", "But automatic labels are only as good as the model behind them.", "neutral", -0.9),
        ("Karen", "Which is exactly why people should review them.", "frustrated", 0.3),
        ("Rishi", "Can I add something here?", "neutral", -0.7),
        ("Fred", "Yes, go ahead.", "neutral", -0.3),
        ("Rishi", "In our lab the model does the first pass, and people only fix what looks wrong.", "happy", 0.4),
        ("Karen", "Right, right.", "happy", -1.6),
        ("Fred", "And how much time does that save you?", "surprised", 0.3),
        ("Rishi", "About two thirds of the time, on speech data.", "neutral", 0.35),
        ("Karen", "That is impressive.", "happy", -0.5),
        ("Fred", "Impressive, but is it reproducible?", "neutral", -0.8),
    ]
    clips, t, end = [], 0.0, 0.0
    speakers = {}
    for v, txt, emo, gap in lines:
        x = tts(v, txt, emo)
        start = max(0.0, end + gap) if gap < 0 else end + gap
        if gap < 0 and clips:
            start = max(start, clips[-1][1] + 0.3)  # never before the previous turn has started
        speakers.setdefault(v, f"SPEAKER_{len(speakers)}")
        lang, gender = VOICES[v]
        clips.append((x, start))
        end = max(end, start + len(x) / SR)
        t = start
    audio = np.zeros(int((end + 0.6) * SR), np.float32)
    utts = []
    for (x, start), (v, txt, emo, _) in zip(clips, lines):
        i = int(start * SR)
        audio[i: i + len(x)] += x * 0.8
        lang, gender = VOICES[v]
        utts.append({"start": round(start, 3), "end": round(start + len(x) / SR, 3), "text": txt, "voice": v,
                     "speaker": speakers[v], "lang": lang, "gender": gender, "emotion": emo, "translation": None,
                     "entities": [], "tokens": None})
    audio = np.clip(audio + noise(len(audio), 0.002), -1, 1).astype(np.float32)
    path = OUT / "podcasts" / "studio_debate.wav"
    write_wav(path, audio)
    an = Ann()
    an.speech(utts, audio=audio, asr_conf=(0.84, 0.07), skip_phones=True)
    an.speaker_regions()
    for k, u in enumerate(utts):  # one deliberate false alarm to correct
        lab, c = ("voice conversion", 0.41) if k == 4 else ("bonafide", conf(0.93, 0.03))
        an.add("spoof_audio", u["start"], u["end"], lab, c)
    an.add("audio_caption", 0.0, round(len(audio) / SR, 3),
           "A heated studio debate: two men and a woman interrupt each other about automatic labelling.", 0.86)
    return path, an


SLIDES = [
    ("Speech Processing 101", ["Lecture 3: Forced alignment", "Prof. D. Hughes · Autumn 2026"]),
    ("What is forced alignment?", ["Input: audio + transcript", "Output: start / end time per word", "and per phone",
                                   "Used for subtitles, phonetics, training data"]),
    ("Alignment error vs. model size", []),
]


def slide_img(k, w, h):
    img = Image.new("RGB", (w, h), "#f8fafc")
    d = ImageDraw.Draw(img)
    title, bullets = SLIDES[k]
    d.rectangle([0, 0, w, 8], fill="#0d9488")
    if k == 0:
        d.text((40, h * 0.32), title, fill="#0f172a", font=font(int(h * 0.11), True))
        for i, b in enumerate(bullets):
            d.text((42, h * 0.52 + i * h * 0.09), b, fill="#475569", font=font(int(h * 0.06)))
    else:
        d.text((30, 24), title, fill="#0f172a", font=font(int(h * 0.075), True))
    if k == 1:
        for i, b in enumerate(bullets):
            d.ellipse([34, 92 + i * 46, 44, 102 + i * 46], fill="#0d9488")
            d.text((56, 84 + i * 46), b, fill="#334155", font=font(int(h * 0.058)))
    if k == 2:
        vals = [("S", 42), ("M", 31), ("L", 22), ("XL", 15)]
        base, bw = h - 50, (w - 120) / len(vals)
        d.line([60, 70, 60, base], fill="#94a3b8", width=2)
        d.line([60, base, w - 30, base], fill="#94a3b8", width=2)
        for i, (lab, v) in enumerate(vals):
            x0 = 80 + i * bw
            d.rectangle([x0, base - v * (h - 140) / 45, x0 + bw * 0.6, base], fill=["#5eead4", "#2dd4bf", "#14b8a6", "#0d9488"][i])
            d.text((x0 + bw * 0.2, base + 8), lab, fill="#334155", font=font(16))
            d.text((x0 + bw * 0.12, base - v * (h - 140) / 45 - 22), f"{v} ms", fill="#334155", font=font(15))
    d.text((w - 60, h - 26), f"{k + 1}/3", fill="#94a3b8", font=font(14))
    return img


def build_lecture():
    tr = Track()
    tr.gap(0.8)
    D = "Daniel"
    plan = [  # (slide, text, emotion)
        (0, "Hello everyone, and welcome to lecture three of Speech Processing one oh one.", "happy"),
        (0, "Today we look at forced alignment.", "neutral"),
        (1, "Forced alignment takes an audio recording and its transcript.", "neutral"),
        (1, "It finds the start and end time of every word, and even of every phone.", "neutral"),
        (2, "As you can see on this chart, larger models make fewer boundary errors.", "neutral"),
        (2, "Next week we will train our own aligner. See you then!", "happy"),
    ]
    slide_start = {}
    for i, (k, txt, emo) in enumerate(plan):
        if k not in slide_start:
            if k:
                tr.gap(0.8)
            slide_start[k] = round(tr.t - (0.4 if k else 0.8), 3)
        tr.say(D, txt, emo, slide=k, entities=["Speech Processing 101 [MISC]"] if i == 0 else [])
        tr.gap(0.45)
    tr.gap(1.0)
    audio = tr.render(bg=lambda n: noise(n, 0.002))
    dur = len(audio) / SR
    env = envelope(audio)
    bounds = [slide_start[0], slide_start[1], slide_start[2], dur]

    def draw(t):
        k = max(i for i in range(3) if t >= bounds[i])
        img = Image.new("RGB", (640, 360), "#0f172a")
        img.paste(slide_img(k, 440, 300), (16, 30))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([470, 30, 624, 210], radius=10, fill="#1e293b")
        u = active(tr.utts, t)
        m = float(env[min(len(env) - 1, int(t * 25))]) if u else 0.0
        expr = "happy" if (u and u["emotion"] == "happy") or t > bounds[3] - 2.5 else "neutral"
        face(d, 547, 105, 40, skin="#e8b896", hair="#5b3a29", expr=expr, mouth=m, blink=(t % 3.7) < 0.13, shirt="#334155")
        d.text((478, 186), "D. Hughes", fill="#cbd5e1", font=font(14))
        d.rectangle([16, 340, 16 + 608 * t / dur, 344], fill="#14b8a6")
        d.text((470, 230), "LIVE", fill="#f87171", font=font(14, True))
        return img

    path = OUT / "videos" / "lecture03_forced_alignment.mp4"
    write_video(path, draw, audio)
    an = Ann()
    an.speech(tr.utts, audio=audio)
    an.sed(tr)
    for k in range(3):
        an.add("shots", bounds[k], round(bounds[k + 1], 3), "shot", conf(0.95, 0.02))
        text = " · ".join([SLIDES[k][0]] + SLIDES[k][1]) if k < 2 else "Alignment error vs. model size · S 42 ms · M 31 ms · L 22 ms · XL 15 ms"
        an.add("ocr", bounds[k], round(bounds[k + 1], 3), text, conf(0.9, 0.04))
    an.windows("fer", dur, 2, lambda a, b: ("happy", conf(0.8, 0.08)) if any(u["emotion"] == "happy" and u["start"] < b and u["end"] > a for u in tr.utts)
               else ("neutral", conf(0.86, 0.06)))
    an.windows("action", dur, 4, lambda a, b: "presenting slides and talking to the camera" if a < bounds[2] else "explaining a bar chart")
    an.windows("dense_caption", dur, 8, lambda a, b: [
        "A title slide for Speech Processing 101 is shown while the lecturer greets the audience.",
        "The lecturer explains what forced alignment is next to a bulleted slide.",
        "A bar chart compares alignment error across model sizes.",
        "The lecturer wraps up and announces next week's topic."][min(3, int(a // 8))])
    for u in tr.utts:
        an.add("active_speaker", u["start"], u["end"], "on-screen", conf(0.93, 0.03))
    an.add("video_caption", 0.0, round(dur, 3), "A recorded lecture: slides about forced alignment on the left and the lecturer's webcam on the right. "
           "He introduces the topic, explains inputs and outputs, and shows a chart of alignment error by model size.", 0.86)
    an.add("audio_caption", 0.0, round(dur, 3), "A male lecturer with a British accent speaks clearly in a quiet room.", 0.9)
    return path, an


def build_meeting():
    tr = Track()
    tr.gap(0.6)
    S, F, M = "Samantha", "Fred", "Moira"
    names = {S: ("Lena · PM", "#f1c7a5", "#3b2a20", "#6366f1"), F: ("Tom · Engineering", "#c58c6b", "#1f2937", "#16a34a"),
             M: ("Aoife · Design", "#f6d2bd", "#b45309", "#db2777")}
    lines = [
        (S, "Okay team, let's go over the release plan for Friday.", "neutral", ["Friday [DATE]"]),
        (F, "The backend is ready. We still need two more days of testing.", "neutral", []),
        (M, "The new editor design is finished. I will share my screen.", "happy", []),
        (M, "Here you can see the tier view with the waveform and the spectrogram.", "happy", []),
        (F, "Wow, that looks much cleaner than before!", "surprised", []),
        (S, "Great work everyone. Let's ship it on Friday.", "happy", ["Friday [DATE]"]),
    ]
    share_at = None
    for i, (v, txt, emo, ents) in enumerate(lines):
        if i == 3:
            share_at = round(tr.t, 3)
        tr.say(v, txt, emo, entities=ents)
        tr.gap(0.6)
    tr.gap(0.8)
    audio = tr.render(bg=lambda n: noise(n, 0.004))
    dur = len(audio) / SR
    env = envelope(audio)
    order = [S, F, M]

    def tile(d, x, y, w, h, voice, t, small=False):
        u = active(tr.utts, t)
        speaking = u is not None and u["voice"] == voice
        nm, skin, hair, shirt = names[voice]
        d.rounded_rectangle([x, y, x + w, y + h], radius=8, fill="#1e293b", outline="#22c55e" if speaking else "#334155", width=3 if speaking else 1)
        m = float(env[min(len(env) - 1, int(t * 25))]) if speaking else 0.0
        expr = u["emotion"] if speaking and u["emotion"] in ("happy", "surprised") else "neutral"
        face(d, x + w / 2, y + h * 0.42, h * (0.2 if not small else 0.24), skin, hair, expr, m,
             blink=((t + order.index(voice) * 1.3) % 4.1) < 0.13, shirt=shirt)
        if not small:
            d.text((x + 10, y + h - 24), nm, fill="#e2e8f0", font=font(14))

    def draw(t):
        img = Image.new("RGB", (640, 360), "#0b1120")
        d = ImageDraw.Draw(img)
        if share_at is None or t < share_at:
            for i, v in enumerate(order):
                tile(d, 12 + i * 209, 70, 199, 210, v, t)
            d.text((12, 24), "Weekly sync · Omni Studio release", fill="#94a3b8", font=font(15))
        else:
            sh = Image.new("RGB", (470, 300), "#111827")
            sd = ImageDraw.Draw(sh)
            sd.rectangle([0, 0, 470, 26], fill="#1f2937")
            sd.text((10, 5), "Omni Studio — tier editor", fill="#e5e7eb", font=font(13))
            for j in range(470):
                a = abs(math.sin(j * 0.21) * math.sin(j * 0.031 + t)) * 32
                sd.line([j, 70 - a, j, 70 + a], fill="#5eead4")
            for r, (lab, col) in enumerate([("ASR", "#38bdf8"), ("Words", "#22d3ee"), ("Speaker", "#fb923c"), ("SER", "#f472b6")]):
                yy = 120 + r * 40
                sd.text((8, yy + 8), lab, fill="#cbd5e1", font=font(12))
                for k in range(6):
                    x0 = 70 + k * 66 + (r * 9) % 20
                    sd.rounded_rectangle([x0, yy, x0 + 56, yy + 28], radius=4, fill=col)
            img.paste(sh, (12, 30))
            for i, v in enumerate(order):
                tile(d, 494, 30 + i * 102, 134, 94, v, t, small=True)
            d.text((14, 338), "Aoife is sharing her screen", fill="#94a3b8", font=font(13))
        return img

    path = OUT / "videos" / "weekly_sync_3_people.mp4"
    write_video(path, draw, audio)
    an = Ann()
    an.speech(tr.utts, audio=audio, quality=lambda k: "good" if k % 3 else "fair")
    an.sed(tr)
    an.add("shots", 0.0, share_at, "shot", conf(0.94, 0.02))
    an.add("shots", share_at, round(dur, 3), "shot", conf(0.9, 0.03))
    an.add("ocr", 0.0, share_at, "Weekly sync · Omni Studio release · Lena · PM · Tom · Engineering · Aoife · Design", conf(0.88, 0.05))
    an.add("ocr", share_at, round(dur, 3), "Omni Studio — tier editor · ASR · Words · Speaker · SER · Aoife is sharing her screen", conf(0.83, 0.06))
    for u in tr.utts:
        an.add("active_speaker", u["start"], u["end"], "on-screen", conf(0.9, 0.05))
    an.windows("fer", dur, 2, lambda a, b: next(((u["emotion"] if u["emotion"] in ("happy", "surprised") else "neutral", conf(0.72, 0.12))
                                                 for u in tr.utts if u["start"] < b and u["end"] > a), ("neutral", conf(0.8, 0.08))))
    an.windows("action", dur, 4, lambda a, b: "video call, people taking turns to talk" if b <= share_at + 2 else "screen sharing a design")
    an.windows("dense_caption", dur, 8, lambda a, b: ("Three colleagues in a video call discuss a release date." if a < share_at
                                                      else "One participant shares a screen showing an annotation editor while the others comment."))
    an.add("video_caption", 0.0, round(dur, 3), "A three-person video meeting. The project manager asks about the Friday release, an engineer reports "
           "on testing, and a designer shares her screen to show the new tier editor.", 0.84)
    an.add("audio_caption", 0.0, round(dur, 3), "Three people talk in turn in an online meeting with light background noise.", 0.82)
    return path, an


def build_street():
    dur = 12.0
    n = int(dur * SR)
    t = np.arange(n) / SR
    amb = noise(n, 0.02, "brown") + noise(n, 0.006)
    car_a, car_b = 2.5, 9.5
    car_env = np.exp(-((t - (car_a + car_b) / 2) / 1.6) ** 2)
    engine = (noise(n, 1.0, "brown") * 0.6 + np.sin(2 * np.pi * (70 + 10 * car_env) * t) * 0.4) * car_env * 0.25
    audio = amb + engine
    birds = []
    for at in (1.0, 1.3, 6.8, 10.6, 10.9):
        c = chirp()
        s = int(at * SR)
        audio[s: s + len(c)] += c
        birds.append(at)
    audio = np.clip(audio, -1, 1).astype(np.float32)

    def draw(tt):
        x = None
        if car_a - 1 <= tt <= car_b + 1:
            x = -280 + (tt - (car_a - 1)) / (car_b - car_a + 2) * 1600
        return street(640, 360, x, tt)

    path = OUT / "videos" / "street_traffic_clip.mp4"
    write_video(path, draw, audio)
    an = Ann()
    an.add("sed", 0.0, dur, ["noise"], conf(0.78, 0.05))
    an.add("sed", car_a, car_b, ["vehicle"], conf(0.91, 0.03))
    for b in (1.0, 6.8, 10.6):
        an.add("sed", b, round(b + 0.5, 3), ["animal"], conf(0.55, 0.12))
    an.add("shots", 0.0, dur, "shot", 0.97)
    an.add("ocr", 0.0, dur, "MAIN ST", conf(0.9, 0.03))
    an.windows("fer", dur, 2, lambda a, b: ("no face", conf(0.9, 0.04)))
    an.windows("action", dur, 4, lambda a, b: "a red car drives past" if a < car_b and b > car_a else "a pedestrian waits on the sidewalk")
    an.windows("dense_caption", dur, 8, lambda a, b: "A red car drives from left to right along a city street." if a < 8 else
               "The street is quiet again; a pedestrian stands near a tree.")
    an.add("video_caption", 0.0, dur, "A static shot of a sunny city street with buildings, a tree and a pedestrian; a red car drives past from left to right.", 0.89)
    an.add("audio_caption", 0.0, dur, "Outdoor city ambience with a car passing by and a few birds chirping.", 0.8)
    return path, an


# ----------------------------------------------------------------------------- spatial helpers
def face_box(cx, cy, r):
    return (cx - 0.95 * r, cy - 1.12 * r, 1.9 * r, 2.07 * r)


def face_kps(cx, cy, r):
    """nose, left eye, right eye, left ear, right ear (the person's left is the image right)."""
    return [(cx, cy + 0.2 * r), (cx + 0.32 * r, cy - 0.12 * r), (cx - 0.32 * r, cy - 0.12 * r),
            (cx + 0.84 * r, cy + 0.05 * r), (cx - 0.84 * r, cy + 0.05 * r)]


BODY = ["ls", "rs", "le", "re", "lw", "rw", "lh", "rh", "lk", "rk", "la", "ra"]


def person(d, J, hc, r, skin, hair, shirt, pants, expr="neutral"):
    lw = int(r * 0.42)
    for hip, knee, ank in (("rh", "rk", "ra"), ("lh", "lk", "la")):
        d.line([J[hip], J[knee], J[ank]], fill=pants, width=int(lw * 1.25), joint="curve")
        ax, ay = J[ank]
        d.ellipse([ax - lw * 0.9, ay - lw * 0.3, ax + lw * 0.9, ay + lw * 0.5], fill="#1f2937")
    d.polygon([J["ls"], J["rs"], J["rh"], J["lh"]], fill=shirt)
    for sh, el, wr in (("rs", "re", "rw"), ("ls", "le", "lw")):
        d.line([J[sh], J[el]], fill=shirt, width=lw, joint="curve")
        d.line([J[el], J[wr]], fill=skin, width=int(lw * 0.85))
        wx, wy = J[wr]
        d.ellipse([wx - lw * 0.55, wy - lw * 0.55, wx + lw * 0.55, wy + lw * 0.55], fill=skin)
    nx, ny = (J["ls"][0] + J["rs"][0]) / 2, (J["ls"][1] + J["rs"][1]) / 2
    d.line([(nx, ny), (hc[0], hc[1] + r * 0.6)], fill=skin, width=int(r * 0.5))
    face(d, hc[0], hc[1], r, skin, hair, expr, bust=False)


def norm_box(b, W, H):
    return {"type": "box", "x": round(b[0] / W, 5), "y": round(b[1] / H, 5), "w": round(b[2] / W, 5), "h": round(b[3] / H, 5)}


def norm_pose(pts, W, H, box=None):
    g = {"type": "keypoints", "skeleton": "coco17",
         "points": [[round(x / W, 5), round(y / H, 5), v] if v else [0, 0, 0] for x, y, v in pts]}
    if box:
        g["box"] = [round(box[0] / W, 5), round(box[1] / H, 5), round(box[2] / W, 5), round(box[3] / H, 5)]
    return g


def bounds(pts, pad):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)


def bust_pose(cx, cy, r, H, drop=()):
    """Head + shoulders visible, the rest of the body is outside the frame (v = 0)."""
    pts = [(x, y, 2) for x, y in face_kps(cx, cy, r)]
    pts += [(cx + 1.1 * r, cy + 1.25 * r, 2), (cx - 1.1 * r, cy + 1.25 * r, 2)]
    pts += [(0, 0, 0)] * 10
    return [(0, 0, 0) if i in drop else p for i, p in enumerate(pts)]


def build_pose_park():
    W, H = 1280, 800
    img = gradient(W, H, "#9bd3f5", "#e6f4fb")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 520, W, H], fill="#86c26a")
    d.rectangle([0, 700, W, H], fill="#74b35a")
    d.rectangle([150, 360, 170, 560], fill="#7c4a24")
    for dx, dy, rr in ((0, -60, 90), (-60, -10, 70), (60, -10, 70)):
        d.ellipse([160 + dx - rr, 300 + dy - rr, 160 + dx + rr, 300 + dy + rr], fill="#2f8f46")
    d.rectangle([1040, 600, 1220, 618], fill="#8b5a2b")       # bench
    d.rectangle([1040, 560, 1220, 574], fill="#8b5a2b")
    d.rectangle([1052, 618, 1062, 660], fill="#5b3a1a")
    d.rectangle([1198, 618, 1208, 660], fill="#5b3a1a")
    # person C (small, in the background, partly hidden by a bush): the "model" misses this one
    JC = {"ls": (668, 368), "rs": (612, 368), "le": (680, 410), "re": (600, 410), "lw": (684, 448), "rw": (598, 448),
          "lh": (656, 452), "rh": (624, 452), "lk": (658, 500), "rk": (622, 500), "la": (660, 540), "ra": (620, 540)}
    person(d, JC, (640, 330), 24, "#c58c6b", "#111827", "#7c3aed", "#374151")
    for dx, rr in ((-70, 46), (-20, 58), (35, 52), (85, 40)):
        d.ellipse([640 + dx - rr, 470 - rr * 0.7, 640 + dx + rr, 470 + rr * 1.1], fill="#3f9d4e")
    # person A: waving with the left arm
    JA = {"ls": (440, 300), "rs": (320, 300), "le": (505, 228), "lw": (525, 140), "re": (292, 398), "rw": (285, 488),
          "lh": (415, 480), "rh": (345, 480), "lk": (425, 600), "rk": (335, 600), "la": (430, 720), "ra": (330, 720)}
    person(d, JA, (380, 218), 56, "#f1c7a5", "#3b2a20", "#ef4444", "#1e3a8a", "happy")
    # person B: walking
    JB = {"ls": (955, 312), "rs": (845, 312), "le": (990, 410), "lw": (1012, 500), "re": (818, 405), "rw": (798, 490),
          "lh": (930, 492), "rh": (870, 492), "lk": (968, 602), "rk": (858, 612), "la": (995, 716), "ra": (848, 726)}
    person(d, JB, (900, 232), 52, "#e8b896", "#9a3412", "#0ea5e9", "#3f3f46", "neutral")
    d.ellipse([1040, 690, 1088, 738], fill="#facc15")          # ball
    d.arc([1040, 690, 1088, 738], 200, 340, fill="#ca8a04", width=4)
    p = OUT / "images" / "pose_park.png"
    img.save(p)

    def full(J, hc, r):
        return [(x, y, 2) for x, y in face_kps(*hc, r)] + [(J[k][0], J[k][1], 2) for k in BODY]

    an = Ann()
    pa, pb = full(JA, (380, 218), 56), full(JB, (900, 232), 52)
    box_a = bounds([(x, y) for x, y, _ in pa] + [(380, 218 - 1.15 * 56)], 24)
    box_b = bounds([(x, y) for x, y, _ in pb] + [(900, 232 - 1.15 * 52)], 24)
    # model mistakes to correct: A's left wrist is off, B's right ankle is missing and the right knee is off
    pa_model = list(pa)
    pa_model[9] = (pa[9][0] + 42, pa[9][1] + 46, 2)
    pb_model = list(pb)
    pb_model[16] = (0, 0, 0)
    pb_model[14] = (pb[14][0] - 30, pb[14][1] - 22, 2)
    an.add("pose", None, None, "person", 0.71, geom=norm_pose(pa_model, W, H, box_a))
    an.add("pose", None, None, "person", 0.46, geom=norm_pose(pb_model, W, H, box_b))
    an.add("person_boxes", None, None, "person", 0.93, geom=norm_box(box_a, W, H))
    an.add("person_boxes", None, None, "person", 0.48, geom=norm_box((box_b[0] + 20, box_b[1] + 40, box_b[2] - 50, box_b[3] - 150), W, H))
    an.add("face_boxes", None, None, "happy", 0.88, geom=norm_box(face_box(380, 218, 56), W, H))
    an.add("face_boxes", None, None, "neutral", 0.57, geom=norm_box(face_box(900, 232, 52), W, H))
    an.add("object_boxes", None, None, "bench", 0.9, geom=norm_box((1034, 552, 194, 112), W, H))
    an.add("object_boxes", None, None, "tree", 0.86, geom=norm_box((70, 170, 190, 395), W, H))
    an.add("object_boxes", None, None, "ball", 0.52, geom=norm_box((1036, 686, 56, 56), W, H))
    an.add("image_caption", None, None, "Two people in a park: a woman in a red shirt waves while a man in a blue shirt walks past a bench and a yellow ball.", 0.87)
    an.add("image_tags", None, None, ["person", "group", "outdoor"], 0.9)
    an.add("fer", None, None, "happy", 0.66)
    return p, an


# ----------------------------------------------------------------------------- images
def build_images():
    out = []
    p = OUT / "images" / "street_scene.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    street(1280, 800, 420).save(p)
    an = Ann()
    an.add("image_caption", None, None, "A sunny city street with a red car, a pedestrian in a red shirt and a tree in front of tall buildings.", 0.9)
    an.add("detailed_caption", None, None, "Daytime urban scene under a blue sky with a few clouds and the sun in the top right. A row of brown and grey "
           "buildings with lit windows lines the street. A red hatchback drives on a two-lane road with white dashed markings. A person in a red "
           "shirt stands on the sidewalk, a green street sign reads 'MAIN ST', and a leafy tree stands on the left.", 0.82)
    an.add("image_tags", None, None, ["outdoor", "vehicle", "person", "text"], 0.87)
    an.add("ocr", None, None, "MAIN ST", 0.93)
    an.add("fer", None, None, "no face", 0.62)
    W, H = 1280, 800
    an.add("person_boxes", None, None, "person", 0.81, geom=norm_box((740, 466, 40, 114), W, H))
    an.add("object_boxes", None, None, "car", 0.95, geom=norm_box((418, 572, 266, 148), W, H))
    an.add("object_boxes", None, None, "tree", 0.83, geom=norm_box((40, 336, 116, 236), W, H))
    an.add("object_boxes", None, None, "sign", 0.77, geom=norm_box((1056, 396, 138, 168), W, H))
    out.append((p, an, False))

    for name, expr, c_fer, bg, reviewed in (("portrait_smiling.png", "happy", 0.94, ("#fde68a", "#f59e0b"), True),
                                            ("portrait_surprised.png", "surprised", 0.51, ("#bfdbfe", "#6366f1"), False),
                                            ("portrait_sad.png", "sad", 0.58, ("#cbd5e1", "#475569"), False)):
        img = gradient(900, 900, *bg)
        face(ImageDraw.Draw(img), 450, 400, 230, skin={"happy": "#f1c7a5", "surprised": "#c58c6b", "sad": "#e8b896"}[expr],
             hair={"happy": "#3b2a20", "surprised": "#111827", "sad": "#9a3412"}[expr], expr=expr, shirt="#0f766e")
        p = OUT / "images" / name
        img.save(p)
        an = Ann()
        cap = {"happy": "A cartoon portrait of a smiling person with dark hair in front of a warm yellow background.",
               "surprised": "A cartoon portrait of a surprised person with wide eyes and an open mouth on a blue background.",
               "sad": "A cartoon portrait of a person with auburn hair looking sad on a grey background."}[expr]
        an.add("image_caption", None, None, cap, conf(0.88, 0.04), reviewed=reviewed)
        an.add("fer", None, None, expr, c_fer, reviewed=reviewed)
        an.add("image_tags", None, None, ["person", "face"], 0.95, reviewed=reviewed)
        an.add("face_caption", None, None, f"Adult with a {expr} expression, looking straight at the camera, head upright, "
               "short hair, wearing a teal shirt.", conf(0.8, 0.06), reviewed=reviewed)
        fb = face_box(450, 400, 230)
        if expr == "surprised":  # model mistake: the face box is shifted and too small
            fb = (fb[0] + 60, fb[1] + 70, fb[2] * 0.78, fb[3] * 0.8)
        an.add("face_boxes", None, None, expr, c_fer, reviewed=reviewed, geom=norm_box(fb, 900, 900))
        an.add("person_boxes", None, None, "person", 0.95, reviewed=reviewed, geom=norm_box((105, 142, 690, 758), 900, 900))
        an.add("pose", None, None, "person", 0.84, reviewed=reviewed,
               geom=norm_pose(bust_pose(450, 400, 230, 900), 900, 900, (105, 142, 690, 758)))
        out.append((p, an, reviewed))

    img = Image.new("RGB", (900, 1200), "#fbf8f1")
    d = ImageDraw.Draw(img)
    lines = ["Meeting notes — 20 October 2026", "", "Attendees: Lena, Tom, Aoife", "",
             "• Release editor v0.1 on Friday", "• Two more days of backend testing", "• Connect Qwen3-Omni after the release", "",
             "Action items", "1. Tom: finish load tests", "2. Aoife: export screenshots", "3. Lena: update the roadmap"]
    y = 90
    for i, ln in enumerate(lines):
        d.text((80, y), ln, fill="#1f2937", font=font(44 if i == 0 else 32, bold=i in (0, 8)))
        y += 70 if i == 0 else 56
    d.line([80, 165, 820, 165], fill="#cbd5e1", width=2)
    img = img.rotate(-1.2, resample=Image.BICUBIC, fillcolor="#e5e7eb").filter(ImageFilter.GaussianBlur(0.6))
    p = OUT / "images" / "meeting_notes_scan.png"
    img.save(p)
    an = Ann()
    an.add("ocr", None, None, "\n".join(l for l in lines if l), 0.91)
    an.add("image_caption", None, None, "A scanned page of typed meeting notes with a title, attendee list, bullet points and action items.", 0.92)
    an.add("image_tags", None, None, ["text", "indoor"], 0.74)
    an.add("fer", None, None, "no face", 0.97)
    out.append((p, an, False))

    img = Image.new("RGB", (1200, 800), "white")
    d = ImageDraw.Draw(img)
    d.text((70, 40), "Annotated hours per month", fill="#0f172a", font=font(44, True))
    months = [("Jan", 120), ("Feb", 160), ("Mar", 210), ("Apr", 190), ("May", 280), ("Jun", 360)]
    base = 700
    d.line([110, 140, 110, base], fill="#64748b", width=3)
    d.line([110, base, 1140, base], fill="#64748b", width=3)
    for i, (m, v) in enumerate(months):
        x = 160 + i * 160
        d.rectangle([x, base - v * 1.4, x + 100, base], fill="#14b8a6")
        d.text((x + 28, base + 14), m, fill="#334155", font=font(28))
        d.text((x + 22, base - v * 1.4 - 40), str(v), fill="#0f172a", font=font(28))
    p = OUT / "images" / "annotated_hours_chart.png"
    img.save(p)
    an = Ann()
    an.add("image_caption", None, None, "A bar chart titled 'Annotated hours per month' rising from 120 hours in January to 360 in June.", 0.93, reviewed=True)
    an.add("ocr", None, None, "Annotated hours per month · Jan 120 · Feb 160 · Mar 210 · Apr 190 · May 280 · Jun 360", 0.88, reviewed=True)
    an.add("image_tags", None, None, ["text", "screen"], 0.66, reviewed=True)
    an.add("fer", None, None, "no face", 0.98, reviewed=True)
    out.append((p, an, True))

    img = gradient(1200, 800, "#e0e7ff", "#c7d2fe")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 600, 1200, 800], fill="#a5b4fc")
    for cx, (skin, hair, expr, shirt) in zip((280, 600, 920), (("#f1c7a5", "#3b2a20", "happy", "#6366f1"),
                                                               ("#c58c6b", "#1f2937", "neutral", "#16a34a"),
                                                               ("#f6d2bd", "#b45309", "happy", "#db2777"))):
        face(d, cx, 330, 120, skin, hair, expr, shirt=shirt)
    p = OUT / "images" / "team_photo.png"
    img.save(p)
    an = Ann()
    an.add("image_caption", None, None, "A cartoon group photo of three colleagues standing side by side, two of them smiling.", 0.89)
    an.add("image_tags", None, None, ["group", "person", "face", "indoor"], 0.9)
    an.add("fer", None, None, "happy", 0.57)
    an.add("face_caption", None, None, "Three adults facing the camera: left and right are smiling, the middle person looks neutral.", 0.77)
    for cx, expr, c in ((280, "happy", 0.91), (600, "sad", 0.41), (920, "happy", 0.86)):  # middle label is wrong
        an.add("face_boxes", None, None, expr, c, geom=norm_box(face_box(cx, 330, 120), 1200, 800))
        an.add("person_boxes", None, None, "person", conf(0.9, 0.03), geom=norm_box((cx - 180, 190, 360, 610), 1200, 800))
        an.add("pose", None, None, "person", 0.62 if cx == 920 else conf(0.85, 0.04),
               geom=norm_pose(bust_pose(cx, 330, 120, 800, drop=(3,) if cx == 920 else ()), 1200, 800, (cx - 180, 190, 360, 610)))
    out.append((p, an, False))
    return out


# =============================================================================
TASKS = ["asr", "forced_alignment", "phoneme", "diarization", "pure_speech", "overlap", "gender", "lid", "translation", "ser", "speech_caption", "ner",
         "sound_quality", "sed", "audio_caption", "active_speaker", "fer", "shots", "ocr", "action", "dense_caption",
         "video_caption", "image_caption", "detailed_caption", "image_tags", "face_boxes", "person_boxes", "object_boxes",
         "pose", "face_caption", "spoof_audio", "spoof_video", "spoof_image", "spoof_regions"]


def add_files(builders):
    """Add (or replace) single showcase files in the existing project, keeping every other file and its edits."""
    proj = store.get_project(PID)
    by_rel = {m["rel_path"]: m for m in store.list_media(PID)}
    for fn in builders:
        path, an = fn()
        rel = str(path.relative_to(OUT))
        if rel in by_rel:
            store.remove_media(PID, by_rel[rel]["id"])
        item = _new_item(path, rel, "linked")
        store.add_media(PID, [item])
        store.save_doc(PID, item["id"], an.doc())
        print(f"  {rel}: {len(an.segs)} pseudo labels")
    missing = [t for t in TASKS if t not in proj["tasks"]]
    if missing:
        store.update_project(PID, {"tasks": proj["tasks"] + missing})
        print("  added tasks:", ", ".join(missing))


BUILDERS = {"interview": build_interview, "call": build_call, "panel": build_panel, "japanese": build_japanese,
            "debate": build_debate, "lecture": build_lecture, "meeting": build_meeting, "street": build_street}


def main():
    if "--add" in sys.argv:  # e.g. --add debate
        add_files([BUILDERS[n] for n in sys.argv[sys.argv.index("--add") + 1:]])
        return
    print("generating media in", OUT)
    built = []
    for fn in BUILDERS.values():
        path, an = fn()
        built.append((path, an, False))
        print(f"  {path.relative_to(OUT)}: {len(an.segs)} pseudo labels")
    for path, an, rev in build_images() + [(*build_pose_park(), False)]:
        built.append((path, an, rev))
        print(f"  {path.relative_to(OUT)}: {len(an.segs)} pseudo labels")

    if PID in {p["id"] for p in store.list_projects()}:
        store.delete_project(PID)
    p = store.create_project("Pseudo showcase", (
        "Synthetic files to preview the UI: speech made with the macOS voices (podcast, noisy support call, "
        "multilingual panel, Japanese weather), generated videos (lecture, team meeting, street) and images. "
        "All labels are pseudo labels written from the generation scripts."), TASKS)
    assert p["id"] == PID
    for path, an, reviewed in built:
        item = _new_item(path, str(path.relative_to(OUT)), "linked")
        store.add_media(PID, [item])
        store.save_doc(PID, item["id"], an.doc())
        if reviewed:
            store.update_media(PID, item["id"], {"reviewed": True})
    print("done ->", f"http://127.0.0.1:8710/#/p/{PID}")


if __name__ == "__main__":
    main()
