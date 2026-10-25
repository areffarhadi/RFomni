"""Capture UserImageRawFrame before the LLM aggregator consumes them."""

from __future__ import annotations

import base64
import io
from typing import Callable, Optional

from loguru import logger

from pipecat.frames.frames import Frame, UserImageRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class CameraTap(FrameProcessor):
    """Pass-through that keeps the latest camera JPEG for Omni vision turns."""

    def __init__(self, on_jpeg: Optional[Callable[[str], None]] = None, **kwargs):
        super().__init__(**kwargs)
        self._on_jpeg = on_jpeg
        self.latest_jpeg_b64: str | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserImageRawFrame):
            try:
                from PIL import Image

                img = Image.frombytes(frame.format or "RGB", frame.size, frame.image)
                # Downscale for faster vision hops.
                img.thumbnail((512, 512))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=70)
                self.latest_jpeg_b64 = base64.b64encode(buf.getvalue()).decode()
                if self._on_jpeg:
                    self._on_jpeg(self.latest_jpeg_b64)
            except Exception as exc:
                logger.debug("CameraTap encode failed: {}", exc)
        await self.push_frame(frame, direction)
