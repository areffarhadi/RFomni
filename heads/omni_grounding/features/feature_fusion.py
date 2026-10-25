"""Depth-wise fusion of projected taps. All maps stay at native 32x32."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class DepthFusion(nn.Module):
    """F_early = L3,L7,L11; F_mid = L15,L19; F_late = L23. Learnable softmax mix."""

    def __init__(self):
        super().__init__()
        self.early_w = nn.Parameter(torch.zeros(3))
        self.mid_w = nn.Parameter(torch.zeros(2))

    def forward(self, streams: list[torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        we = F.softmax(self.early_w, dim=0)
        wm = F.softmax(self.mid_w, dim=0)
        f_early = we[0] * streams[0] + we[1] * streams[1] + we[2] * streams[2]
        f_mid = wm[0] * streams[3] + wm[1] * streams[4]
        f_late = streams[5]
        return f_early, f_mid, f_late
