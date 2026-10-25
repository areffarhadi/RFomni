"""Speaker diarization sidecar for the frozen Qwen3-Omni audio encoder.

The head reads the encoder's per-layer hidden states and never writes back into it, so
the `proj2` features the LLM consumes are unchanged and both outputs come out of one
encoder forward pass.

    (B, T, S, 1280)  tapped layers, T at 13 Hz
        -> per-stream adapters      S x (B, T, 128)
        -> frame-level MHFA         (B, T, 256)
        -> Conformer                (B, T, 256)      <- temporal modelling at 13 Hz
        -> transposed conv x4       (B, 4T, 256)     <- ~19 ms boundary resolution
        -> Conformer                (B, 4T, 256)
        -> linear + log-softmax     (B, 4T, 11)      powerset posteriors

The 13 Hz trunk is also exposed as a per-frame speaker embedding, which is exactly the
rate of Qwen3-Omni's audio tokens and is what ``llm_injection`` feeds to the LLM.

Adapters and MHFA follow ``main_train_diarization_wpt_w2vbert_zl389_v3_mhfa.py``, the
strongest configuration in this repo (7.47% DER with a w2v-bert backbone).
"""

from __future__ import annotations

import os
import sys
from typing import List, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from diarizen.models.module.conformer import ConformerEncoder  # noqa: E402
from pyannote.audio.utils.powerset import Powerset  # noqa: E402


class PerStreamAdapters(nn.Module):
    """One small MLP per tapped layer, mapping 1280 -> adapter_dim."""

    def __init__(self, d_model: int, n_streams: int, adapter_dim: int = 128):
        super().__init__()
        self.n_streams = n_streams
        self.layers = nn.ModuleList(
            nn.Sequential(
                nn.Linear(d_model, adapter_dim),
                nn.LayerNorm(adapter_dim),
                nn.ReLU(True),
                nn.Linear(adapter_dim, adapter_dim),
            )
            for _ in range(n_streams)
        )

    def forward(self, feats: torch.Tensor) -> List[torch.Tensor]:
        """(B, T, S, D) -> list of S tensors (B, T, adapter_dim)."""
        return [self.layers[i](feats[:, :, i]) for i in range(self.n_streams)]


class FrameLevelMHFA(nn.Module):
    """Multi-head factorised attention over layer streams, applied per frame.

    Separate softmax weights over streams build a key and a value view; learnable head
    queries then attend into them, so the fusion over layers is content-dependent rather
    than a single fixed weighted sum.
    """

    def __init__(
        self,
        n_streams: int,
        adapter_dim: int = 128,
        num_heads: int = 8,
        compress_dim: int = 64,
        out_dim: int = 256,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.compress_dim = compress_dim
        self.layer_weights_key = nn.Parameter(torch.empty(n_streams).uniform_(-0.1, 0.1))
        self.layer_weights_value = nn.Parameter(torch.empty(n_streams).uniform_(-0.1, 0.1))
        self.key_proj = nn.Linear(adapter_dim, compress_dim)
        self.value_proj = nn.Linear(adapter_dim, compress_dim)
        self.head_queries = nn.Parameter(torch.randn(num_heads, compress_dim) * compress_dim ** -0.5)
        self.out_proj = nn.Linear(num_heads * compress_dim, out_dim)
        self.out_norm = nn.LayerNorm(out_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, streams: List[torch.Tensor]) -> torch.Tensor:
        stacked = torch.stack(streams, dim=0)  # (S, B, T, A)
        S = stacked.shape[0]
        w_k = F.softmax(self.layer_weights_key, dim=0).view(S, 1, 1, 1)
        w_v = F.softmax(self.layer_weights_value, dim=0).view(S, 1, 1, 1)

        K = self.key_proj((stacked * w_k).sum(0))    # (B, T, C)
        V = self.value_proj((stacked * w_v).sum(0))  # (B, T, C)

        scores = torch.einsum("hc,btc->bth", self.head_queries, K) / self.compress_dim ** 0.5
        weights = torch.softmax(scores, dim=-1)
        out = torch.einsum("bth,btc->bthc", weights, V).flatten(2)  # (B, T, H*C)
        return self.out_norm(self.dropout(self.out_proj(out)))


class TemporalUpsampler(nn.Module):
    """Exact integer upsampling in time via transposed convolution."""

    def __init__(self, dim: int, factor: int):
        super().__init__()
        self.factor = factor
        self.conv = nn.ConvTranspose1d(
            dim, dim, kernel_size=2 * factor, stride=factor, padding=factor // 2
        )
        self.norm = nn.LayerNorm(dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        T = x.shape[1]
        y = self.conv(x.transpose(1, 2)).transpose(1, 2)
        return self.norm(y[:, : T * self.factor])


class SpeakerSidecar(nn.Module):
    def __init__(
        self,
        num_streams: int,
        d_model: int = 1280,
        adapter_dim: int = 128,
        mhfa_heads: int = 8,
        compress_dim: int = 64,
        attention_in: int = 256,
        ffn_hidden: int = 1024,
        conformer_heads: int = 4,
        conformer_layers: int = 4,
        refine_layers: int = 2,
        kernel_size: int = 31,
        dropout: float = 0.1,
        upsample: int = 4,
        max_speakers_per_chunk: int = 4,
        max_speakers_per_frame: int = 2,
        speaker_embedding_dim: int = 192,
    ):
        super().__init__()
        self.upsample = upsample
        self.powerset = Powerset(max_speakers_per_chunk, max_speakers_per_frame)
        self.num_classes = self.powerset.num_powerset_classes

        self.adapters = PerStreamAdapters(d_model, num_streams, adapter_dim)
        self.mhfa = FrameLevelMHFA(
            num_streams, adapter_dim, mhfa_heads, compress_dim, attention_in, dropout
        )
        self.trunk = ConformerEncoder(
            attention_in=attention_in, ffn_hidden=ffn_hidden, num_head=conformer_heads,
            num_layer=conformer_layers, kernel_size=kernel_size, dropout=dropout,
            use_posi=False, output_activate_function=False,
        )
        self.upsampler = TemporalUpsampler(attention_in, upsample)
        self.refine = ConformerEncoder(
            attention_in=attention_in, ffn_hidden=ffn_hidden, num_head=conformer_heads,
            num_layer=refine_layers, kernel_size=kernel_size, dropout=dropout,
            use_posi=False, output_activate_function=False,
        )
        self.classifier = nn.Linear(attention_in, self.num_classes)
        self.speaker_embedding = nn.Linear(attention_in, speaker_embedding_dim)

    def forward(self, feats: torch.Tensor, return_trunk: bool = False):
        """feats: (B, T, S, d_model) -> log-posteriors (B, T*upsample, num_classes)."""
        trunk = self.trunk(self.mhfa(self.adapters(feats)))  # (B, T, attention_in) @13Hz
        x = self.refine(self.upsampler(trunk))
        scores = F.log_softmax(self.classifier(x), dim=-1)
        return (scores, trunk) if return_trunk else scores

    @torch.no_grad()
    def diarization_side_outputs(self, feats: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """Per-audio-token signals for the LLM: powerset posteriors and speaker embeddings.

        Both are returned at 13 Hz, i.e. one vector per Qwen3-Omni audio token.
        """
        scores, trunk = self.forward(feats, return_trunk=True)
        B, T4, C = scores.shape
        posteriors = scores.exp().view(B, T4 // self.upsample, self.upsample, C).mean(2)
        return posteriors, self.speaker_embedding(trunk)

    def num_trainable_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)
