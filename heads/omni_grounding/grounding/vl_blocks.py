"""Portable OV blocks from YOLO-World / YOLOE, without mmyolo or CLIP.

MaxSigmoid: YOLO-World RepVL-PAN text-gated spatial features
  (yolo_world/models/layers/yolo_bricks.py::MaxSigmoidAttnBlock).

RepRTA: YOLOE residual SwiGLU on frozen text
  (ultralytics/nn/modules/head.py::Residual / SwiGLUFFN).

BN score: YOLO-World v2 / YOLOE BNContrastiveHead — BN on regions, L2 on text.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class MaxSigmoidAttnBlock(nn.Module):
    """Per-location max-over-vocab sigmoid gate, then 3x3 project."""

    def __init__(self, channels: int, guide_channels: int, num_heads: int = 4):
        super().__init__()
        if channels % num_heads:
            raise ValueError(f"channels {channels} not divisible by num_heads {num_heads}")
        self.num_heads = num_heads
        self.head_channels = channels // num_heads
        self.embed_conv = nn.Conv2d(channels, channels, 1, bias=False)
        self.guide_fc = nn.Linear(guide_channels, channels)
        self.bias = nn.Parameter(torch.zeros(num_heads))
        self.project = nn.Conv2d(channels, channels, 3, padding=1, bias=False)

    def forward(self, x: torch.Tensor, guide: torch.Tensor) -> torch.Tensor:
        bsz, _, height, width = x.shape
        if guide.dim() == 2:
            guide = guide.unsqueeze(0).expand(bsz, -1, -1)
        elif guide.shape[0] == 1 and bsz > 1:
            guide = guide.expand(bsz, -1, -1)
        guide = self.guide_fc(guide.float())
        guide = guide.reshape(bsz, -1, self.num_heads, self.head_channels)
        embed = self.embed_conv(x).reshape(bsz, self.num_heads, self.head_channels, height, width)
        attn = torch.einsum("bmchw,bnmc->bmhwn", embed.float(), guide)
        attn = attn.max(dim=-1)[0] / (self.head_channels ** 0.5)
        attn = (attn + self.bias[None, :, None, None]).sigmoid()
        out = self.project(x).reshape(bsz, self.num_heads, -1, height, width)
        out = out * attn.unsqueeze(2).to(dtype=out.dtype)
        return out.reshape(bsz, -1, height, width)


class ResidualTextGate(nn.Module):
    """MaxSigmoid with gamma=0 so a warm-started FPN is identity at step 0."""

    def __init__(self, channels: int, guide_channels: int, num_heads: int = 4):
        super().__init__()
        self.attn = MaxSigmoidAttnBlock(channels, guide_channels, num_heads)
        self.gamma = nn.Parameter(torch.zeros(1))

    def forward(self, x: torch.Tensor, guide: torch.Tensor) -> torch.Tensor:
        return x + self.gamma.to(dtype=x.dtype) * self.attn(x, guide)


class SwiGLUFFN(nn.Module):
    def __init__(self, dim: int, expansion: int = 2):
        super().__init__()
        hidden = expansion * dim
        self.w12 = nn.Linear(dim, hidden)
        self.w3 = nn.Linear(hidden // 2, dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x1, x2 = self.w12(x).chunk(2, dim=-1)
        return self.w3(F.silu(x1) * x2)


class RepRTA(nn.Module):
    """YOLOE text adapter: residual SwiGLU, zero-init so frozen text is unchanged at start."""

    def __init__(self, dim: int, expansion: int = 2):
        super().__init__()
        self.m = SwiGLUFFN(dim, expansion)
        nn.init.zeros_(self.m.w3.weight)
        nn.init.zeros_(self.m.w3.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.m(x)
