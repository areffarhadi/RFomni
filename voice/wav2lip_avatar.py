"""Pipecat Bot Video via free self-hosted Wav2Lip sidecar (LiveTalking)."""

from __future__ import annotations

import base64
import os
import uuid
from typing import Optional

import httpx
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


class Wav2LipAvatarService(FrameProcessor):
    """Drive lip-synced OutputImageRawFrame from TTSAudioRawFrame via local sidecar."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout: float = 30.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._base = (base_url or os.environ.get("WAV2LIP_URL", "http://127.0.0.1:8099")).rstrip("/")
        self._session_id = session_id or f"rfomni-{uuid.uuid4().hex[:10]}"
        self._client = httpx.AsyncClient(timeout=timeout)
        self._w = 576
        self._h = 768
        self._speaking = False
        self._idle: Optional[bytes] = None
        self._ok = True

    async def cleanup(self):
        try:
            await self._client.delete(f"{self._base}/v1/session/{self._session_id}")
        except Exception:
            pass
        await self._client.aclose()
        await super().cleanup()

    async def _fetch_idle(self) -> Optional[bytes]:
        try:
            r = await self._client.get(
                f"{self._base}/v1/idle",
                params={"session_id": self._session_id},
            )
            r.raise_for_status()
            self._w = int(r.headers.get("X-Width", self._w))
            self._h = int(r.headers.get("X-Height", self._h))
            self._idle = r.content
            self._ok = True
            return self._idle
        except Exception as exc:
            self._ok = False
            logger.warning("Wav2Lip idle fetch failed: {}", exc)
            return None

    async def _push_frames_b64(self, frames_b64: list[str]):
        for b64 in frames_b64:
            raw = base64.b64decode(b64)
            await self.push_frame(
                OutputImageRawFrame(image=raw, size=(self._w, self._h), format="RGB")
            )

    async def _send_audio(self, pcm: bytes, sample_rate: int, channels: int):
        if not pcm or not self._ok:
            return
        try:
            r = await self._client.post(
                f"{self._base}/v1/audio",
                json={
                    "pcm_b64": base64.b64encode(pcm).decode("ascii"),
                    "sample_rate": sample_rate,
                    "channels": channels,
                    "session_id": self._session_id,
                },
            )
            r.raise_for_status()
            data = r.json()
            self._w = int(data.get("width", self._w))
            self._h = int(data.get("height", self._h))
            await self._push_frames_b64(data.get("frames_b64") or [])
        except Exception as exc:
            logger.warning("Wav2Lip audio push failed: {}", exc)
            self._ok = False

    async def _flush(self):
        if not self._ok:
            return
        try:
            r = await self._client.post(
                f"{self._base}/v1/flush",
                params={"session_id": self._session_id},
            )
            r.raise_for_status()
            data = r.json()
            await self._push_frames_b64(data.get("frames_b64") or [])
            await self._client.post(
                f"{self._base}/v1/reset",
                params={"session_id": self._session_id},
            )
        except Exception as exc:
            logger.warning("Wav2Lip flush failed: {}", exc)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            idle = await self._fetch_idle()
            if idle:
                await self.push_frame(
                    OutputImageRawFrame(image=idle, size=(self._w, self._h), format="RGB")
                )
            return

        if isinstance(frame, (EndFrame, CancelFrame)):
            self._speaking = False
            await self._flush()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            self._speaking = False
            try:
                await self._client.post(
                    f"{self._base}/v1/reset",
                    params={"session_id": self._session_id},
                )
            except Exception:
                pass
            idle = await self._fetch_idle()
            if idle:
                await self.push_frame(
                    OutputImageRawFrame(image=idle, size=(self._w, self._h), format="RGB")
                )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSStartedFrame):
            self._speaking = True
            self._ok = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSStoppedFrame):
            self._speaking = False
            await self._flush()
            idle = await self._fetch_idle()
            if idle:
                await self.push_frame(
                    OutputImageRawFrame(image=idle, size=(self._w, self._h), format="RGB")
                )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSAudioRawFrame):
            if self._speaking:
                await self._send_audio(frame.audio, frame.sample_rate, frame.num_channels)
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
