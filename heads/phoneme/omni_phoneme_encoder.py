"""Frozen mix87 audio tower -> per-layer streams in the layout the CTC phoneme heads read.

``OmniStreams(x, lens)``: x (B, N) 16 kHz waveforms right-padded, lens (B,) samples ->
feats (L, B, T, 1280) bf16 (one stream per tapped tower layer, 13 frames/s), valid (B, T).

Log-mel is computed on the padded batch: the Whisper floor (max - 8) is per clip and zero padding never
raises the max, and each clip's mel is cut to lens // 160 frames before packing, so every clip is encoded
exactly as it would be alone. The tower runs block-diagonal (104-frame windows), the deployment mode
every other mix87 sidecar head uses.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Sequence

import torch
import torch.nn as nn

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from omni_encoder import FrozenOmniAudioEncoder  # noqa: E402

DEFAULT_TOWER = os.environ.get("OMNI_AUDIO_TOWER", str(REPO / "checkpoints/mix87_audio_tower"))
HOP = 160


class OmniStreams(nn.Module):
    def __init__(self, tower: str = DEFAULT_TOWER, taps: Optional[Sequence[int]] = None,
                 amp_dtype: Optional[torch.dtype] = torch.bfloat16):
        super().__init__()
        self.enc = FrozenOmniAudioEncoder(tower, attn_mode="blockdiag")
        self.taps = list(range(self.enc.num_layers)) if taps is None else sorted(int(t) for t in taps)
        self.amp_dtype = amp_dtype
        self.num_streams = len(self.taps)
        self.feature_dim = self.enc.d_model

    def train(self, mode: bool = True):
        super().train(False)
        return self

    @torch.no_grad()
    def forward(self, x: torch.Tensor, lens: torch.Tensor):
        mel = self.enc.mel(x)                                              # (B, n_mels, N // HOP)
        n = torch.clamp(lens // HOP, min=1, max=mel.shape[-1])
        flat = torch.cat([mel[b, :, :int(n[b])] for b in range(x.shape[0])], dim=1)
        with torch.autocast("cuda", dtype=self.amp_dtype, enabled=self.amp_dtype is not None):
            out = self.enc(flat, n, tap_layers=self.taps, return_llm_features=False)
        taps, T = out["taps"], out["aftercnn_lens"]                       # (sum T, L, D), (B,)
        per = torch.split(taps.to(torch.bfloat16), T.tolist(), dim=0)
        feats = nn.utils.rnn.pad_sequence(per, batch_first=True)          # (B, Tmax, L, D)
        valid = torch.arange(feats.shape[1], device=x.device)[None, :] < T.to(x.device)[:, None]
        return feats.permute(2, 0, 1, 3).contiguous(), valid
