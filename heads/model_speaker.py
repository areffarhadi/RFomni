"""Speaker-embedding (ASV) head for the frozen Qwen3-Omni audio encoder.

This is a second read-only head on the *same* tapped layers as the diarization sidecar,
so one encoder forward pass yields local speaker activity and utterance-level speaker
embeddings at once. The embeddings are what turn per-chunk diarization output into a
globally consistent timeline: instead of stitching chunks by overlap (which cannot
re-identify a speaker who goes silent and returns), we cluster embeddings by voice,
which is what DiariZen's reference pipeline does with an external WeSpeaker model.

Architecture is MHFA (Multi-Head Factorised Attention), following
``USM_FTcode/main_train_simple_sv_wpt_qwen3asr_mhfa.py``:

    taps (B, T, S, 1280)
      -> factorised softmax layer weights build separate Key and Value views
      -> compress both to 128
      -> attention over time from the Key view, 8 heads
      -> pool the Value view with those weights -> (B, 8*128)
      -> project to 256, adapter with residual, deep MLP -> (B, 256)

Roughly 0.79 M parameters at inference. The ArcFace classifier used during training is
a further 1.5 M and is discarded afterwards.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import torch
import torch.nn as nn
import torch.nn.functional as F


class MHFASpeakerHead(nn.Module):
    def __init__(
        self,
        feature_dim: int = 1280,
        num_streams: int = 8,
        num_heads: int = 8,
        compression_dim: int = 128,
        embedding_dim: int = 256,
        adapter_bottleneck: int = 128,
        dropout: float = 0.1,
        key_streams: Optional[Sequence[int]] = None,
    ):
        super().__init__()
        self.num_streams = num_streams
        # key_streams: indices (into the tapped streams) that build the Key view, i.e. decide
        # where to attend in time; the Value view always mixes all streams. None = all (original MHFA).
        self.key_streams = list(key_streams) if key_streams is not None else None
        self.num_heads = num_heads
        self.compression_dim = compression_dim
        self.embedding_dim = embedding_dim

        n_key = len(self.key_streams) if self.key_streams is not None else num_streams
        self.layer_weights_key = nn.Parameter(torch.empty(n_key).uniform_(-0.1, 0.1))
        self.layer_weights_value = nn.Parameter(torch.empty(num_streams).uniform_(-0.1, 0.1))

        self.key_projection = nn.Linear(feature_dim, compression_dim)
        self.value_projection = nn.Linear(feature_dim, compression_dim)
        self.attention_projection = nn.Linear(compression_dim, num_heads)

        self.embedding_projection = nn.Sequential(
            nn.Linear(num_heads * compression_dim, embedding_dim),
            nn.LayerNorm(embedding_dim),
            nn.Dropout(dropout),
        )
        self.adapter = nn.Sequential(
            nn.Linear(embedding_dim, adapter_bottleneck),
            nn.BatchNorm1d(adapter_bottleneck),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(adapter_bottleneck, embedding_dim),
        )
        self.adapter_norm = nn.LayerNorm(embedding_dim)
        self.embedding_layer = nn.Sequential(
            nn.Linear(embedding_dim, embedding_dim),
            nn.BatchNorm1d(embedding_dim),
            nn.PReLU(),
            nn.Dropout(dropout),
            nn.Linear(embedding_dim, embedding_dim),
        )
        self.dropout = nn.Dropout(dropout)

    def forward(self, feats: torch.Tensor, lengths: Optional[torch.Tensor] = None) -> torch.Tensor:
        """feats: (B, T, S, D) tapped hidden states -> (B, embedding_dim).

        ``lengths`` optionally gives the valid frame count per item so that padded
        frames are excluded from the attention pooling.
        """
        stacked = feats.permute(2, 0, 1, 3)  # (S, B, T, D)
        S = stacked.shape[0]
        key_stack = stacked if self.key_streams is None else stacked[self.key_streams]
        w_k = F.softmax(self.layer_weights_key, dim=0).view(key_stack.shape[0], 1, 1, 1)
        w_v = F.softmax(self.layer_weights_value, dim=0).view(S, 1, 1, 1)

        K = self.dropout(self.key_projection((key_stack * w_k).sum(0)))  # (B, T, C)
        V = self.dropout(self.value_projection((stacked * w_v).sum(0)))  # (B, T, C)

        logits = self.attention_projection(K)  # (B, T, H)
        if lengths is not None:
            T = feats.shape[1]
            pad = torch.arange(T, device=feats.device)[None, :] >= lengths[:, None]
            logits = logits.masked_fill(pad[:, :, None], float("-inf"))
        attn = F.softmax(logits, dim=1)

        pooled = torch.einsum("bth,btc->bhc", attn, V).flatten(1)  # (B, H*C)

        x = self.embedding_projection(pooled)
        x = self.adapter_norm(x + self.adapter(x))
        return self.embedding_layer(x)

    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters())


class ArcFaceHead(nn.Module):
    """Additive angular margin softmax. Training only; dropped at inference."""

    def __init__(self, embedding_dim: int, num_classes: int, scale: float = 32.0,
                 margin: float = 0.2):
        super().__init__()
        self.weight = nn.Parameter(torch.empty(num_classes, embedding_dim))
        nn.init.xavier_uniform_(self.weight)
        self.scale = scale
        self.set_margin(margin)

    def set_margin(self, margin: float) -> None:
        """Margin is usually warmed up from 0; large margins destabilise early training."""
        self.margin = margin
        self.cos_m = math.cos(margin)
        self.sin_m = math.sin(margin)
        self.th = math.cos(math.pi - margin)
        self.mm = math.sin(math.pi - margin) * margin

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor):
        cosine = F.linear(
            F.normalize(embeddings, dim=1, eps=1e-8),
            F.normalize(self.weight, dim=1, eps=1e-8),
        ).clamp(-1 + 1e-7, 1 - 1e-7)

        sine = torch.sqrt((1.0 - cosine.pow(2)).clamp(0, 1))
        phi = torch.where(
            cosine > self.th,
            (cosine * self.cos_m - sine * self.sin_m).clamp(-1, 1),
            cosine - self.mm,
        )
        one_hot = torch.zeros_like(cosine).scatter_(1, labels.view(-1, 1), 1.0)
        logits = self.scale * (one_hot * phi + (1.0 - one_hot) * cosine)
        return F.cross_entropy(logits, labels), cosine
