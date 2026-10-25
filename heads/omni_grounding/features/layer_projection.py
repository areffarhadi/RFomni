"""Per-tap LayerNorm + Linear projection (1152 -> d_model)."""

from __future__ import annotations

import torch.nn as nn


class LayerProjector(nn.Module):
    def __init__(self, in_dim: int = 1152, d_model: int = 384, num_streams: int = 6):
        super().__init__()
        self.projs = nn.ModuleList(
            [nn.Sequential(nn.LayerNorm(in_dim), nn.Linear(in_dim, d_model)) for _ in range(num_streams)]
        )

    def forward(self, feats):
        """feats: (B, T, S, D) -> list of S tensors (B, T, d_model)."""
        return [self.projs[i](feats[:, :, i]) for i in range(feats.shape[2])]
