#!/usr/bin/env python3
"""
CTC phoneme heads on frozen encoder streams (S x B x T x C).

Vendored here so inference does not depend on OmniHead/ or Encode-explore/.
The designs match OmniHead/phoneme/phoneme_heads.py (ZIPA 127-token CTC).

Every head: layer mix -> sequence model at 13 Hz -> x k upsample -> optional
post layer at 13k Hz -> linear to 127 tokens (index 0 = CTC blank).
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

VOCAB = 127


class QFormerLayerMix(nn.Module):
    def __init__(self, num_streams: int, feature_dim: int, num_queries: int = 8):
        super().__init__()
        self.queries = nn.Parameter(torch.empty(num_queries, feature_dim))
        nn.init.normal_(self.queries, std=0.02)
        self.mix = nn.Parameter(torch.zeros(num_queries))
        self.register_buffer("last_layer_attn", torch.full((num_streams,), 1.0 / num_streams), persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = x.permute(1, 2, 0, 3)
        logits = torch.einsum("nc,btlc->btnl", self.queries, h) * h.size(-1) ** -0.5
        w = logits.softmax(dim=-1)
        w = (w * F.softmax(self.mix, dim=0).view(1, 1, -1, 1)).sum(dim=2)
        self.last_layer_attn.copy_(w.detach().mean(dim=(0, 1)))
        return torch.einsum("btl,btlc->btc", w, h)


class ScalarMix(nn.Module):
    def __init__(self, num_streams):
        super().__init__()
        self.w = nn.Parameter(torch.zeros(num_streams))

    def forward(self, x):
        return torch.einsum("s,sbtc->btc", self.w.softmax(0), x)

    def weights(self):
        return self.w.softmax(0).detach()


class TFStack(nn.Module):
    def __init__(self, d, layers, heads, dropout):
        super().__init__()
        self.pos = nn.Conv1d(d, d, 7, padding=3, groups=d)
        self.layers = nn.ModuleList(
            nn.TransformerEncoderLayer(d, heads, 4 * d, dropout, "gelu", batch_first=True, norm_first=True)
            for _ in range(layers)
        )
        self.norm = nn.LayerNorm(d)

    def forward(self, x, valid):
        m = valid.unsqueeze(-1).to(x.dtype)
        x = x + self.pos((x * m).transpose(1, 2)).transpose(1, 2)
        for layer in self.layers:
            x = layer(x, src_key_padding_mask=~valid)
        return self.norm(x)


class BiLSTMStack(nn.Module):
    def __init__(self, d, layers, dropout):
        super().__init__()
        self.m = nn.LSTM(d, d // 2, num_layers=layers, batch_first=True, bidirectional=True, dropout=dropout)

    def forward(self, x, valid):
        lens = valid.sum(1).cpu()
        p = nn.utils.rnn.pack_padded_sequence(x, lens, batch_first=True, enforce_sorted=False)
        y, _ = self.m(p)
        y, _ = nn.utils.rnn.pad_packed_sequence(y, batch_first=True, total_length=x.size(1))
        return y


def stack(kind, d, layers, heads, dropout):
    if kind == "none" or layers == 0:
        return None
    if kind == "tf":
        return TFStack(d, layers, heads, dropout)
    if kind == "bilstm":
        return BiLSTMStack(d, layers, dropout)
    raise ValueError(kind)


class PhonemeCTCHead(nn.Module):
    def __init__(self, num_streams=32, feature_dim=1280, vocab=VOCAB, mix="qformer", num_queries=8, d=512,
                 backbone="tf", layers=4, heads=8, up="split", k=3, post="none", post_layers=0, dropout=0.1):
        super().__init__()
        self.config = dict(num_streams=num_streams, feature_dim=feature_dim, vocab=vocab, mix=mix,
                           num_queries=num_queries, d=d, backbone=backbone, layers=layers, heads=heads, up=up, k=k,
                           post=post, post_layers=post_layers, dropout=dropout)
        self.k = k
        self.mix = QFormerLayerMix(num_streams, feature_dim, num_queries) if mix == "qformer" else ScalarMix(num_streams)
        if backbone == "none":
            d = feature_dim
            self.inp = nn.Identity()
        else:
            self.inp = nn.Sequential(nn.Linear(feature_dim, d), nn.LayerNorm(d), nn.Dropout(dropout))
        self.body = stack(backbone, d, layers, heads, dropout)
        if up == "split":
            self.up = nn.Linear(d, k * d)
        else:
            self.up = nn.ConvTranspose1d(d, d, kernel_size=k, stride=k)
        self.up_kind = up
        self.post = stack(post, d, post_layers, heads, dropout)
        self.out_norm = nn.LayerNorm(d)
        self.out = nn.Linear(d, vocab)
        nn.init.normal_(self.out.weight, std=0.02)
        nn.init.zeros_(self.out.bias)
        with torch.no_grad():
            self.out.bias[0] = -2.0

    def stream_weights(self):
        return self.mix.last_layer_attn if hasattr(self.mix, "last_layer_attn") else self.mix.weights()

    def forward(self, feats, valid):
        x = F.layer_norm(feats.float(), feats.shape[-1:])
        x = self.inp(self.mix(x))
        if self.body is not None:
            x = self.body(x, valid)
        B, T, D = x.shape
        if self.up_kind == "split":
            x = self.up(x).view(B, T, self.k, D).reshape(B, T * self.k, D)
        else:
            x = self.up(x.transpose(1, 2)).transpose(1, 2)
        ov = valid.repeat_interleave(self.k, dim=1)
        if self.post is not None:
            x = self.post(x, ov)
        return self.out(self.out_norm(x)), ov


def build_head(cfg: dict) -> PhonemeCTCHead:
    return PhonemeCTCHead(**cfg)


def n_params(m):
    return sum(p.numel() for p in m.parameters())


def greedy(logits, ov):
    pred = logits.argmax(-1).cpu().numpy()
    lens = ov.sum(1).cpu().numpy()
    out = []
    for p, L in zip(pred, lens):
        p = p[:L]
        keep = [True] * len(p)
        for i in range(1, len(p)):
            keep[i] = p[i] != p[i - 1]
        out.append([int(t) for t, k in zip(p, keep) if k and t != 0])
    return out
