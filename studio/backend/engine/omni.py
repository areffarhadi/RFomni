"""Omni engine: the fine-tuned mix87 annotation model (its thinker) served by vLLM-omni.

How a task reaches the model:
  * Tasks the thinker was trained on (ASR, LID, NER, gender) use the trained short prompt
    ("JSON only. audio annotation Tasks: ASR. Keys: ASR."). Asking for many keys in one
    call truncates the transcript, so keys are grouped small and cached per region.
  * Every other task, and any built-in task whose prompt the user changed, is sent as the
    task prompt plus its output spec (tasks.build_prompt) with the media attached, so the
    edits made on the Tasks page are what the model sees.
  * Tasks a mix87 sidecar head was trained for (speakers, voice gender, audio spoof,
    phonemes, end of turn, speaker count, body parts, pose, face spoof/deepfake, head pose,
    video spoof/deepfake) go to the heads service (engine/heads.py), which runs the heads on
    the same model's frozen towers. Speech regions come from Silero VAD in that service. Where a head finds something and words are
    needed (a face's expression, the kind of spoof), the thinker names it.
  * Tasks nothing here can answer yet (forced alignment, shot cuts), and head-only tasks
    while the heads service is down, raise UnsupportedTask instead of producing values.

Audio is cut on speech regions (energy VAD), images go as JPEG, video tasks get frames or,
at file level, the video itself. Text documents go as text: whole-document tasks read the start of the
document; span tasks (entities, personal data) quote the words, which are then found in the document and
stored as character offsets. Entities use the trained short prompt ("JSON only. text annotation Tasks: NER."). Settings: OMNI_ENGINE_URL, OMNI_ENGINE_MODEL,
OMNI_ENGINE_CONCURRENCY (requests in flight, kept low so live voice stays responsive),
OMNI_ENGINE_TIMEOUT.
"""
import ast
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageOps

from .. import media as media_mod
from .. import tasks as task_reg
from ..config import SR
from ..speakers import speaker_regions
from . import heads
from .base import AnnotateContext, Engine
from .mock import energy_vad

URL = os.environ.get("OMNI_ENGINE_URL", "http://127.0.0.1:8410/v1").rstrip("/")
MODEL = os.environ.get("OMNI_ENGINE_MODEL", "mix87")
CONCURRENCY = max(1, int(os.environ.get("OMNI_ENGINE_CONCURRENCY", "2")))
TIMEOUT = float(os.environ.get("OMNI_ENGINE_TIMEOUT", "120"))
MAX_VIDEO_BYTES = 25 * 1024 * 1024  # larger videos are sent as frames
EVENT_CHUNK = 30.0                  # event tasks: the model lists events per 30 s of audio
TEXT_CHUNK = 2500                   # text spans and long translations: characters per request
TEXT_FILE_MAX = 12000               # a whole-document text task reads at most this many characters
TEXT_PER_CHUNK = {"text_translation", "text_correction"}  # output as long as the input: done chunk by chunk

# Trained short-prompt keys per task. LID rides along with ASR when both run. GEN alone
# comes back as a voice description; asked together with ASR and EMO it is a label.
TRAINED = {"asr": ("ASR",), "lid": ("ASR", "LID"), "ner": ("ASR", "NER"), "gender": ("ASR", "EMO", "GEN")}
# Needs a sidecar head (mix87 heads bundle) that is not served on this machine yet.
HEAD_ONLY = {"forced_alignment": "forced-alignment read-off of the full (unquantised) thinker",
             "shots": "shot-cut detector"}
# task -> head of the heads service. Tasks marked * have no thinker fallback.
AUDIO_HEADS = {"vad": "vad", "diarization": "diarization", "pure_speech": "diarization", "overlap": "diarization",
               "gender": "gender", "spoof_audio": "spoof", "phoneme": "phonemes", "speaker_count": "speaker_count",
               "turn_end": "end_of_turn"}
IMAGE_HEADS = {"person_boxes": "boxes", "body_parts": "boxes", "face_boxes": "boxes", "pose": "pose",
               "spoof_image": "face_spoof", "spoof_regions": "face_spoof", "head_pose": "head_pose"}
VIDEO_HEADS = {"spoof_video": "video_spoof"}
NEEDS_HEADS = {"diarization", "pure_speech", "overlap", "phoneme", "speaker_count", "turn_end", "body_parts",
               "pose", "head_pose"}

SYNONYMS = {
    "happiness": "happy", "joy": "happy", "sadness": "sad", "anger": "angry", "fear": "fearful",
    "neu": "neutral", "hap": "happy", "ang": "angry",
    "f": "female", "m": "male", "woman": "female", "man": "male",
    "adult female": "female", "adult male": "male", "excellent": "good",
}
LID_CODES = {
    "english": "en", "german": "de", "french": "fr", "italian": "it", "spanish": "es", "persian": "fa",
    "arabic": "ar", "japanese": "ja", "korean": "ko", "mandarin chinese": "zh", "chinese": "zh",
    "russian": "ru", "portuguese": "pt", "dutch": "nl", "turkish": "tr", "hindi": "hi", "polish": "pl",
    "swedish": "sv", "greek": "el", "cantonese": "yue", "indonesian": "id", "thai": "th",
}

