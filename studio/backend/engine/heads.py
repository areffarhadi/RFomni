"""Client for the mix87 sidecar heads service (omni-mix87-sidecar-heads/serve).

The heads sit on the frozen audio and vision towers of the same mix87 model and answer the
tasks they were trained for (speech regions, speakers, voice gender, spoofing, phonemes,
end of turn, body parts, pose, face spoof/deepfake, head pose) faster and better than
prompting the thinker. The service runs on its own GPU; when it is down the engine falls
back to the thinker where it can. Setting: OMNI_HEADS_URL.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

import numpy as np

URL = os.environ.get("OMNI_HEADS_URL", "http://127.0.0.1:8720").rstrip("/")
TIMEOUT = float(os.environ.get("OMNI_HEADS_TIMEOUT", "600"))

_probe = {"at": 0.0, "info": None}


def info() -> dict | None:
    """/health of the service, cached for 30 s; None when it is not reachable."""
    if time.time() - _probe["at"] > 30:
        try:
            with urllib.request.urlopen(f"{URL}/health", timeout=3) as r:
                _probe["info"] = json.load(r)
        except Exception:
            _probe["info"] = None
        _probe["at"] = time.time()
    return _probe["info"]


def available(kind: str) -> bool:
    i = info()
    return bool(i and i.get(kind))


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(f"{URL}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"heads service {path} returned {e.code}: {e.read()[:200].decode(errors='replace')}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        _probe["at"] = 0.0
        raise RuntimeError(f"heads service not reachable at {URL}: {e}") from e


def audio(x: np.ndarray, heads: list, segments=None, options=None) -> dict:
    """x: float32 mono 16 kHz in -1..1."""
    pcm = np.clip(x * 32767.0, -32768, 32767).astype("<i2").tobytes()
    return _post("/audio", {"pcm16": base64.b64encode(pcm).decode(), "heads": heads,
                            "segments": segments, "options": options or {}})


def image(path, heads: list, options=None) -> dict:
    return _post("/image", {"path": str(path), "heads": heads, "options": options or {}})


def video(path, heads: list, options=None) -> dict:
    return _post("/video", {"path": str(path), "heads": heads, "options": options or {}})
