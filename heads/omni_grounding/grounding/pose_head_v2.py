"""Pose v2 head on the frozen mix87 tower (top-down heatmaps).

v1 (AP 0.237 / AP50 0.630 with GT boxes): 6 taps, 256x256 square crop (16x16
tokens), two deconvs, argmax + quarter offset.

v2:
  * all 27 blocks, learned depth mixes (omni_grounding.features.all_layers)
  * 384x288 (H x W) crop, the human aspect ratio -> 24x18 tokens on the body
  * N trainable transformer blocks over the fused tokens: the frozen tower gives
    local appearance, these blocks add the global body context needed for
    left/right and occluded joints
  * deconv x2 -> 96x72 heatmaps, UDP targets, DARK decoding, flip test
Encoder stays frozen.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from omni_grounding.features.all_layers import AllLayerFusion, tokens_to_map

NUM_KPTS = 17


class _Block(nn.Module):
    def __init__(self, d: int, heads: int, mlp: float = 4.0, drop_path: float = 0.0):
        super().__init__()
        self.n1 = nn.LayerNorm(d)
        self.attn = nn.MultiheadAttention(d, heads, batch_first=True)
        self.n2 = nn.LayerNorm(d)
        self.mlp = nn.Sequential(nn.Linear(d, int(d * mlp)), nn.GELU(), nn.Linear(int(d * mlp), d))
        self.drop_path = drop_path

    def _dp(self, x):
        if not self.training or self.drop_path <= 0:
            return x
        keep = torch.rand(x.shape[0], 1, 1, device=x.device) >= self.drop_path
        return x * keep / (1 - self.drop_path)

    def forward(self, x):
        h = self.n1(x)
        x = x + self._dp(self.attn(h, h, h, need_weights=False)[0])
        return x + self._dp(self.mlp(self.n2(x)))


class _DeconvBNReLU(nn.Module):
    def __init__(self, cin: int, cout: int):
        super().__init__()
        self.up = nn.ConvTranspose2d(cin, cout, 4, stride=2, padding=1, bias=False)
        self.bn = nn.BatchNorm2d(cout)
        self.act = nn.ReLU(inplace=True)

    def forward(self, x):
        return self.act(self.bn(self.up(x)))


class OmniPoseV2(nn.Module):
    def __init__(
        self,
        feature_dim: int = 1152,
        d_model: int = 384,
        num_layers: int = 27,
        num_blocks: int = 4,
        heads: int = 6,
        grid_hw: tuple[int, int] = (24, 18),
        deconv_ch: int = 256,
        num_kpts: int = NUM_KPTS,
        drop_path: float = 0.1,
        num_sources: int = 1,
    ):
        super().__init__()
        self.grid_hw = grid_hw
        self.num_kpts = num_kpts
        self.num_sources = num_sources
        self.all_layers = AllLayerFusion(feature_dim, d_model, num_layers, num_groups=3)
        self.fuse = nn.Linear(3 * d_model, d_model)
        self.pos = nn.Parameter(torch.zeros(1, grid_hw[0] * grid_hw[1], d_model))
        nn.init.trunc_normal_(self.pos, std=0.02)
        dpr = np.linspace(0, drop_path, num_blocks).tolist()
        self.blocks = nn.ModuleList([_Block(d_model, heads, drop_path=p) for p in dpr])
        self.norm = nn.LayerNorm(d_model)
        self.deconv = nn.Sequential(_DeconvBNReLU(d_model, deconv_ch), _DeconvBNReLU(deconv_ch, deconv_ch))
        # One 1x1 output per dataset (ViTPose+ style): joint definitions differ slightly
        # between COCO / CrowdPose / MPII. Source 0 (COCO) is the one used at inference.
        self.final = nn.Conv2d(deconv_ch, num_kpts * num_sources, 1)

    def forward(self, feats: torch.Tensor, source: torch.Tensor | None = None) -> torch.Tensor:
        """(B, K, h, w) heatmaps from the head of `source` (B,) (default: source 0)."""
        gh, gw = self.grid_hw
        x = self.fuse(torch.cat(self.all_layers(feats), dim=-1)) + self.pos
        for blk in self.blocks:
            x = blk(x)
        x = tokens_to_map(self.norm(x), gh, gw)
        hm = self.final(self.deconv(x))
        hm = hm.view(hm.shape[0], self.num_sources, self.num_kpts, *hm.shape[-2:])
        if source is None:
            return hm[:, 0]
        return hm[torch.arange(hm.shape[0], device=hm.device), source]


# ---------------------------------------------------------------- targets / loss


def udp_heatmaps(kpts_xy: torch.Tensor, vis: torch.Tensor, input_hw, hm_hw, sigma: float = 2.0) -> torch.Tensor:
    """UDP Gaussian targets. kpts in input pixels; (in-1)/(hm-1) scale (Huang et al. 2020)."""
    bsz, k, _ = kpts_xy.shape
    (ih, iw), (hh, ww) = input_hw, hm_hw
    x = kpts_xy[..., 0] * (ww - 1) / (iw - 1)
    y = kpts_xy[..., 1] * (hh - 1) / (ih - 1)
    yy = torch.arange(hh, device=kpts_xy.device, dtype=torch.float32).view(1, 1, hh, 1)
    xx = torch.arange(ww, device=kpts_xy.device, dtype=torch.float32).view(1, 1, 1, ww)
    hm = torch.exp(-((xx - x[..., None, None]) ** 2 + (yy - y[..., None, None]) ** 2) / (2 * sigma**2))
    inside = (x >= 0) & (x <= ww - 1) & (y >= 0) & (y <= hh - 1)
    return hm * ((vis > 0) & inside).float()[..., None, None]


def heatmap_mse(pred: torch.Tensor, target: torch.Tensor, weight: torch.Tensor) -> torch.Tensor:
    """mmpose KeypointMSELoss(use_target_weight=True): 0.5 * mean over joints of masked MSE."""
    w = weight.float()[..., None, None]
    return 0.5 * ((pred.float() - target) ** 2 * w).mean()


# ---------------------------------------------------------------- decoding


def flip_heatmaps(hm: torch.Tensor, flip_index) -> torch.Tensor:
    """Heatmaps predicted on a mirrored crop -> unmirrored, with joint swap (UDP: no shift)."""
    return hm.flip(-1)[:, flip_index]


def dark_decode(hm: torch.Tensor, blur_kernel: int = 11) -> tuple[np.ndarray, np.ndarray]:
    """DARK (Zhang et al. 2020): argmax, Gaussian-smoothed log heatmap, Newton step.

    Returns xy in heatmap pixels (B, K, 2) and max values (B, K).
    """
    hm = hm.float()
    bsz, k, hh, ww = hm.shape
    conf, idx = hm.reshape(bsz, k, -1).max(-1)
    xs, ys = (idx % ww).float(), (idx // ww).float()
    sigma = 0.3 * ((blur_kernel - 1) * 0.5 - 1) + 0.8
    r = blur_kernel // 2
    g = torch.exp(-torch.arange(-r, r + 1, device=hm.device, dtype=torch.float32) ** 2 / (2 * sigma**2))
    g = g / g.sum()
    x = hm.reshape(bsz * k, 1, hh, ww)
    orig_max = x.amax((2, 3), keepdim=True)
    x = F.conv2d(F.pad(x, (r, r, 0, 0), mode="replicate"), g.view(1, 1, 1, -1))
    x = F.conv2d(F.pad(x, (0, 0, r, r), mode="replicate"), g.view(1, 1, -1, 1))
    x = x * orig_max / x.amax((2, 3), keepdim=True).clamp(min=1e-10)
    x = torch.log(x.clamp(min=1e-10)).reshape(bsz, k, hh, ww)
    x = F.pad(x, (1, 1, 1, 1), mode="replicate")
    px, py = xs.long() + 1, ys.long() + 1
    b = torch.arange(bsz, device=hm.device)[:, None].expand(bsz, k)
    j = torch.arange(k, device=hm.device)[None].expand(bsz, k)

    def at(dy, dx):
        return x[b, j, py + dy, px + dx]

    dxv = 0.5 * (at(0, 1) - at(0, -1))
    dyv = 0.5 * (at(1, 0) - at(-1, 0))
    dxx = at(0, 1) - 2 * at(0, 0) + at(0, -1)
    dyy = at(1, 0) - 2 * at(0, 0) + at(-1, 0)
    dxy = 0.25 * (at(1, 1) - at(1, -1) - at(-1, 1) + at(-1, -1))
    det = dxx * dyy - dxy**2
    ok = det.abs() > 1e-12
    inv_det = torch.where(ok, 1.0 / det.where(ok, torch.ones_like(det)), torch.zeros_like(det))
    ox = -(dyy * dxv - dxy * dyv) * inv_det
    oy = -(-dxy * dxv + dxx * dyv) * inv_det
    ox, oy = ox.clamp(-1, 1), oy.clamp(-1, 1)
    xy = torch.stack([xs + ox, ys + oy], -1)
    return xy.cpu().numpy(), conf.cpu().numpy()


def heatmap_to_input(xy: np.ndarray, input_hw, hm_hw) -> np.ndarray:
    (ih, iw), (hh, ww) = input_hw, hm_hw
    out = xy.copy()
    out[..., 0] *= (iw - 1) / (ww - 1)
    out[..., 1] *= (ih - 1) / (hh - 1)
    return out


def num_params(m: nn.Module) -> float:
    return sum(p.numel() for p in m.parameters() if p.requires_grad) / 1e6

