"""Sidecar heads service: one process, the frozen mix87 audio and vision towers loaded once,
every head resident on them. Local only (127.0.0.1); Omni Studio's engine calls it.

  GET  /health                      what is loaded, GPU memory
  POST /audio  {"pcm16": <base64 int16 LE, 16 kHz mono> | "path": <local audio file>, "heads": [...],
                "segments": [[s, e], ...], "options": {}}
         heads: vad, diarization, speaker_count, gender, spoof, phonemes, end_of_turn
  POST /image  {"path": <file> | "image": <base64 bytes>, "heads": [...], "options": {"classes": [...], "score_thresh": 0.3}}
         heads: boxes, pose, face_spoof, head_pose
  POST /video  {"path": <file>, "heads": ["video_spoof"], "options": {"fps": 2.0, "max_frames": 256}}

Settings: OMNI_AUDIO_TOWER, OMNI_VISION_ENCODER, HEADS_DEVICE (default cuda:0; start.sh pins
the physical GPU with CUDA_VISIBLE_DEVICES), HEADS_LOAD (audio,vision).
"""
import base64
import io
import os
import threading
import time
from contextlib import asynccontextmanager

import numpy as np
import torch
from fastapi import Body, FastAPI, HTTPException
from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool

from . import compat  # noqa: F401  (sys.path, vendored modules)

# the mix87 heads bundle (model weights, not in the repository)
BUNDLE = os.environ.get("RFOMNI_HEADS_BUNDLE", "/home/rf/omni-annotate/omni-mix87-heads-bundle/encoder")
AUDIO_TOWER = os.environ.get("OMNI_AUDIO_TOWER", f"{BUNDLE}/mix87_audio_tower")
VISION_ENCODER = os.environ.get("OMNI_VISION_ENCODER", f"{BUNDLE}/mix87_ckpt54000-merged-MXFP4A16mse-fused")
DEVICE = torch.device(os.environ.get("HEADS_DEVICE", "cuda:0"))
LOAD = {s.strip() for s in os.environ.get("HEADS_LOAD", "audio,vision").split(",") if s.strip()}

state = {"audio": None, "vision": None, "loaded_at": None, "load_s": {}}
gpu = threading.Lock()  # one request on the GPU at a time


class _gpu_turn:
    """Hold the GPU for one request, then hand cached blocks back so the card stays shareable
    (a long video can peak ~10 GB above the 4 GB the towers and heads need)."""

    def __enter__(self):
        gpu.acquire()

    def __exit__(self, *exc):
        try:
            torch.cuda.empty_cache()
        finally:
            gpu.release()


def _load():
    if "audio" in LOAD:
        t = time.time()
        from .audio_heads import AudioHeads
        state["audio"] = AudioHeads(AUDIO_TOWER, DEVICE)
        state["load_s"]["audio"] = round(time.time() - t, 1)
    if "vision" in LOAD:
        t = time.time()
        from .vision_heads import VisionHeads
        state["vision"] = VisionHeads(VISION_ENCODER, DEVICE)
        state["load_s"]["vision"] = round(time.time() - t, 1)
    state["loaded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


@asynccontextmanager
async def lifespan(_app):
    await run_in_threadpool(_load)
    yield


app = FastAPI(title="mix87 sidecar heads", lifespan=lifespan)


def _need(kind):
    if state[kind] is None:
        raise HTTPException(503, f"{kind} heads are not loaded")
    return state[kind]


@app.get("/health")
def health():
    return {"audio": state["audio"] is not None, "vision": state["vision"] is not None,
            "audio_heads": ["vad", "diarization", "speaker_count", "gender", "spoof", "phonemes", "end_of_turn"],
            "image_heads": ["boxes", "pose", "face_spoof", "head_pose"], "video_heads": ["video_spoof"],
            "vocab": state["vision"].vocab if state["vision"] else [],
            "device": str(DEVICE), "gpu_name": torch.cuda.get_device_name(DEVICE),
            "gpu_mem_gb": round(torch.cuda.memory_allocated(DEVICE) / 2**30, 2),
            "loaded_at": state["loaded_at"], "load_s": state["load_s"]}


def _read_audio(path: str) -> np.ndarray:
    """Any file soundfile reads (wav, flac, ogg, ...) -> float32 mono 16 kHz."""
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(1)
    if sr != 16000:
        import torchaudio.functional as AF
        x = AF.resample(torch.from_numpy(x), sr, 16000).numpy()
    return np.ascontiguousarray(x, dtype=np.float32)


def _audio(body):
    heads = _need("audio")
    try:
        if body.get("path"):
            pcm = _read_audio(body["path"])
        else:
            pcm = np.frombuffer(base64.b64decode(body["pcm16"]), dtype="<i2").astype(np.float32) / 32768.0
    except Exception as e:
        raise HTTPException(400, f"send 'path' to a readable audio file or 'pcm16' (base64 int16 mono 16 kHz): {e}")
    t = time.time()
    with _gpu_turn():
        out = heads.analyze(pcm, body.get("heads") or ["vad"], body.get("segments"), body.get("options"))
    out["elapsed_s"] = round(time.time() - t, 3)
    return out


def _image(body):
    heads = _need("vision")
    try:
        if body.get("path"):
            img = Image.open(body["path"])
        else:
            img = Image.open(io.BytesIO(base64.b64decode(body["image"])))
        img.load()
        img = ImageOps.exif_transpose(img)  # same orientation the studio shows (boxes line up)
    except Exception as e:
        raise HTTPException(400, f"could not read the image: {e}")
    t = time.time()
    with _gpu_turn():
        out = heads.analyze_image(img, body.get("heads") or ["boxes"], body.get("options"))
    out["elapsed_s"] = round(time.time() - t, 3)
    return out


def _video(body):
    heads = _need("vision")
    if not body.get("path") or not os.path.exists(body["path"]):
        raise HTTPException(400, "video needs an existing local 'path'")
    t = time.time()
    with _gpu_turn():
        out = heads.analyze_video(body["path"], body.get("heads") or ["video_spoof"], body.get("options"))
    out["elapsed_s"] = round(time.time() - t, 3)
    return out


@app.post("/audio")
async def audio(body: dict = Body(...)):
    return await run_in_threadpool(_audio, body)


@app.post("/image")
async def image(body: dict = Body(...)):
    return await run_in_threadpool(_image, body)


@app.post("/video")
async def video(body: dict = Body(...)):
    return await run_in_threadpool(_video, body)
