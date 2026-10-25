"""Silero's VADIterator, packaged as a pipecat VADAnalyzer.

WHY THIS EXISTS. pipecat's SileroVADAnalyzer runs the same Silero weights but owns the
speech decision itself: it reads a raw probability per 512-sample window and applies its
own start/stop hysteresis. VADIterator is the model's own streaming front end and makes
the decision internally — a speech probability above ``threshold`` triggers, staying below
``threshold - 0.15`` for ``min_silence_duration_ms`` ends it. This analyzer lets that
built-in state machine drive pipecat instead, so the whole VAD decision (threshold,
hysteresis band, minimum silence) is the one Silero ships, tuned independently of pipecat's
``start_secs``/``stop_secs``, which then act as a second confirmation layer on top.

THE INTERFACE MISMATCH, AND HOW IT IS BRIDGED. pipecat asks for a continuous confidence
in [0, 1] per window and thresholds it against ``VADParams.confidence``. VADIterator
answers a yes/no question. The bridge is binary: while the iterator is triggered this
returns 1.0, otherwise 0.0. ``VADParams.confidence`` therefore no longer scores audio — it
selects the threshold handed to VADIterator (whose end condition is then that value minus
0.15), and ``min_volume`` still gates as before. The practical effect on latency: turn end
now waits for VADIterator's minimum silence *and* pipecat's ``stop_secs``, so
OMNI_VAD_ITER_MIN_SILENCE_MS should be kept small (its 100 ms default) unless the
iterator's own silence handling is what you want to lean on.

The model runs through onnxruntime (``load_silero_vad(onnx=True)``): same weights as the
torch checkpoint, no CUDA wheels needed. torch is still imported by ``silero_vad`` itself,
so the interpreter needs a torch install — CPU is fine.
"""

from __future__ import annotations

import time

import numpy as np
import torch
from loguru import logger

from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams

# How often to reset the model's RNN state, as pipecat's own Silero analyzer does. The
# iterator's speech state (``triggered``) is deliberately preserved: wiping it mid-turn
# would replay a "start" event pipecat has already consumed.
_MODEL_RESET_STATES_TIME = 5.0


class VADIteratorAnalyzer(VADAnalyzer):
    """VADAnalyzer backed by silero-vad's VADIterator.

    Args:
        sample_rate: Audio sample rate (8000 or 16000 Hz). If None, will be set later.
        params: VAD parameters. ``confidence`` becomes VADIterator's speech threshold;
            the other three keep their pipecat meanings.
        min_silence_duration_ms: VADIterator's own minimum silence before it ends a
            speech region. Stacks with pipecat's ``stop_secs``.
        speech_pad_ms: VADIterator's padding of reported boundaries. Only affects the
            ``start``/``end`` timestamps, which this analyzer does not surface, but is
            accepted for completeness.
    """

    def __init__(
        self,
        *,
        sample_rate: int | None = None,
        params: VADParams | None = None,
        min_silence_duration_ms: int = 100,
        speech_pad_ms: int = 30,
    ):
        super().__init__(sample_rate=sample_rate, params=params)

        from silero_vad import VADIterator, load_silero_vad

        logger.debug("Loading Silero VADIterator (onnx)...")
        self._model = load_silero_vad(onnx=True)
        self._min_silence_duration_ms = min_silence_duration_ms
        self._speech_pad_ms = speech_pad_ms
        self._iterator: VADIterator | None = None
        self._last_reset_time = time.time()
        logger.debug("Loaded Silero VADIterator")

    def set_sample_rate(self, sample_rate: int):
        if sample_rate != 16000 and sample_rate != 8000:
            raise ValueError(
                f"Silero VAD sample rate needs to be 16000 or 8000 (sample rate: {sample_rate})"
            )
        super().set_sample_rate(sample_rate)
        self._build_iterator()

    def set_params(self, params: VADParams):
        super().set_params(params)
        # The threshold lives inside the iterator, not in this class; keep the two in
        # step so a runtime param change takes effect on the next window.
        if self._iterator is not None:
            self._iterator.threshold = self._params.confidence

    def _build_iterator(self):
        from silero_vad import VADIterator

        if self._sample_rate not in (8000, 16000):
            return
        self._iterator = VADIterator(
            self._model,
            threshold=self._params.confidence,
            sampling_rate=self._sample_rate,
            min_silence_duration_ms=self._min_silence_duration_ms,
            speech_pad_ms=self._speech_pad_ms,
        )

    def num_frames_required(self) -> int:
        return 512 if self.sample_rate == 16000 else 256

    def voice_confidence(self, buffer) -> float:
        if self._iterator is None:
            self._build_iterator()
            if self._iterator is None:
                return 0
        try:
            audio_int16 = np.frombuffer(buffer, np.int16)
            # Signed 16-bit data, scaled back to the model's expected range.
            audio_float32 = audio_int16.astype(np.float32) / 32768.0
            self._iterator(torch.from_numpy(audio_float32))

            curr_time = time.time()
            if curr_time - self._last_reset_time >= _MODEL_RESET_STATES_TIME:
                self._iterator.model.reset_states()
                self._last_reset_time = curr_time

            return 1.0 if self._iterator.triggered else 0.0
        except Exception as e:
            logger.error(f"Error analyzing audio with Silero VADIterator: {e}")
            return 0
