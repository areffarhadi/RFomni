"""ViTPose / Sapiens2-style heatmap pose on frozen mix87 taps.

ViTPose (NeurIPS'22): top-down crop + simple deconv/upsample heatmap head.
Sapiens2: same idea on a high-res ViT — final token map, ConvTranspose x2,
then 1x1 to K heatmaps. Encoder stays frozen; only projector + decoder train.

https://github.com/ViTAE-Transformer/ViTPose
https://github.com/facebookresearch/sapiens2
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from omni_grounding.features.feature_fusion import DepthFusion
from omni_grounding.features.layer_projection import LayerProjector

NUM_KPTS = 17


class _DeconvBNReLU(nn.Module):
    """ViTPose SimpleBaseline deconv (k=4, s=2) with BN+ReLU."""

    def __init__(self, cin: int, cout: int):
        super().__init__()
        self.up = nn.ConvTranspose2d(cin, cout, 4, stride=2, padding=1, bias=False)
        self.bn = nn.BatchNorm2d(cout)
        self.act = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.act(self.bn(self.up(x)))


class OmniVitPoseHeatmap(nn.Module):
    def __init__(
        self,
        feature_dim: int = 1152,
        d_model: int = 256,
        num_streams: int = 6,
        num_kpts: int = NUM_KPTS,
        num_deconv: int = 2,
    ):
        super().__init__()
        self.num_kpts = num_kpts
        self.d_model = d_model
        self.projector = LayerProjector(feature_dim, d_model, num_streams)
        self.fusion = DepthFusion()
        self.fuse = nn.Conv2d(d_model * 3, d_model, 1)
        layers: list[nn.Module] = []
        c = d_model
        for _ in range(num_deconv):
            layers.append(_DeconvBNReLU(c, d_model))
            c = d_model
        self.deconv = nn.Sequential(*layers) if layers else nn.Identity()
        self.final = nn.Conv2d(d_model, num_kpts, kernel_size=3, padding=1)

    def _to_map(self, tokens: torch.Tensor, gh: int, gw: int) -> torch.Tensor:
        bsz, n, dim = tokens.shape
        if n != gh * gw:
            tokens = tokens[:, : gh * gw]
        return tokens.transpose(1, 2).reshape(bsz, dim, gh, gw)

    def forward(self, feats: torch.Tensor, grid_hw: tuple[int, int]) -> torch.Tensor:
        gh, gw = grid_hw
        streams = self.projector(feats)
        f_early, f_mid, f_late = self.fusion(streams)
        fused = self.fuse(
            torch.cat(
                [
                    self._to_map(f_early, gh, gw),
                    self._to_map(f_mid, gh, gw),
                    self._to_map(f_late, gh, gw),
                ],
                dim=1,
            )
        )
        return self.final(self.deconv(fused))

    def load_matching(self, ckpt: dict) -> list[str]:
        sd = ckpt.get("model", ckpt)
        own = self.state_dict()
        loaded = []
        for k, v in sd.items():
            dst = k
            if k.startswith("fpn.projector."):
                dst = k[len("fpn.") :]
            elif k.startswith("fpn.fusion."):
                dst = k[len("fpn.") :]
            elif k.startswith("fpn.fuse."):
                dst = k[len("fpn.") :]
            if dst in own and tuple(own[dst].shape) == tuple(v.shape):
                own[dst].copy_(v)
                loaded.append(dst)
        self.load_state_dict(own)
        return loaded


def gaussian_heatmaps(
    kpts_xy: torch.Tensor,
    vis: torch.Tensor,
    hm_hw: tuple[int, int],
    sigma: float = 2.0,
) -> torch.Tensor:
    """kpts_xy (B,K,2) in heatmap pixel coords. vis (B,K) >0 if labeled."""
    bsz, k, _ = kpts_xy.shape
    hh, ww = hm_hw
    device = kpts_xy.device
    yy = torch.arange(hh, device=device, dtype=torch.float32).view(1, 1, hh, 1)
    xx = torch.arange(ww, device=device, dtype=torch.float32).view(1, 1, 1, ww)
    x = kpts_xy[..., 0].view(bsz, k, 1, 1)
    y = kpts_xy[..., 1].view(bsz, k, 1, 1)
    hm = torch.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma * sigma))
    mask = (vis > 0).view(bsz, k, 1, 1).float()
    in_frame = (x >= 0) & (x < ww) & (y >= 0) & (y < hh)
    return hm * mask * in_frame.float()


def heatmap_mse(pred: torch.Tensor, target: torch.Tensor, vis: torch.Tensor) -> torch.Tensor:
    """JointsMSELoss with target weights (ViTPose)."""
    w = (vis > 0).float().view(pred.shape[0], pred.shape[1], 1, 1)
    diff = (pred.float() - target.float()).pow(2) * w
    denom = w.sum().clamp(min=1.0) * pred.shape[-1] * pred.shape[-2]
    return diff.sum() / denom


def decode_heatmap(hm: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Argmax + 0.25 quarter offset (mmpose default). xy heatmap pixels, conf max val."""
    bsz, k, hh, ww = hm.shape
    flat = hm.reshape(bsz, k, -1)
    conf, idx = flat.max(dim=-1)
    ys = (idx // ww).float()
    xs = (idx % ww).float()
    for b in range(bsz):
        for j in range(k):
            x, y = int(xs[b, j].item()), int(ys[b, j].item())
            if 1 <= x < ww - 1 and 1 <= y < hh - 1:
                dx = float(hm[b, j, y, x + 1] - hm[b, j, y, x - 1])
                dy = float(hm[b, j, y + 1, x] - hm[b, j, y - 1, x])
                xs[b, j] += 0.25 * (1.0 if dx > 0 else -1.0)
                ys[b, j] += 0.25 * (1.0 if dy > 0 else -1.0)
    xy = torch.stack([xs, ys], dim=-1)
    return xy, conf
