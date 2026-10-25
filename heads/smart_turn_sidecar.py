"""Smart-turn (end-of-turn) sidecar on the frozen mix87 audio tower.

Input protocol follows pipecat Smart Turn v3: run it when VAD reports silence, on the audio
of the user's whole current turn (16 kHz mono); only the last 8 s are used. Output is
P(turn complete). Unlike Smart Turn the clip is *not* zero-padded to 8 s -- the tower takes
variable length, so a short turn is cheaper.

Two ways to run it:

  standalone   ``SmartTurnSidecar(ckpt)`` keeps only the tower layers the head reads
               (``head.max_layer + 1`` of 32) and runs waveform -> mel -> layers -> head.
  shared       when the full tower already runs for the LLM, pass its taps for the turn's
               last 8 s to ``from_taps``; the head itself is ~1.5 M parameters.

  sc = SmartTurnSidecar("checkpoints/smart_turn/best.pt", device="cuda:0")
  p = sc.predict([wave_np])            # -> tensor([0.97])
"""
from __future__ import annotations

import os
from typing import List, Sequence, Union

import numpy as np
import torch
import torch.nn as nn

from model_smart_turn import load_turn_head
from omni_encoder import FrozenOmniAudioEncoder

REPO = os.path.dirname(os.path.abspath(__file__))
CKPT = os.path.join(REPO, "checkpoints", "smart_turn", "best.pt")
DEFAULT_ENCODER = os.environ.get("OMNI_AUDIO_TOWER", "/local/scratch/arfarh/mix87_audio_tower_export")
SR = 16000
MAX_SECONDS = 8


class SmartTurnSidecar(nn.Module):
    def __init__(self, ckpt: str = CKPT, encoder_path: str = DEFAULT_ENCODER,
                 device: Union[str, torch.device] = "cuda:0", amp: bool = True,
                 standalone: bool = True):
        super().__init__()
        self.device = torch.device(device)
        self.head, self.meta = load_turn_head(ckpt, self.device)
        self.taps = self.head.taps
        self.threshold_logit = float(self.meta.get("threshold_logit", 0.0))
        self.amp_dtype = torch.bfloat16 if amp and self.device.type == "cuda" else None
        self.encoder = None
        if standalone:
            enc = FrozenOmniAudioEncoder(encoder_path)
            keep = self.head.max_layer + 1
            enc.encoder.layers = enc.encoder.layers[:keep]  # pruned: deeper layers never run
            enc.num_layers = keep
            self.encoder = enc.to(self.device).eval()
        for p in self.parameters():
            p.requires_grad = False

    @torch.no_grad()
    def from_taps(self, taps: torch.Tensor, lens: torch.Tensor) -> torch.Tensor:
        """taps (N, len(self.taps), 1280) packed, lens (B,) frames -> (B,) P(complete)."""
        with torch.autocast("cuda", dtype=self.amp_dtype, enabled=self.amp_dtype is not None):
            return torch.sigmoid(self.head(taps, lens).float())

    @torch.no_grad()
    def predict(self, waves: Sequence[Union[np.ndarray, torch.Tensor]]) -> torch.Tensor:
        """List of 16 kHz mono turns (float in [-1, 1]) -> (B,) P(turn complete)."""
        if self.encoder is None:
            raise RuntimeError("built with standalone=False: use from_taps")
        mels: List[torch.Tensor] = []
        for w in waves:
            w = torch.as_tensor(np.asarray(w, dtype=np.float32))[-MAX_SECONDS * SR:]
            mels.append(self.encoder.mel_from_waveform(w.to(self.device)))
        lens = torch.tensor([m.shape[1] for m in mels], device=self.device)
        with torch.autocast("cuda", dtype=self.amp_dtype, enabled=self.amp_dtype is not None):
            out = self.encoder(torch.cat(mels, 1), lens, tap_layers=self.taps,
                               return_llm_features=False)
        return self.from_taps(out["taps"], out["aftercnn_lens"])

    @torch.no_grad()
    def is_complete(self, waves) -> torch.Tensor:
        p = self.predict(waves)
        return torch.logit(p.clamp(1e-6, 1 - 1e-6)) > self.threshold_logit
