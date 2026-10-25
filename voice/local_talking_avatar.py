"""Bot Video avatar helpers. Default is none (empty Bot Video panel)."""

from __future__ import annotations

import math
import struct
from typing import Optional

import numpy as np
from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    OutputImageRawFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class LocalTalkingAvatarService(FrameProcessor):
    """Cheap Bot Video filler driven by TTSAudioRawFrame RMS."""

    def __init__(
        self,
        *,
        width: int = 512,
        height: int = 512,
        fps: int = 12,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._w = width
        self._h = height
        self._fps = fps
        self._speaking = False
        self._level = 0.0
        self._t = 0
        self._idle = self._draw(0.0, blink=False)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            await self.push_frame(
                OutputImageRawFrame(image=self._idle, size=(self._w, self._h), format="RGB")
            )
            return

        if isinstance(frame, (EndFrame, CancelFrame, InterruptionFrame)):
            self._speaking = False
            self._level = 0.0
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSStartedFrame):
            self._speaking = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSStoppedFrame):
            self._speaking = False
            self._level = 0.0
            await self.push_frame(
                OutputImageRawFrame(image=self._draw(0.0), size=(self._w, self._h), format="RGB")
            )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSAudioRawFrame):
            self._level = 0.85 * self._level + 0.15 * self._rms(frame.audio)
            self._t += 1
            # Emit ~fps frames while speaking (not every audio chunk).
            if self._speaking and (self._t % max(1, int(16000 / (320 * self._fps))) == 0):
                await self.push_frame(
                    OutputImageRawFrame(
                        image=self._draw(self._level),
                        size=(self._w, self._h),
                        format="RGB",
                    )
                )
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    @staticmethod
    def _rms(pcm: bytes) -> float:
        if len(pcm) < 4:
            return 0.0
        n = len(pcm) // 2
        samples = struct.unpack(f"<{n}h", pcm[: n * 2])
        if not samples:
            return 0.0
        mean = sum(s * s for s in samples) / len(samples)
        return min(1.0, math.sqrt(mean) / 8000.0)

    def _draw(self, level: float, blink: Optional[bool] = None) -> bytes:
        w, h = self._w, self._h
        img = np.zeros((h, w, 3), dtype=np.uint8)
        # Background gradient
        yy = np.linspace(18, 40, h, dtype=np.float32)[:, None]
        img[:, :, 0] = (yy * 1.2).astype(np.uint8)
        img[:, :, 1] = (yy * 2.0).astype(np.uint8)
        img[:, :, 2] = (yy * 2.6).astype(np.uint8)

        cx, cy = w // 2, h // 2 - 10
        face_r = min(w, h) // 3
        self._circle(img, cx, cy, face_r, (220, 190, 160))

        eye_y = cy - face_r // 3
        eye_dx = face_r // 3
        eye_r = max(6, face_r // 10)
        closed = blink if blink is not None else False
        if closed:
            self._hline(img, cx - eye_dx - eye_r, cx - eye_dx + eye_r, eye_y, (40, 40, 40))
            self._hline(img, cx + eye_dx - eye_r, cx + eye_dx + eye_r, eye_y, (40, 40, 40))
        else:
            self._circle(img, cx - eye_dx, eye_y, eye_r, (40, 40, 40))
            self._circle(img, cx + eye_dx, eye_y, eye_r, (40, 40, 40))

        # Mouth ellipse grows with level
        mouth_w = face_r // 2
        mouth_h = max(4, int((8 + level * 42)))
        my = cy + face_r // 3
        self._ellipse(img, cx, my, mouth_w, mouth_h, (120, 40, 50))

        # RFOMNI label
        return img.tobytes()

    @staticmethod
    def _circle(img: np.ndarray, cx: int, cy: int, r: int, color):
        h, w = img.shape[:2]
        y, x = np.ogrid[-cy : h - cy, -cx : w - cx]
        mask = x * x + y * y <= r * r
        img[mask] = color

    @staticmethod
    def _ellipse(img: np.ndarray, cx: int, cy: int, rx: int, ry: int, color):
        h, w = img.shape[:2]
        y, x = np.ogrid[-cy : h - cy, -cx : w - cx]
        mask = (x * x) * (ry * ry) + (y * y) * (rx * rx) <= (rx * rx) * (ry * ry)
        img[mask] = color

    @staticmethod
    def _hline(img: np.ndarray, x0: int, x1: int, y: int, color):
        h, w = img.shape[:2]
        if 0 <= y < h:
            img[y, max(0, x0) : min(w, x1 + 1)] = color


def build_avatar_processor():
    """Return (processor|None, kind). Default is no avatar (empty Bot Video)."""
    import os

    import httpx

    prefer = (os.environ.get("OMNI_AVATAR") or "none").strip().lower()
    wav2lip_url = (os.environ.get("WAV2LIP_URL") or "http://127.0.0.1:8099").rstrip("/")

    if prefer in ("none", "off", "0", "false", "no", "empty", ""):
        logger.info("OMNI_AVATAR={} — Bot Video left empty (no avatar)", prefer or "none")
        return None, "none"

    def _local():
        return LocalTalkingAvatarService(
            width=int(os.environ.get("OMNI_AVATAR_WIDTH", "512")),
            height=int(os.environ.get("OMNI_AVATAR_HEIGHT", "512")),
        ), "local"

    def _facemesh():
        from facemesh_avatar import FaceMeshAvatarService

        wire = (os.environ.get("OMNI_FACEMESH_WIRE", "1") or "1").strip() not in ("0", "false", "no")
        logger.info("Using 3D MediaPipe face-mesh avatar (wire={})", wire)
        return FaceMeshAvatarService(
            width=int(os.environ.get("OMNI_AVATAR_WIDTH", "640")),
            height=int(os.environ.get("OMNI_AVATAR_HEIGHT", "640")),
            fps=int(os.environ.get("OMNI_AVATAR_FPS", "15")),
            show_wire=wire,
        ), "facemesh"

    if prefer in ("facemesh", "mesh", "face-mesh", "face_mesh"):
        try:
            return _facemesh()
        except Exception as exc:
            logger.warning("Face-mesh unavailable ({}), no avatar", exc)
            return None, "none"

    if prefer in ("wav2lip", "livetalking"):
        try:
            r = httpx.get(f"{wav2lip_url}/health", timeout=2.0)
            if r.status_code == 200 and r.json().get("ok"):
                from wav2lip_avatar import Wav2LipAvatarService

                logger.info("Using free Wav2Lip avatar sidecar at {}", wav2lip_url)
                return Wav2LipAvatarService(base_url=wav2lip_url), "wav2lip"
            logger.warning("Wav2Lip sidecar unhealthy ({})", r.status_code)
        except Exception as exc:
            logger.warning("Wav2Lip sidecar unavailable ({})", exc)
        return None, "none"

    if prefer == "local":
        logger.info("OMNI_AVATAR=local — procedural Bot Video")
        return _local()

    api_key = (os.environ.get("SIMLI_API_KEY") or "").strip()
    face_id = (os.environ.get("SIMLI_FACE_ID") or "cace3ef7-a4c4-425d-a8cf-a5358eb0c427").strip()
    if prefer == "simli":
        if api_key:
            try:
                from pipecat.services.simli.video import SimliVideoService

                logger.info("Using Simli talking avatar face_id={}", face_id[:12])
                return SimliVideoService(api_key=api_key, face_id=face_id), "simli"
            except Exception as exc:
                logger.warning("Simli unavailable ({}), no avatar", exc)
        else:
            logger.warning("OMNI_AVATAR=simli but SIMLI_API_KEY unset")
        return None, "none"

    logger.info("Unknown OMNI_AVATAR={!r} — Bot Video left empty", prefer)
    return None, "none"
