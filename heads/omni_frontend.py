"""Whisper log-mel frontend and the exact Qwen3-Omni encoder frame grid.

The frame grid here is not a uniform frame rate. The official encoder forward splits
mel into chunks of ``n_window * 2 = 100`` frames (1.00 s) and runs the conv frontend on
each chunk independently, producing exactly 13 frames per second. Within a second the
frames sit 80 ms apart, so there is a ~40 ms discontinuity at every second boundary.
Treating the output as a uniform 1/13 s = 76.9 ms grid drifts by up to 40 ms within
each second, which is enough to smear diarization boundaries.

``FRAME_CENTRE_OFFSETS`` was measured by back-propagating from each output frame to the
mel input and taking the centre of mass of the sensitivity (see the module docstring of
``tools/probe_frame_grid.py``). The two edge frames deviate from the 80 ms spacing
because of conv padding.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import torch
import torch.nn as nn

SAMPLE_RATE = 16000
HOP_LENGTH = 160
MEL_FRAMES_PER_SECOND = SAMPLE_RATE // HOP_LENGTH  # 100
MEL_CHUNK = 100  # n_window * 2, the unit the official forward convolves over
FRAMES_PER_SECOND = 13  # encoder frames produced per MEL_CHUNK

# Centre time of each of the 13 frames, in seconds relative to the start of its second.
FRAME_CENTRE_OFFSETS = np.array(
    [0.0267, 0.0949, 0.1749, 0.2549, 0.3349, 0.4149, 0.4949,
     0.5749, 0.6549, 0.7349, 0.8149, 0.8949, 0.9621],
    dtype=np.float64,
)
FRAME_RECEPTIVE_FIELD = 0.15  # seconds, 15 mel frames


def num_encoder_frames(num_mel_frames: int) -> int:
    """Mirror of ``transformers``' ``_get_feat_extract_output_lengths``."""
    leave = num_mel_frames % MEL_CHUNK
    feat = (leave - 1) // 2 + 1
    return ((feat - 1) // 2 + 1 - 1) // 2 + 1 + (num_mel_frames // MEL_CHUNK) * FRAMES_PER_SECOND


def frame_centres(num_frames: int) -> np.ndarray:
    """Centre time in seconds of each encoder frame, following the per-second grid."""
    idx = np.arange(num_frames)
    return idx // FRAMES_PER_SECOND + FRAME_CENTRE_OFFSETS[idx % FRAMES_PER_SECOND]


def upsampled_frame_centres(num_frames: int, factor: int) -> np.ndarray:
    """Centre times after subdividing each encoder frame into ``factor`` sub-frames.

    Each encoder frame owns an 80 ms cell centred on its measured centre; the cell is
    split into ``factor`` equal sub-cells and we return their centres. For factor=4 this
    gives a ~20 ms grid whose coverage is continuous across second boundaries.
    """
    base = frame_centres(num_frames)
    cell = 1.0 / (FRAMES_PER_SECOND * factor) * (FRAMES_PER_SECOND * 0.08)  # 80ms / factor
    k = np.arange(factor)
    sub = (k - (factor - 1) / 2.0) * cell
    return (base[:, None] + sub[None, :]).reshape(-1)


class WhisperLogMel(nn.Module):
    """Batched Whisper log-mel, mirroring ``WhisperFeatureExtractor``.

    Unlike the HF extractor this does not pad or truncate to 30 s, so a whole recording
    can be processed in one call. The dynamic-range floor uses the max over the entire
    input, matching HF's per-sample behaviour.
    """

    def __init__(
        self,
        mel_filters: np.ndarray,
        n_fft: int = 400,
        hop_length: int = HOP_LENGTH,
        dither: float = 0.0,
    ):
        super().__init__()
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.dither = dither
        self.register_buffer("mel_filters_t", torch.from_numpy(mel_filters.astype(np.float32).T))
        self.register_buffer("window", torch.hann_window(n_fft))

    @classmethod
    def from_hf_extractor(cls, extractor) -> "WhisperLogMel":
        return cls(
            mel_filters=extractor.mel_filters,
            n_fft=extractor.n_fft,
            hop_length=extractor.hop_length,
            dither=getattr(extractor, "dither", 0.0),
        )

    def forward(self, waveform: torch.Tensor, global_max: Optional[torch.Tensor] = None):
        if waveform.dim() == 1:
            waveform = waveform.unsqueeze(0)
        x = waveform.to(dtype=torch.float32)
        if self.dither != 0.0:
            x = x + self.dither * torch.randn_like(x)

        stft = torch.stft(
            x,
            self.n_fft,
            self.hop_length,
            window=self.window.to(device=x.device, dtype=x.dtype),
            return_complex=True,
        )
        magnitudes = stft[..., :-1].abs().pow(2)
        mel_spec = torch.matmul(self.mel_filters_t, magnitudes)
        log_spec = torch.clamp(mel_spec, min=1e-10).log10()

        if global_max is None:
            global_max = log_spec.amax(dim=2, keepdim=True).amax(dim=1, keepdim=True)
        log_spec = torch.maximum(log_spec, global_max - 8.0)
        return (log_spec + 4.0) / 4.0