_slots = threading.BoundedSemaphore(CONCURRENCY)


class UnsupportedTask(Exception):
    """The engine cannot produce this task yet; the job reports it and goes on."""


# ----------------------------------------------------------------------------- model I/O
def chat(content: list, max_tokens: int = 400) -> str:
    body = {"model": MODEL, "modalities": ["text"], "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(f"{URL}/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with _slots:
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    return (json.load(r)["choices"][0]["message"].get("content") or "").strip()
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"model returned {e.code}: {e.read()[:200].decode(errors='replace')}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt:
                    raise RuntimeError(f"model not reachable at {URL}: {e}") from e
                time.sleep(1.0)


def parse_json(txt: str):
    """First JSON object in the reply (tolerates code fences, trailing prose and the
    Python-dict quoting the thinker sometimes uses)."""
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        pass
    start = txt.find("{")
    if start >= 0:
        try:
            dec, objs, i = json.JSONDecoder(), [], start
            while i < len(txt):
                obj, end = dec.raw_decode(txt, i)
                objs.append(obj)
                i = end
                while i < len(txt) and txt[i] in " \t\r\n,":
                    i += 1
                if i < len(txt) and txt[i] != "{":
                    break
            # one object per paragraph ({"text": ...}{"text": ...}): a long text answer, joined
            if len(objs) > 1 and all(isinstance(o, dict) and list(o) == ["text"] for o in objs):
                return {"text": "\n\n".join(str(o["text"]) for o in objs)}
            return objs[0]
        except json.JSONDecodeError:
            if 'objs' in locals() and objs:
                return objs[0]
        try:
            obj = ast.literal_eval(txt[start: txt.rfind("}") + 1])
            return obj if isinstance(obj, dict) else None
        except (ValueError, SyntaxError):
            pass
    return _salvage(txt)


def _salvage(txt: str):
    """Near-JSON the thinker sometimes writes (a stray quote after a number, a missing brace): pull out the
    one key a task asked for."""
    out = {}
    m = re.search(r'"label"\s*:\s*"([^"]+)"', txt)
    if m:
        out["label"] = m.group(1)
    m = re.search(r'"(?:score|confidence)"\s*:\s*"?(-?[0-9.]+)', txt)
    if m and "label" not in out:
        out["score"] = m.group(1)
    m = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
    if m and not out:
        out["text"] = m.group(1)
    return out or None


def parse_keyed(txt: str, keys: tuple) -> dict:
    """Short-prompt replies, whatever shape they arrive in: JSON, "ASR: ..." lines,
    "[ASR]" headers, or bare text (taken as the first key, normally ASR)."""
    obj = parse_json(txt)
    if isinstance(obj, dict):
        return {k.upper(): v for k, v in obj.items()}
    found, cur = {}, None
    for line in txt.splitlines():
        m = re.match(r"^\s*\[?([A-Za-z]{2,8})\]?\s*[:\]]\s*(.*)$", line) or re.match(r"^\s*\[([A-Za-z]{2,8})\]\s*$", line)
        if m and m.group(1).upper() in keys:
            cur = m.group(1).upper()
            found[cur] = (m.group(2) if m.lastindex and m.lastindex > 1 else "").strip()
        elif cur and line.strip():
            found[cur] = (found[cur] + " " + line.strip()).strip()
    if found:
        return found
    bare = txt.strip().strip("[]'\"")
    return {keys[0]: bare} if bare else {}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def audio_part(x) -> dict:
    return {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64," + _b64(media_mod.wav_bytes(x))}}


def image_part(img: Image.Image, side: int = 1024) -> dict:
    img = img.copy()
    img.thumbnail((side, side))
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _b64(media_mod.jpeg_bytes(img, 88))}}


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


# ----------------------------------------------------------------------------- values
def _match_label(raw: str, labels: list):
    """Map a model label onto the task's classes. Returns (value, on_list)."""
    v = str(raw).strip()
    if not labels:
        return v, True
    low = {l.lower(): l for l in labels}
    k = v.lower()
    for cand in (k, SYNONYMS.get(k, k), LID_CODES.get(k, k)):
        if cand in low:
            return low[cand], True
    return v, False


def _confidence(obj) -> float | None:
    c = obj.get("confidence") if isinstance(obj, dict) else None
    try:
        c = float(c)
    except (TypeError, ValueError):
        return None
    return round(c, 3) if 0.0 <= c <= 1.0 else None


