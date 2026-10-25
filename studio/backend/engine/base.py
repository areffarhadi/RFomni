"""Engine interface. An engine turns (media file, task) into a list of segments.

To connect a real model (e.g. Qwen3-Omni served by vLLM-omni), implement `Engine.annotate`
in a new module and return it from `engine/__init__.py:get_engine`. Everything else
(projects, job queue, merging into tiers, the editor, exports) stays unchanged.

A returned segment is a dict:
    {"start": float | None, "end": float | None,   # seconds; None for images / whole-file values
     "value": str | list[str],                       # text, label, score (as str) or tags
     "confidence": float | None,                     # 0..1
     "meta": {...}}                                  # optional, kept as-is
"""
from typing import Callable

import numpy as np


class AnnotateContext:
    """What an engine gets to work with for one media file during a run."""

    def __init__(self, media: dict, path, doc: dict, audio_loader: Callable[[], np.ndarray],
                 settings: dict | None = None):
        self.media = media
        self.path = path
        self.doc = doc
        self.duration = media.get("duration") or 0.0
        self.settings = settings or {}
        self._audio_loader = audio_loader
        self._audio = None
        self.results: dict[str, list] = {}   # task_id -> segments produced earlier in this run
        self.cache: dict = {}                # scratch space shared across tasks of one file

    @property
    def audio(self) -> np.ndarray:
        """16 kHz mono float32 (empty for images, text and silent video)."""
        if self._audio is None:
            if self.media.get("modality") in ("image", "text") or not self.media.get("has_audio", True):
                self._audio = np.zeros(0, np.float32)
            else:
                self._audio = self._audio_loader()
        return self._audio

    def tier_segments(self, task_id: str) -> list:
        """Segments for a task: this run's output if present, else what is already in the file."""
        if task_id in self.results:
            return self.results[task_id]
        tiers = {t["id"] for t in self.doc.get("tiers", []) if t.get("task_id") == task_id or t["id"] == task_id}
        segs = [s for s in self.doc.get("segments", []) if s["tier_id"] in tiers]
        return sorted(segs, key=lambda s: (s["start"] or 0))


class Engine:
    name = "base"
    title = "Base engine"
    source = "model"
    connected = False

    def info(self) -> dict:
        return {"name": self.name, "title": self.title, "connected": self.connected,
                "source": self.source, "description": (self.__doc__ or "").strip()}

    def annotate(self, task: dict, ctx: AnnotateContext) -> list:
        raise NotImplementedError
