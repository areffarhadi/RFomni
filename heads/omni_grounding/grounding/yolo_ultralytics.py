"""YOLOv8 Detect head on frozen mix87 FPN.

Architecture and assignment follow Ultralytics YOLOv8
(https://github.com/ultralytics/ultralytics): decoupled DFL box + class
branches, TAL, CIoU + DFL + BCE. The CSP backbone is replaced by OmniFPN
on frozen Qwen3-Omni taps. Encoder weights are never updated.
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from omni_grounding.features.fpn import OmniFPN


class ConvBNSiLU(nn.Module):
    """Ultralytics `Conv`:  conv + BN + SiLU."""

    def __init__(self, c_in: int, c_out: int, k: int = 3, s: int = 1):
        super().__init__()
        self.conv = nn.Conv2d(c_in, c_out, k, s, k // 2, bias=False)
        self.bn = nn.BatchNorm2d(c_out)
        self.act = nn.SiLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.act(self.bn(self.conv(x)))


def make_anchors(feats: list[torch.Tensor], strides: list[int], offset: float = 0.5):
    """Anchor centers in *grid cells* plus a per-anchor stride column (pixels/cell)."""
    points, stride_t = [], []
    dtype, device = feats[0].dtype, feats[0].device
    for feat, stride in zip(feats, strides):
        h, w = feat.shape[-2:]
        sy, sx = torch.meshgrid(
            torch.arange(h, device=device, dtype=dtype) + offset,
            torch.arange(w, device=device, dtype=dtype) + offset,
            indexing="ij",
        )
        points.append(torch.stack((sx, sy), -1).reshape(-1, 2))
        stride_t.append(torch.full((h * w, 1), float(stride), device=device, dtype=dtype))
    return torch.cat(points, 0), torch.cat(stride_t, 0)


def dist2bbox(distance: torch.Tensor, anchor_points: torch.Tensor) -> torch.Tensor:
    """ltrb distances (grid cells) + anchors (grid cells) → xyxy (grid cells)."""
    lt, rb = distance.chunk(2, -1)
    return torch.cat((anchor_points - lt, anchor_points + rb), -1)


def bbox2dist(anchor_points: torch.Tensor, bbox: torch.Tensor, reg_max: int | None = None) -> torch.Tensor:
    """xyxy (grid cells) → ltrb. Clamp only when DFL bins are in use."""
    x1y1, x2y2 = bbox.chunk(2, -1)
    dist = torch.cat((anchor_points - x1y1, x2y2 - anchor_points), -1)
    if reg_max is not None:
        dist = dist.clamp_(0, reg_max - 0.01)
    return dist


def dfl_decode(pred_dist: torch.Tensor, reg_max: int) -> torch.Tensor:
    """(B, A, 4*reg_max) logits → (B, A, 4) expected ltrb in grid cells."""
    b, a, _ = pred_dist.shape
    proj = torch.arange(reg_max, device=pred_dist.device, dtype=pred_dist.dtype)
    return pred_dist.view(b, a, 4, reg_max).softmax(-1).matmul(proj)


class YoloV8DetectHead(nn.Module):
    """Decoupled DFL box + class head (Ultralytics Detect, one scale each)."""

    def __init__(self, ch: int, num_classes: int, reg_max: int = 16):
        super().__init__()
        self.nc = num_classes
        self.reg_max = reg_max
        c2 = max(16, ch // 4, 4 * reg_max)
        c3 = max(ch, min(num_classes, 100))
        self.box = nn.ModuleList(
            [
                nn.Sequential(
                    ConvBNSiLU(ch, c2, 3),
                    ConvBNSiLU(c2, c2, 3),
                    nn.Conv2d(c2, 4 * reg_max, 1),
                )
            ]
        )
        self.cls = nn.ModuleList(
            [
                nn.Sequential(
                    ConvBNSiLU(ch, c3, 3),
                    ConvBNSiLU(c3, c3, 3),
                    nn.Conv2d(c3, num_classes, 1),
                )
            ]
        )
        self._init_biases()

    def _init_biases(self) -> None:
        for m in self.box:
            nn.init.constant_(m[-1].bias, 1.0)
        for m in self.cls:
            nn.init.constant_(m[-1].bias, math.log(5 / self.nc / (640 / 8) ** 2))

    def add_level(self, ch: int) -> None:
        c2 = max(16, ch // 4, 4 * self.reg_max)
        c3 = max(ch, min(self.nc, 100))
        box = nn.Sequential(
            ConvBNSiLU(ch, c2, 3),
            ConvBNSiLU(c2, c2, 3),
            nn.Conv2d(c2, 4 * self.reg_max, 1),
        )
        cls = nn.Sequential(
            ConvBNSiLU(ch, c3, 3),
            ConvBNSiLU(c3, c3, 3),
            nn.Conv2d(c3, self.nc, 1),
        )
        nn.init.constant_(box[-1].bias, 1.0)
        nn.init.constant_(cls[-1].bias, math.log(5 / self.nc / (640 / 8) ** 2))
        self.box.append(box)
        self.cls.append(cls)

    def forward_level(self, feat: torch.Tensor, i: int) -> tuple[torch.Tensor, torch.Tensor]:
        box = self.box[i](feat).flatten(2).transpose(1, 2)
        cls = self.cls[i](feat).flatten(2).transpose(1, 2)
        return box, cls


class OmniYoloV8Detector(nn.Module):
    """OmniFPN + YOLOv8 Detect. Default P2/P3/P4 at strides 8/16/32."""

    def __init__(
        self,
        feature_dim: int = 1152,
        d_model: int = 256,
        num_streams: int = 6,
        num_classes: int = 80,
        reg_max: int = 16,
        use_p2: bool = True,
        drop_p5: bool = True,
    ):
        super().__init__()
        self.num_classes = num_classes
        self.reg_max = reg_max
        self.use_p2 = use_p2
        self.drop_p5 = drop_p5
        self.fpn = OmniFPN(feature_dim, d_model, num_streams, use_p2=use_p2)
        if use_p2 and drop_p5:
            self.strides = [8, 16, 32]
        elif use_p2:
            self.strides = [8, 16, 32, 64]
        else:
            self.strides = [16, 32, 64]
        n_lvl = len(self.strides)
        self.head = YoloV8DetectHead(d_model, num_classes, reg_max)
        for _ in range(n_lvl - 1):
            self.head.add_level(d_model)

    def _select_pyramid(self, pyramid: list[torch.Tensor]) -> list[torch.Tensor]:
        if self.use_p2 and self.drop_p5:
            return pyramid[:3]
        return pyramid

    def forward(self, feats: torch.Tensor, grid_hw: tuple[int, int]) -> dict:
        pyramid = self._select_pyramid(self.fpn(feats, grid_hw))
        dists, scores = [], []
        for i, feat in enumerate(pyramid):
            box, cls = self.head.forward_level(feat, i)
            dists.append(box)
            scores.append(cls)
        pred_dist = torch.cat(dists, 1)
        pred_scores = torch.cat(scores, 1)
        anchors, stride_t = make_anchors(pyramid, self.strides)
        ltrb = dfl_decode(pred_dist.float(), self.reg_max)
        boxes_cells = dist2bbox(ltrb, anchors)
        boxes_px = boxes_cells * stride_t
        return {
            "feats": pyramid,
            "pred_dist": pred_dist,
            "pred_scores": pred_scores,
            "pred_bboxes": boxes_px,
            "anchor_points": anchors,
            "stride_tensor": stride_t,
            "grids": [(int(f.shape[-2]), int(f.shape[-1])) for f in pyramid],
        }

    def load_fpn_from_ckpt(self, ckpt: dict) -> list[str]:
        sd = ckpt.get("model", ckpt)
        fpn_sd = {k[len("fpn.") :]: v for k, v in sd.items() if k.startswith("fpn.")}
        missing, _ = self.fpn.load_state_dict(fpn_sd, strict=False)
        skip = set(missing)
        return [f"fpn.{k}" for k in fpn_sd if k not in skip]
