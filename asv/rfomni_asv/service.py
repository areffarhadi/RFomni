#!/usr/bin/env python3
"""Speaker-embedding microservice.

    POST /embed   raw 16-bit mono PCM (?sr=16000)  -> {"embedding": [...256], "dim": 256}
    POST /score   {"a": [...], "b": [...]}         -> {"similarity": 0.83}
    GET  /health                                   -> {"speaker": "up", ...}

WHY A SERVICE RATHER THAN A LIBRARY CALL. The consumer of this block is usually a voice
agent already holding a large model on the GPU. Keeping the embedder behind loopback HTTP
means the agent process loads no torch, takes no VRAM, and can be restarted without
reloading the voiceprint model — and it lets the two run in different virtualenvs, which
matters because wespeaker's dependencies do not co-install with much.

WHY THIS MODEL. 0.4148% EER on VoxCeleb1-O, and measured against the pyannote-loaded
ResNet34-LM on the same 18-case set (VoxConverse, ground-truth speaker labels),
owner-vs-impostor separation is 0.737 against 0.593. Owner scores rise (0.783 vs 0.735)
and impostor scores fall (0.046 vs 0.142). See benchmarks/RESULTS.md.

Run it:

    python -m rfomni_asv.service --port 8612
    SPK_BACKEND=torch SPK_DEVICE=cuda:0 python -m rfomni_asv.service --port 8612
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
from fastapi import FastAPI, Request

from .backends import SAMPLE_RATE, Embedder, load_embedder

#: The embedder rejects anything shorter: a sliver of speech makes a poor voiceprint, and
#: scoring it would hand the caller a confident-looking number built on nothing.
MIN_SECONDS = float(os.environ.get("SPK_MIN_SECONDS", "0.5"))

app = FastAPI(title="RFOMNI ASV")
_embedder: Embedder | None = None


def embedder() -> Embedder:
    """Loaded on first use so ``--help`` and imports stay instant."""
    global _embedder
    if _embedder is None:
        _embedder = load_embedder()
    return _embedder


@app.get("/health")
async def health():
    """Reports ``speaker: up`` only after a real forward pass — a model that loads but
    cannot run is exactly the failure a health check exists to catch."""
    try:
        e = embedder()
        silence = np.zeros(SAMPLE_RATE, dtype=np.int16).tobytes()
        dim = len(e.embed(silence, SAMPLE_RATE))
        # spec_info carries the threshold this checkpoint was measured at, so a client
        # that switches models does not keep using the previous one's threshold.
        return {"speaker": "up", "dim": dim, **e.info(), **e.spec_info()}
    except Exception as exc:
        return {"speaker": "down", "error": str(exc)[:300]}


@app.post("/embed")
async def embed(request: Request):
    sr = int(request.query_params.get("sr", str(SAMPLE_RATE)))
    pcm = await request.body()
    secs = len(pcm) / 2 / sr
    if secs < MIN_SECONDS:
        return {"error": f"too short for a voiceprint ({secs:.2f}s)"}
    try:
        v = embedder().embed(pcm, sr)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"embedding": v.tolist(), "dim": len(v), "seconds": round(secs, 2)}


@app.post("/score")
async def score(request: Request):
    body = json.loads(await request.body())
    a = np.asarray(body["a"], dtype=np.float64)
    b = np.asarray(body["b"], dtype=np.float64)
    a = a / (np.linalg.norm(a) or 1)
    b = b / (np.linalg.norm(b) or 1)
    return {"similarity": float(np.dot(a, b))}


def main() -> None:
    ap = argparse.ArgumentParser(description="RFOMNI ASV embedding service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SPK_PORT", "8612")))
    args = ap.parse_args()

    import uvicorn

    e = embedder()  # fail loudly here rather than on the first request
    print(f"[rfomni-asv] {e.info()}")
    print(f"[rfomni-asv] listening on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
