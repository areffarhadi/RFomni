"""Linear VAD probe on one frozen encoder tap.

Measured on 4 AudioBench TED-LIUM long-form talks (docs/PAPER_FORCED_ALIGNMENT_FINDING.md §9b):
encoder layer 31 reaches held-out ROC-AUC 0.9558, layer 19 0.9498, against 0.8905 for a waveform-RMS
control and 0.9086 for `diar_sidecar`'s speaker activity read as a speech gate. It is one matrix
multiply on a tap the audio heads already extract, so it costs effectively nothing when the encoder
is running anyway -- and if VAD is all that is wanted, layer 19 lets inference stop 41% into the
tower.

The probe is deliberately linear. Anything stronger would need to justify itself against
`diar_sidecar`, which already produces a usable gate as a by-product of diarization.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

FRAMES_PER_SECOND = 13.0


class LinearVAD(nn.Module):
    """Standardise, then a single linear layer, on one tap's hidden states.

    Stores the standardiser inside the module so a checkpoint is self-contained: the mean/scale are
    part of the model, not something the caller has to remember to apply.
    """

    def __init__(self, d_model: int = 1280, layer: int = 31):
        super().__init__()
        self.layer = layer
        self.register_buffer("mean", torch.zeros(d_model))
        self.register_buffer("scale", torch.ones(d_model))
        self.fc = nn.Linear(d_model, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """(T, d_model) hidden states -> (T,) logit that the frame contains speech."""
        return self.fc((x - self.mean) / self.scale).squeeze(-1)

    @torch.no_grad()
    def probs(self, x: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.forward(x))

    def set_standardiser(self, mean: np.ndarray, scale: np.ndarray) -> None:
        self.mean.copy_(torch.as_tensor(mean, dtype=self.mean.dtype))
        self.scale.copy_(torch.as_tensor(np.clip(scale, 1e-6, None), dtype=self.scale.dtype))


def load_vad(path: str, device="cpu") -> Tuple[LinearVAD, Dict]:
    ck = torch.load(path, map_location="cpu", weights_only=False)
    args = ck.get("args", {})
    m = LinearVAD(d_model=int(args.get("d_model", 1280)), layer=int(args.get("layer", 31)))
    m.load_state_dict(ck["model"])
    return m.to(device).eval(), ck


def to_segments(prob: np.ndarray, threshold: float = 0.5,
                min_speech: float = 0.20, min_silence: float = 0.20,
                pad: float = 0.0, fps: float = FRAMES_PER_SECOND) -> List[Dict[str, float]]:
    """Per-frame speech probability -> merged [start, end) segments in seconds.

    Short gaps are filled before short segments are dropped: doing it the other way round would
    delete a real word that happens to sit either side of a single-frame dip.
    """
    on = prob >= threshold
    if not on.any():
        return []
    idx = np.flatnonzero(np.diff(np.r_[False, on, False]))
    spans = list(zip(idx[::2], idx[1::2]))

    gap = int(round(min_silence * fps))
    merged: List[List[int]] = []
    for s, e in spans:
        if merged and s - merged[-1][1] <= gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    keep = int(round(min_speech * fps))
    out = []
    for s, e in merged:
        if e - s < keep:
            continue
        a, b = s / fps - pad, e / fps + pad
        out.append({"start": round(max(0.0, a), 3), "end": round(b, 3),
                    "score": round(float(prob[s:e].mean()), 4)})
    return out


def speech_ratio(segments: List[Dict[str, float]], duration: float) -> float:
    return round(sum(s["end"] - s["start"] for s in segments) / max(duration, 1e-9), 4)