NER_TYPES = {"person": "PERSON", "per": "PERSON", "people": "PERSON", "org": "ORG", "organization": "ORG",
             "organisation": "ORG", "company": "ORG", "loc": "LOC", "location": "LOC", "place": "LOC", "facility": "LOC",
             "fac": "LOC", "gpe": "GPE", "country": "GPE", "city": "GPE", "state": "GPE", "date": "DATE", "time": "DATE",
             "event": "EVENT", "misc": "MISC", "norp": "MISC", "nationality": "MISC", "product": "MISC",
             "language": "MISC", "work_of_art": "MISC", "law": "MISC", "money": "MISC", "percent": "MISC",
             "quantity": "MISC", "cardinal": "MISC", "ordinal": "MISC"}


def ner_pairs(v) -> list:
    """(entity, type) from every shape the thinker emits: {type: [entities]}, {entity: type},
    {entity: [type]}, [{text, type}], ["entity [TYPE]"]."""
    out = []
    if isinstance(v, dict):
        for k, val in v.items():
            vals = val if isinstance(val, list) else [val]
            if str(k).lower() in NER_TYPES:
                out += [(str(e), str(k)) for e in vals if isinstance(e, str)]
            elif vals and all(isinstance(x, str) and x.lower() in NER_TYPES for x in vals):
                out.append((str(k), str(vals[0])))
    elif isinstance(v, list):
        for e in v:
            if isinstance(e, dict) and "text" in e:
                out.append((str(e["text"]), str(e.get("type") or e.get("label") or "MISC")))
            elif isinstance(e, dict):
                out += [(str(t), str(typ)) for t, typ in e.items() if isinstance(typ, str)]
            elif isinstance(e, str):
                m = re.match(r"^(.*?)\s*\[([\w ]+)\]\s*$", e)
                out.append((m.group(1), m.group(2)) if m else (e, "MISC"))
    return [(e.strip(), t.strip()) for e, t in out if e.strip()]


def _ner_items(v) -> list:
    """NER in any of the shapes the thinker emits -> ["Karen [PERSON]", ...]."""
    return [f"{e} [{t}]" for e, t in ner_pairs(v)]


