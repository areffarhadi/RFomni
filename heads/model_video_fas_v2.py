"""Heads of the v2 video spoof + deepfake sidecar (frozen mix87 vision tower).

    MultiLayerMLPHead  pooled: token mean of all 27 blocks, each block normalised
                       and projected on its own, concatenated, 2-layer MLP
    TokenFASHead       token level: MHFA (model_speaker.MHFASpeakerHead) attends
                       over the 256 tokens of 9 blocks, then two classifiers

Both score one frame. Two 2-way classifiers share the embedding: live vs
physical spoof, and live vs deepfake. Scores are logit differences, higher
means attack; the joint score is the larger of the two. Training code:
train_video_fas_v2.py (pooled) and train_video_fas_tokens.py (token level).
"""
from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from model_speaker import MHFASpeakerHead


class MultiLayerMLPHead(nn.Module):
    """Every block keeps its own normalisation and projection; the MLP sees all of them.

    The v1 heads mix blocks with one softmax, a convex combination that cannot
    weigh a direction from block 6 against another from block 20.
    """

    def __init__(self, feature_dim=1152, num_streams=27, proj_dim=128, hidden=512, dropout=0.2,
                 live_weight=1.0):
        super().__init__()
        self.norm = nn.LayerNorm(feature_dim)
        self.layer_norms = nn.Parameter(torch.ones(num_streams, feature_dim))
        self.layer_bias = nn.Parameter(torch.zeros(num_streams, feature_dim))
        self.proj = nn.Parameter(torch.randn(num_streams, feature_dim, proj_dim) / math.sqrt(feature_dim))
        self.mlp = nn.Sequential(nn.GELU(), nn.Dropout(dropout), nn.Linear(num_streams * proj_dim, hidden),
                                 nn.GELU(), nn.Dropout(dropout))
        self.spoof = nn.Linear(hidden, 2)
        self.deepfake = nn.Linear(hidden, 2)
        self.register_buffer("ce_weight", torch.tensor([live_weight, 1.0], dtype=torch.float32))

    def embed(self, feats):
        """feats (B, frames, tokens, streams, D) -> (B, frames, hidden)."""
        x = feats.float().mean(dim=2)
        x = F.layer_norm(x, (x.shape[-1],)) * self.layer_norms + self.layer_bias
        x = torch.einsum("bfsd,sdk->bfsk", x, self.proj).flatten(2)
        return self.mlp(x)

    def clip_scores(self, feats):
        h = self.embed(feats)
        spoof = self.spoof(h).mean(dim=1)
        deep = self.deepfake(h).mean(dim=1)
        s, d = spoof[:, 1] - spoof[:, 0], deep[:, 1] - deep[:, 0]
        return {"spoof": s, "deepfake": d, "joint": torch.maximum(s, d)}

    def forward(self, feats, spoof_y, deep_y, spoof_m, deep_m, _aux_weight=0.0):
        h = self.embed(feats)
        spoof = self.spoof(h).mean(dim=1)
        deep = self.deepfake(h).mean(dim=1)
        loss = _masked_ce(spoof, spoof_y, spoof_m, self.ce_weight) + _masked_ce(deep, deep_y, deep_m, self.ce_weight)
        return loss, {}


class TokenFASHead(nn.Module):
    def __init__(self, feature_dim=1152, num_streams=9, num_heads=8, compression_dim=128, embedding_dim=256,
                 dropout=0.1, live_weight=1.0):
        super().__init__()
        self.norm = nn.LayerNorm(feature_dim, elementwise_affine=False)
        self.mhfa = MHFASpeakerHead(feature_dim, num_streams, num_heads, compression_dim, embedding_dim,
                                    adapter_bottleneck=128, dropout=dropout)
        self.spoof = nn.Linear(embedding_dim, 2)
        self.deepfake = nn.Linear(embedding_dim, 2)
        self.register_buffer("ce_weight", torch.tensor([live_weight, 1.0], dtype=torch.float32))

    def scores(self, feats):
        """feats (B, tokens, streams, D) -> per-frame scores, higher = attack."""
        h = self.mhfa(self.norm(feats.float()))
        s, d = self.spoof(h), self.deepfake(h)
        s, d = s[:, 1] - s[:, 0], d[:, 1] - d[:, 0]
        return {"spoof": s, "deepfake": d, "joint": torch.maximum(s, d)}

    def forward(self, feats, spoof_y, deep_y, spoof_m, deep_m):
        h = self.mhfa(self.norm(feats.float()))
        loss = _masked_ce(self.spoof(h), spoof_y, spoof_m, self.ce_weight)
        return loss + _masked_ce(self.deepfake(h), deep_y, deep_m, self.ce_weight)


def _masked_ce(logits, target, mask, weight):
    per = F.cross_entropy(logits, target.long(), weight=weight, reduction="none")
    return (per * mask).sum() / mask.sum().clamp(min=1.0)
