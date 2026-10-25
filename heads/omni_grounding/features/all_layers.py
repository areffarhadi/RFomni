"""All-block fusion of the frozen mix87 vision tower.

Instead of 6 hand-picked taps, every one of the 27 blocks is normalised and
projected (one batched einsum), then G learned softmax mixes over depth give
G fused token maps (e.g. early / mid / late). The encoder is still frozen; only
the per-block LayerNorm affine, the projections and the mix logits train.
"""

from __future__ import annotations

import math
from typing import Sequence

import torch
import torch.nn as nn
import torch.nn.functional as F

ALL_LAYERS = list(range(27))
# Old 6-tap layout -> group id (early / mid / late), used for warm start.
LEGACY_TAPS = [3, 7, 11, 15, 19, 23]
LEGACY_GROUPS = [0, 0, 0, 1, 1, 2]


def tokens_to_map(tokens: torch.Tensor, gh: int, gw: int, merge: int = 2) -> torch.Tensor:
    """(B, gh*gw, C) tower tokens -> (B, C, gh, gw) image-ordered map.

    The Qwen vision tower emits patches in merge-window order (each 2x2 block is 4
    consecutive tokens, blocks in raster order), not row-major. A plain reshape
    scrambles the layout (v1 heads did this).
    """
    b, n, c = tokens.shape
    x = tokens[:, : gh * gw].reshape(b, gh // merge, gw // merge, merge, merge, c)
    return x.permute(0, 5, 1, 3, 2, 4).reshape(b, c, gh, gw)


class AllLayerFusion(nn.Module):
    def __init__(
        self,
        in_dim: int = 1152,
        d_model: int = 256,
        num_layers: int = 27,
        num_groups: int = 3,
    ):
        super().__init__()
        self.num_layers = num_layers
        self.num_groups = num_groups
        self.ln_weight = nn.Parameter(torch.ones(num_layers, in_dim))
        self.ln_bias = nn.Parameter(torch.zeros(num_layers, in_dim))
        self.proj_weight = nn.Parameter(torch.empty(num_layers, in_dim, d_model))
        self.proj_bias = nn.Parameter(torch.zeros(num_layers, d_model))
        bound = 1.0 / math.sqrt(in_dim)
        nn.init.uniform_(self.proj_weight, -bound, bound)
        # Group g starts centred on its slice of depth.
        centers = torch.linspace(0, num_layers - 1, num_groups + 2)[1:-1]
        depth = torch.arange(num_layers).float()
        width = num_layers / (2.0 * num_groups)
        self.mix_logits = nn.Parameter(-((depth[None] - centers[:, None]) / width) ** 2)

    def forward(self, feats: torch.Tensor) -> list[torch.Tensor]:
        """feats (B, T, L, D) -> list of G tensors (B, T, d_model)."""
        x = F.layer_norm(feats, (feats.shape[-1],))
        x = x * self.ln_weight.to(x.dtype) + self.ln_bias.to(x.dtype)
        x = torch.einsum("btld,ldk->btlk", x, self.proj_weight.to(x.dtype)) + self.proj_bias.to(x.dtype)
        w = F.softmax(self.mix_logits.float(), dim=-1).to(x.dtype)
        mixed = torch.einsum("btlk,gl->bgtk", x, w)
        return [mixed[:, g] for g in range(self.num_groups)]

    @torch.no_grad()
    def load_legacy_taps(
        self,
        sd: dict,
        prefix: str,
        fusion_prefix: str | None = None,
        taps: Sequence[int] = LEGACY_TAPS,
        groups: Sequence[int] = LEGACY_GROUPS,
        boost: float = 6.0,
    ) -> int:
        """Copy an old LayerProjector (6 taps) into the matching blocks.

        `prefix` points at `...projector.projs`. Mix logits are set so each group
        reproduces the old DepthFusion weights; other blocks start near zero weight.
        """
        n = 0
        for i, layer in enumerate(taps):
            lw, lb = sd.get(f"{prefix}.{i}.0.weight"), sd.get(f"{prefix}.{i}.0.bias")
            pw, pb = sd.get(f"{prefix}.{i}.1.weight"), sd.get(f"{prefix}.{i}.1.bias")
            if any(t is None for t in (lw, lb, pw, pb)) or pw.shape[0] != self.proj_weight.shape[-1]:
                continue
            self.ln_weight[layer].copy_(lw)
            self.ln_bias[layer].copy_(lb)
            self.proj_weight[layer].copy_(pw.t())
            self.proj_bias[layer].copy_(pb)
            n += 1
        if n == 0:
            return 0
        old_w = {0: None, 1: None}
        if fusion_prefix is not None:
            ew, mw = sd.get(f"{fusion_prefix}.early_w"), sd.get(f"{fusion_prefix}.mid_w")
            old_w[0] = F.softmax(ew.float(), 0) if ew is not None else None
            old_w[1] = F.softmax(mw.float(), 0) if mw is not None else None
        self.mix_logits.zero_()
        seen = {0: 0, 1: 0, 2: 0}
        for layer, g in zip(taps, groups):
            if g >= self.num_groups:
                continue
            w = old_w.get(g)
            p = float(w[seen[g]]) if w is not None else 1.0 / groups.count(g)
            seen[g] += 1
            self.mix_logits[g, layer] = boost + math.log(max(p, 1e-4))
        return n