def _chunks(doc: str, size: int) -> list:
    """(offset, piece) pieces of at most `size` characters, cut after a paragraph, a sentence or a space."""
    out, i = [], 0
    while i < len(doc):
        j = min(len(doc), i + size)
        if j < len(doc):
            cut = max(doc.rfind("\n", i + size // 2, j), doc.rfind(". ", i + size // 2, j))
            if cut <= i:
                cut = doc.rfind(" ", i + size // 2, j)
            j = cut + 1 if cut > i else j
        out.append((i, doc[i:j]))
        i = j
    return out


def _find_all(doc: str, phrase: str) -> list:
    """Every (start, end) where the quoted phrase occurs: exact first, then ignoring case and runs of spaces."""
    phrase = phrase.strip().strip('"').strip()
    if not phrase:
        return []
    pat = r"\s+".join(re.escape(w) for w in phrase.split())
    if re.match(r"\w", phrase[0]):
        pat = r"(?<!\w)" + pat
    if re.match(r"\w", phrase[-1]):
        pat += r"(?!\w)"
    hits = [m.span() for m in re.finditer(pat, doc)]
    return hits or [m.span() for m in re.finditer(pat, doc, re.IGNORECASE)]


def _first_value(obj, *keys):
    if not isinstance(obj, dict):
        return obj
    for k in keys:
        for kk in (k, k.lower(), k.upper()):
            if kk in obj:
                return obj[kk]
    return next(iter(obj.values()), None) if len(obj) == 1 else None


def _scalar(v):
    """Short-prompt keys sometimes come back wrapped: ["English"] or {"NEU": [...]}."""
    if isinstance(v, list):
        return _scalar(v[0]) if v else ""
    if isinstance(v, dict):
        return next(iter(v.keys()), "")
    return v


def value_from_generic(task: dict, obj) -> dict | None:
    """Turn the JSON a task prompt asked for into a segment value."""
    if obj is None:
        return None
    out, labels = task["output"], task.get("labels") or []
    if out == "label":
        raw = _scalar(_first_value(obj, "label"))
        if raw in (None, ""):
            return None
        v, ok = _match_label(raw, labels)
        return {"value": v, "confidence": _confidence(obj), "meta": None if ok else {"off_list": True}}
    if out == "tags":
        raw = _first_value(obj, "tags")
        if task["id"] == "ner":
            return {"value": _ner_items(raw), "confidence": None}
        tags = [str(t) for t in (raw if isinstance(raw, list) else [raw] if raw else [])]
        if labels:
            tags = [m for m, ok in (_match_label(t, labels) for t in tags) if ok]
        return {"value": tags, "confidence": None}
    if out == "score":
        raw = _scalar(_first_value(obj, "score"))
        try:
            x = float(raw)
        except (TypeError, ValueError):
            return None
        lo, hi = float(task.get("score_min", 1)), float(task.get("score_max", 5))
        return {"value": f"{min(hi, max(lo, x)):g}", "confidence": None}
    raw = _first_value(obj, "text")
    if isinstance(raw, (dict, list)):
        raw = json.dumps(raw, ensure_ascii=False)
    return {"value": str(raw or "").strip(), "confidence": _confidence(obj)} if raw else None


# ----------------------------------------------------------------------------- engine
class OmniEngine(Engine):
    """The fine-tuned annotation model (mix87, shown to users as RFomni), served by vLLM-omni."""
    name = "omni"
    title = "RFomni"
    source = "model"

    def __init__(self):
        self._probe = {"at": 0.0, "ok": False}

    @property
    def connected(self) -> bool:
        if time.time() - self._probe["at"] > 30:
            try:
                with urllib.request.urlopen(f"{URL}/models", timeout=3) as r:
                    self._probe["ok"] = r.status == 200
            except Exception:
                self._probe["ok"] = False
            self._probe["at"] = time.time()
        return self._probe["ok"]

    def info(self) -> dict:
        h = heads.info()
        return {**super().info(), "url": URL, "model": MODEL, "unsupported": sorted(HEAD_ONLY),
                "heads": {"url": heads.URL, "audio": bool(h and h.get("audio")), "vision": bool(h and h.get("vision")),
                          "device": h.get("gpu_name") if h else None}}

    # ---------------- spans ----------------
    def _speech(self, ctx: AnnotateContext) -> list:
        """Speech regions: Silero VAD (heads service) when it is up, else energy VAD."""
        if "speech" not in ctx.cache:
            spans = []
            if len(ctx.audio):
                res = self._audio_heads(ctx, "vad")
                spans = [(s["start"], s["end"]) for s in res["vad"]] if res else energy_vad(ctx.audio)
            ctx.cache["speech"] = spans
        return ctx.cache["speech"]

    @staticmethod
    def _windows(dur: float, win: float) -> list:
        out, t = [], 0.0
        while t < dur - 0.05:
            out.append((round(t, 3), round(min(dur, t + win), 3)))
            t += win
        return out

    def _clip(self, ctx, a, b):
        return ctx.audio[int(a * SR): int(b * SR)]

    def _frame(self, ctx, t: float) -> Image.Image:
        key = ("frame", round(t, 2))
        if key not in ctx.cache:
            ctx.cache[key] = media_mod.video_frame(ctx.path, t, height=0)
        return ctx.cache[key]

    def _image(self, ctx) -> Image.Image:
        if "image" not in ctx.cache:
            with Image.open(ctx.path) as im:
                ctx.cache["image"] = ImageOps.exif_transpose(im).convert("RGB")
        return ctx.cache["image"]

    # ---------------- media parts for one region ----------------
    def _visual(self, task: dict, ctx) -> bool:
        """Video tasks without an audio modality look at the picture, not the sound."""
        return ctx.media["modality"] == "image" or "audio" not in task.get("modalities", [])

    def _parts(self, task, ctx, a, b) -> list:
        mod = ctx.media["modality"]
        if mod == "image":
            return [image_part(self._image(ctx))]
        if self._visual(task, ctx):
            if task.get("granularity") == "file" and a == 0 and ctx.path.stat().st_size <= MAX_VIDEO_BYTES:
                return [{"type": "video_url", "video_url": {
                    "url": "data:video/mp4;base64," + _b64(ctx.path.read_bytes())}}]
            n = 1 if b - a <= 6 else 3
            return [image_part(self._frame(ctx, a + (b - a) * (i + 0.5) / n), 768) for i in range(n)]
        return [audio_part(self._clip(ctx, a, b))]

    # ---------------- calls ----------------
    def _trained(self, ctx, a, b, keys: tuple) -> dict:
        cache = ctx.cache.setdefault("trained", {})
        k = (a, b, keys)
        if k not in cache:
            ks = ", ".join(keys)
            txt = chat([audio_part(self._clip(ctx, a, b)), text_part(f"JSON only. audio annotation Tasks: {ks}. Keys: {ks}.")])
            cache[k] = parse_keyed(txt, keys)
        return cache[k]

    def _uses_trained(self, task, ctx) -> bool:
        edited = task.get("modified") and "prompt" in (task.get("modified_fields") or [])
        return task["id"] in TRAINED and not edited and not self._visual(task, ctx)

    def _trained_value(self, task, ctx, a, b) -> dict | None:
        keys = TRAINED[task["id"]]
        if task["id"] == "asr" and "lid" in getattr(ctx, "run_task_ids", ()):
            keys = TRAINED["lid"]  # one call answers both
        obj = self._trained(ctx, a, b, keys)
        own = {"asr": "ASR", "lid": "LID", "ner": "NER", "gender": "GEN"}[task["id"]]
        raw = obj.get(own)
        if raw is None and task["id"] == "asr":
            raw = obj.get("TEXT")
        if task["id"] == "ner":
            return {"value": _ner_items(raw), "confidence": None} if raw is not None else None
        raw = _scalar(raw)
        if raw in (None, ""):
            return None
        if task["output"] == "text":
            return {"value": str(raw).strip(), "confidence": None}
        v, ok = _match_label(raw, task.get("labels") or [])
        return {"value": v, "confidence": None, "meta": None if ok else {"off_list": True}}

    def _generic_value(self, task, ctx, a, b) -> dict | None:
        txt = chat(self._parts(task, ctx, a, b) + [text_part(task_reg.build_prompt(task))])
        return value_from_generic(task, parse_json(txt))

    def _region(self, task, ctx, a, b):
        if getattr(ctx, "should_stop", lambda: False)():
            return None
        try:
            if self._uses_trained(task, ctx):
                val = self._trained_value(task, ctx, a, b)
            else:
                val = self._generic_value(task, ctx, a, b)
        except RuntimeError:
            raise
        except Exception as e:  # one bad region should not sink the file
            return {"error": str(e)}
        if not val or val.get("value") in ("", [], None):
            return None
        seg = {"start": a, "end": b, "value": val["value"], "confidence": val.get("confidence")}
        if val.get("meta"):
            seg["meta"] = val["meta"]
        return seg

    def _regions(self, task, ctx, spans) -> list:
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(pool.map(lambda s: self._region(task, ctx, *s), spans))
        segs = [r for r in results if r and "error" not in r]
        errors = [r["error"] for r in results if r and "error" in r]
        if errors and not segs:
            raise RuntimeError(f"{task['name']}: {errors[0]}")
        return segs

    # ---------------- boxes ----------------
    def _boxes(self, task, ctx) -> list:
        obj = parse_json(chat([image_part(self._image(ctx)), text_part(task_reg.build_prompt(task))], 800)) or {}
        labels = task.get("labels") or []
        out, seen = [], set()
        for b in obj.get("boxes") or []:
            box = b.get("box_2d") if isinstance(b, dict) else None
            if not (isinstance(box, list) and len(box) == 4):
                continue
            x1, y1, x2, y2 = (max(0.0, min(1000.0, float(v))) / 1000 for v in box)
            if x2 <= x1 or y2 <= y1:
                continue
            label, ok = _match_label(b.get("label", ""), labels)
            key = tuple(round(v, 3) for v in (x1, y1, x2, y2))
            if key in seen:  # the model sometimes repeats one box with different "labels"
                continue
            seen.add(key)
            out.append({"start": None, "end": None, "value": label if ok else "",
                        "confidence": _confidence(b),
                        "geom": {"type": "box", "x": round(x1, 4), "y": round(y1, 4),
                                 "w": round(x2 - x1, 4), "h": round(y2 - y1, 4)},
                        **({"meta": {"model_label": b.get("label")}} if not ok else {})})
        return out

    # ---------------- events ----------------
    def _events(self, task, ctx) -> list:
        """The output spec asks for {"items": [{start, end, value}]}; times are per chunk."""
        out = []
        for a, b in self._windows(ctx.duration, EVENT_CHUNK):
            obj = parse_json(chat(self._parts(task, ctx, a, b) + [text_part(task_reg.build_prompt(task))], 600)) or {}
            for it in obj.get("items") or []:
                try:
                    s, e = float(it["start"]) + a, float(it["end"]) + a
                except (KeyError, TypeError, ValueError):
                    continue
                if e <= s:
                    continue
                val = value_from_generic(task, {"label": it.get("value"), "tags": it.get("value"),
                                                "text": it.get("value"), "score": it.get("value")})
                if val and val["value"] not in ("", []):
                    out.append({"start": round(s, 3), "end": round(min(e, b), 3), "value": val["value"],
                                "confidence": val.get("confidence")})
        return out

    # ---------------- sidecar heads ----------------
    def _audio_heads(self, ctx, head: str):
        """One heads-service call per file answers every audio head this run needs."""
        cache = ctx.cache.setdefault("heads_audio", {})
        if head not in cache:
            if not heads.available("audio") or not len(ctx.audio):
                return None
            run = getattr(ctx, "run_task_ids", None) or set()
            want = {"vad", head} | {AUDIO_HEADS[t] for t in run if t in AUDIO_HEADS}
            res = heads.audio(ctx.audio, sorted(want))
            for k in want:
                cache[k] = res
        return cache[head]

    def _image_heads(self, ctx, head: str, options=None):
        key = ("heads_image", head, json.dumps(options or {}, sort_keys=True))
        if key not in ctx.cache:
            ctx.cache[key] = heads.image(ctx.path, [head], options) if heads.available("vision") else None
        return ctx.cache[key]

    def _name_it(self, img: Image.Image, question: str, labels: list):
        """Ask the thinker to name what a head found (one label from `labels`)."""
        if not labels:
            return None, None
        prompt = (f"{question} Choose exactly one of [{', '.join(labels)}]. "
                  'Return only JSON: {"label": "<label>", "confidence": <0-1>}')
        obj = parse_json(chat([image_part(img, 512), text_part(prompt)], 60)) or {}
        v, ok = _match_label(_scalar(_first_value(obj, "label")) or "", labels)
        return (v, _confidence(obj)) if ok else (None, None)

    def _crop(self, ctx, box, pad=0.15) -> Image.Image:
        img = self._image(ctx)
        w, h = img.size
        x1, y1, x2, y2 = box
        dx, dy = (x2 - x1) * pad, (y2 - y1) * pad
        return img.crop((max(0, (x1 - dx) * w), max(0, (y1 - dy) * h), min(w, (x2 + dx) * w), min(h, (y2 + dy) * h)))

    @staticmethod
    def _geom_box(b) -> dict:
        return {"type": "box", "x": b[0], "y": b[1], "w": round(b[2] - b[0], 4), "h": round(b[3] - b[1], 4)}

    @staticmethod
    def _sig(x) -> float:
        return round(1 / (1 + 2.718281828 ** (-x)), 3)

    def _from_audio_heads(self, task, ctx):
        tid, labels = task["id"], task.get("labels") or []
        res = self._audio_heads(ctx, AUDIO_HEADS[tid])
        if res is None:
            return None
        meta = {"by": "RFomni head"}
        if tid == "vad":
            label = labels[0] if labels else "speech"
            return [{"start": s["start"], "end": s["end"], "value": label, "confidence": s["score"],
                     "meta": {"by": "silero-vad"}} for s in res["vad"]]
        if tid == "diarization":
            return [{"start": t["start"], "end": t["end"], "value": t["speaker"], "confidence": None, "meta": meta}
                    for t in res["diarization"]]
        if tid in ("pure_speech", "overlap"):
            turns = [s for s in ctx.tier_segments("diarization") if s.get("start") is not None] or \
                    [{"start": t["start"], "end": t["end"], "value": t["speaker"], "confidence": None} for t in res["diarization"]]
            pure, over = speaker_regions(turns)
            return [{k: r[k] for k in ("start", "end", "value", "confidence")} for r in (pure if tid == "pure_speech" else over)]
        if tid == "phoneme":
            return [{**p, "confidence": None} for p in res["phonemes"]]
        if tid == "speaker_count":
            sc = res.get("speaker_count")
            if not sc:
                return []
            v, ok = _match_label(str(sc["n_speakers"]), labels)
            return [{"start": 0.0, "end": round(ctx.duration, 3), "value": v, "confidence": None,
                     "meta": {**meta, "windows": sc["n_windows"]}}]
        out = []
        for r in res[AUDIO_HEADS[tid]]:
            seg = {"start": r["start"], "end": r["end"], "confidence": r.get("confidence"), "meta": dict(meta)}
            if tid == "spoof_audio" and r["label"] == "spoof":
                seg["meta"]["head_score"] = r["score"]
                kinds = [l for l in labels if l.lower() != "bonafide"]
                txt = chat([audio_part(self._clip(ctx, r["start"], r["end"])), text_part(
                    "This speech is not genuine live human speech. How was it made? Choose exactly one of "
                    f"[{', '.join(kinds)}]. " + 'Return only JSON: {"label": "<label>", "confidence": <0-1>}')], 60)
                v, ok = _match_label(_scalar(_first_value(parse_json(txt) or {}, "label")) or "", kinds)
                seg["value"] = v if ok else "spoof"
                if not ok:
                    seg["meta"]["off_list"] = True
            else:
                if tid == "turn_end":
                    seg["meta"]["p_complete"] = r["p_complete"]
                v, ok = _match_label(r["label"], labels)
                seg["value"] = v
                if not ok:
                    seg["meta"]["off_list"] = True
            out.append(seg)
        return out

    def _from_image_heads(self, task, ctx):
        tid, labels = task["id"], task.get("labels") or []
        meta = {"by": "RFomni head"}
        if tid in ("person_boxes", "body_parts", "face_boxes"):
            classes = {"person_boxes": ["person"], "face_boxes": ["face"]}.get(tid, labels or None)
            res = self._image_heads(ctx, "boxes", {"classes": classes, "score_thresh": 0.3})
            if res is None:
                return None
            out = []
            for b in res["boxes"]:
                value, conf = b["label"], b["score"]
                if tid == "face_boxes":  # the head finds the face, the thinker reads its expression
                    value, _ = self._name_it(self._crop(ctx, b["box"]), "What is the facial expression of this face?", labels)
                    value = value or ""
                elif labels:
                    value, ok = _match_label(value, labels)
                    value = value if ok else ""
                out.append({"start": None, "end": None, "value": value, "confidence": conf,
                            "geom": self._geom_box(b["box"]), "meta": dict(meta)})
            return out
        if tid == "pose":
            res = self._image_heads(ctx, "pose", {"score_thresh": 0.3})
            if res is None:
                return None
            label = labels[0] if labels else "person"
            return [{"start": None, "end": None, "value": label, "confidence": p["score"], "meta": meta,
                     "geom": {"type": "keypoints", "skeleton": "coco17",
                              "points": [[x, y, 2 if vis else 1] for (x, y, _), vis in zip(p["keypoints"], p["visible"])],
                              "box": [p["box"][0], p["box"][1], round(p["box"][2] - p["box"][0], 4),
                                      round(p["box"][3] - p["box"][1], 4)]}} for p in res["pose"]]
        if tid == "head_pose":
            res = self._image_heads(ctx, "head_pose")
            if res is None:
                return None
            hp = res.get("head_pose")
            if not hp:
                return []
            return [{"start": None, "end": None, "confidence": hp.get("conf"),
                     "value": f"yaw {hp['yaw']:.0f}°, pitch {hp['pitch']:.0f}°, roll {hp['roll']:.0f}°",
                     "meta": {**meta, "yaw": hp["yaw"], "pitch": hp["pitch"], "roll": hp["roll"]}}]
        res = self._image_heads(ctx, "face_spoof")
        if res is None:
            return None
        fs = res["face_spoof"]
        if tid == "spoof_regions":
            return [{"start": None, "end": None, "value": _match_label("face swap", labels)[0],
                     "confidence": self._sig(f["score"] - fs["thresholds"]["deepfakes"]),
                     "geom": self._geom_box(f["box"]), "meta": {**meta, "head_score": f["score"]}}
                    for f in fs["faces"] if f["deepfake"]]
        if not fs["found_face"]:
            return None  # the head reads faces; let the thinker judge face-less images
        kind = "face swap" if fs["deepfake"] else "recaptured" if fs["presentation_attack"] else "real"
        v, ok = _match_label(kind, labels)
        thr = fs["thresholds"]["deepfakes"] if kind == "face swap" else fs["thresholds"]["fas"]
        conf = self._sig(abs(fs["score"] - thr))
        return [{"start": None, "end": None, "value": v, "confidence": conf,
                 "meta": {**meta, "head_score": fs["score"], **({} if ok else {"off_list": True})}}]

    def _from_video_heads(self, task, ctx):
        """spoof_video: per-window decision from the per-frame head scores."""
        if not heads.available("vision"):
            return None
        labels = task.get("labels") or []
        key = "heads_video_spoof"
        if key not in ctx.cache:
            ctx.cache[key] = heads.video(ctx.path, ["video_spoof"], {"fps": 2.0, "max_frames": 600})["video_spoof"]
        vs = ctx.cache[key]
        th = vs["thresholds"]
        out = []
        for a, b in self._windows(ctx.duration, float(task.get("window_sec", 4))):
            fr = [f for f in vs["frames"] if a <= f["t"] < b and f["face"]]
            if not fr:
                continue
            df = sum(f["deepfake"] for f in fr) / len(fr)
            sp = sum(f["spoof"] for f in fr) / len(fr)
            meta = {"by": "RFomni head", "deepfake_score": round(df, 3), "spoof_score": round(sp, 3)}
            if df > th.get("deepfake", 0.0):
                kinds = [l for l in labels if l.lower() != "real"]
                v, conf = self._name_it(self._frame(ctx, (a + b) / 2), "This face video is a deepfake. What kind of manipulation is it?", kinds)
                v = v or (kinds[0] if kinds else "deepfake")
                conf = self._sig(df - th.get("deepfake", 0.0))
            elif sp > th.get("spoof", 0.0):
                v, conf = "presentation attack", self._sig(sp - th.get("spoof", 0.0))
                v, ok = _match_label(v, labels)
                if not ok:
                    meta["off_list"] = True
            else:
                v, conf = _match_label("real", labels)[0], self._sig(-max(df - th.get("deepfake", 0.0), sp - th.get("spoof", 0.0)))
            out.append({"start": a, "end": b, "value": v, "confidence": conf, "meta": meta})
        return out

    def _from_heads(self, task, ctx):
        """Segments from the sidecar heads, or None when this task/media is not theirs (or the
        service is down)."""
        tid, mod = task["id"], ctx.media["modality"]
        if tid in AUDIO_HEADS and mod in ("audio", "video"):
            return self._from_audio_heads(task, ctx)
        if tid in IMAGE_HEADS and mod == "image":
            return self._from_image_heads(task, ctx)
        if tid in VIDEO_HEADS and mod == "video":
            return self._from_video_heads(task, ctx)
        return None

    # ---------------- text ----------------
    def _doc_text(self, ctx) -> str:
        if "text" not in ctx.cache:
            ctx.cache["text"] = media_mod.read_text(ctx.path)
        return ctx.cache["text"]

    def _text(self, task, ctx) -> list:
        doc = self._doc_text(ctx)
        if not doc.strip():
            return []
        if task.get("granularity") == "span":
            return self._text_spans(task, ctx, doc)
        prompt = task_reg.build_prompt(task)
        long_out = task.get("output") == "text" and task["id"] in TEXT_PER_CHUNK
        if long_out and len(doc) > TEXT_CHUNK:  # a translation or correction as long as the document
            parts = []
            for _, piece in _chunks(doc, TEXT_CHUNK):
                if getattr(ctx, "should_stop", lambda: False)():
                    return []
                v = value_from_generic(task, parse_json(chat([text_part(f"Text:\n{piece}"), text_part(prompt)], 2000)))
                parts.append((v or {}).get("value") or "")
            return [{"start": None, "end": None, "value": "\n\n".join(p for p in parts if p), "confidence": None}]
        body = doc[:TEXT_FILE_MAX]
        tokens = 2000 if long_out else 600 if task.get("output") == "text" else 200
        v = value_from_generic(task, parse_json(chat([text_part(f"Text:\n{body}"), text_part(prompt)], tokens)))
        if not v or v.get("value") in ("", [], None):
            return []
        meta = dict(v.get("meta") or {})
        if len(doc) > TEXT_FILE_MAX:
            meta["read_chars"] = TEXT_FILE_MAX  # the model saw the start of a long document
        seg = {"start": None, "end": None, "value": v["value"], "confidence": v.get("confidence")}
        if meta:
            seg["meta"] = meta
        return [seg]

    def _text_spans(self, task, ctx, doc) -> list:
        """Quoted words found in the document, as character offsets (start inclusive, end exclusive)."""
        labels = task.get("labels") or []
        edited = task.get("modified") and "prompt" in (task.get("modified_fields") or [])
        trained = task["id"] == "text_ner" and not edited
        prompt = task_reg.build_prompt(task)

        def one(piece):
            off, txt = piece
            if getattr(ctx, "should_stop", lambda: False)():
                return off, txt, []
            if trained:
                obj = parse_json(chat([text_part(f"JSON only. text annotation Tasks: NER. Keys: NER.\n{txt}")], 800)) or {}
                pairs = [(e, NER_TYPES.get(t.lower(), t.upper())) for e, t in ner_pairs(obj.get("NER", obj))]
            else:
                obj = parse_json(chat([text_part(f"Text:\n{txt}"), text_part(prompt)], 1000)) or {}
                pairs = [(str(it.get("text") or ""), it.get("value")) for it in obj.get("items") or [] if isinstance(it, dict)]
            return off, txt, pairs

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(pool.map(one, _chunks(doc, TEXT_CHUNK)))
        out, taken = [], []
        for off, piece, pairs in results:
            for phrase, raw in pairs:
                v = value_from_generic(task, {"label": raw, "tags": raw, "text": raw, "score": raw})
                if not v or v.get("value") in ("", [], None):
                    continue
                if labels and task.get("output") == "label" and (v.get("meta") or {}).get("off_list"):
                    v = {**v, "value": "MISC" if "MISC" in labels else v["value"]}
                for a, b in _find_all(piece, phrase):
                    a, b = a + off, b + off
                    if any(a < y and x < b for x, y in taken):  # one mark per stretch of text
                        continue
                    taken.append((a, b))
                    seg = {"start": a, "end": b, "value": v["value"], "confidence": v.get("confidence"),
                           "meta": {"text": doc[a:b], **(v.get("meta") or {})}}
                    out.append(seg)
        return sorted(out, key=lambda x: x["start"])

    # ---------------- main ----------------
    def annotate(self, task: dict, ctx: AnnotateContext) -> list:
        tid, mod, out = task["id"], ctx.media["modality"], task.get("output")
        if tid in HEAD_ONLY:
            raise UnsupportedTask(f"{task['name']} needs the {HEAD_ONLY[tid]}, which is not available here")
        if mod == "text":
            return self._text(task, ctx)
        segs = self._from_heads(task, ctx)
        if segs is not None:
            return segs
        if tid in NEEDS_HEADS or out == "keypoints":
            if mod == "image" and tid in AUDIO_HEADS or mod != "image" and tid in IMAGE_HEADS:
                return []
            raise UnsupportedTask(f"{task['name']} runs on the RFomni sidecar heads; the heads service "
                                  f"({heads.URL}) is not running")
        if out == "boxes":
            return self._boxes(task, ctx) if mod == "image" else []
        if tid == "vad":
            label = (task.get("labels") or ["speech"])[0]
            return [{"start": a, "end": b, "value": label, "confidence": None, "meta": {"by": "energy-vad"}}
                    for a, b in self._speech(ctx)]

        gran = "file" if mod == "image" else task.get("granularity", "segment")
        if gran in ("word", "phone"):
            raise UnsupportedTask(f"{task['name']}: word and phone timing need the alignment head")
        if gran == "event":
            return self._events(task, ctx)
        if gran == "file":
            spans = [(None, None)] if mod == "image" else [(0.0, round(ctx.duration, 3))]
        elif gran == "window":
            spans = self._windows(ctx.duration, float(task.get("window_sec", 5)))
        else:  # segment
            spans = self._speech(ctx)
        if mod != "image" and not self._visual(task, ctx) and not len(ctx.audio):
            return []
        return self._regions(task, ctx, spans)
