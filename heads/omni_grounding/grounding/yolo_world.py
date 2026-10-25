"""YOLO-World-style open-vocab head on the closed-set YOLOv8 Detect.

Keeps OmniFPN + DFL boxes from `localize_yolo_ultralytics`. Replaces the
80-way classifier with a CLIP-space embedding and cosine scores.
Encoder stays frozen. CLIP is text-only.
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from omni_grounding.features.fpn import OmniFPN, VitDetAllLayerFPN, VitDetSimpleFPN
from omni_grounding.grounding.vl_blocks import RepRTA, ResidualTextGate
from omni_grounding.grounding.yolo_ultralytics import (
    ConvBNSiLU,
    YoloV8DetectHead,
    dist2bbox,
    dfl_decode,
    make_anchors,
)


class OmniYoloWorldDetector(nn.Module):
    def __init__(
        self,
        feature_dim: int = 1152,
        d_model: int = 256,
        num_streams: int = 6,
        text_dim: int = 512,
        reg_max: int = 16,
        use_p2: bool = True,
        drop_p5: bool = True,
        fpn_kind: str = "omni",
        score_kind: str = "cosine",
        use_reprta: bool = False,
        use_text_gate: bool = False,
    ):
        super().__init__()
        self.text_dim = text_dim
        self.reg_max = reg_max
        self.use_p2 = use_p2
        self.drop_p5 = drop_p5
        self.fpn_kind = fpn_kind
        self.score_kind = score_kind
        if fpn_kind == "vitdet":
            self.fpn = VitDetSimpleFPN(feature_dim, d_model, num_streams, use_p2=use_p2)
        elif fpn_kind == "vitdet_all":
            self.fpn = VitDetAllLayerFPN(feature_dim, d_model, num_layers=num_streams, use_p2=use_p2)
        else:
            self.fpn = OmniFPN(feature_dim, d_model, num_streams, use_p2=use_p2)
        if use_p2 and drop_p5:
            self.strides = [8, 16, 32]
        elif use_p2:
            self.strides = [8, 16, 32, 64]
        else:
            self.strides = [16, 32, 64]
        n_lvl = len(self.strides)
        # Same box module tree as OmniYoloV8Detector so closed-set `head.box.*` loads.
        box_src = YoloV8DetectHead(d_model, num_classes=80, reg_max=reg_max)
        for _ in range(n_lvl - 1):
            box_src.add_level(d_model)
        self.head = nn.Module()
        self.head.box = box_src.box
        c3 = max(d_model, 256)
        self.head.embed = nn.ModuleList(
            [
                nn.Sequential(
                    ConvBNSiLU(d_model, c3, 3),
                    ConvBNSiLU(c3, c3, 3),
                    nn.Conv2d(c3, text_dim, 1),
                )
                for _ in range(n_lvl)
            ]
        )
        for m in self.head.embed:
            nn.init.normal_(m[-1].weight, std=0.01)
            nn.init.zeros_(m[-1].bias)
        # CLIP temperature; YOLO Detect cls bias so unused cells start as background.
        if score_kind == "bn":
            self.embed_bn = nn.BatchNorm1d(text_dim)
            self.logit_scale = nn.Parameter(torch.tensor(-1.0))
        else:
            self.embed_bn = None
            self.logit_scale = nn.Parameter(torch.tensor(math.log(1.0 / 0.07)))
        self.logit_shift = nn.Parameter(
            torch.tensor(math.log(5 / 80 / (640 / 8) ** 2))
        )
        self.reprta = RepRTA(text_dim) if use_reprta else None
        self.text_gate = (
            nn.ModuleList([ResidualTextGate(d_model, text_dim) for _ in range(n_lvl)])
            if use_text_gate
            else None
        )

    def _select_pyramid(self, pyramid: list[torch.Tensor]) -> list[torch.Tensor]:
        if self.use_p2 and self.drop_p5:
            return pyramid[:3]
        return pyramid

    def refine_text(self, text_emb: torch.Tensor) -> torch.Tensor:
        t = text_emb.float()
        if self.reprta is not None:
            t = self.reprta(t)
        return F.normalize(t, dim=-1)

    def encode_pyramid(
        self,
        feats: torch.Tensor,
        grid_hw: tuple[int, int],
        text_emb: torch.Tensor | None = None,
    ) -> dict:
        pyramid = self._select_pyramid(self.fpn(feats, grid_hw))
        if self.text_gate is not None and text_emb is not None:
            pyramid = [gate(feat, text_emb) for gate, feat in zip(self.text_gate, pyramid)]
        dists, embeds = [], []
        for i, feat in enumerate(pyramid):
            dists.append(self.head.box[i](feat).flatten(2).transpose(1, 2))
            embeds.append(self.head.embed[i](feat).flatten(2).transpose(1, 2))
        pred_dist = torch.cat(dists, 1)
        pred_embed = torch.cat(embeds, 1).float()
        if self.score_kind != "bn":
            pred_embed = F.normalize(pred_embed, dim=-1)
        anchors, stride_t = make_anchors(pyramid, self.strides)
        boxes_px = dist2bbox(dfl_decode(pred_dist.float(), self.reg_max), anchors) * stride_t
        return {
            "feats": pyramid,
            "pred_dist": pred_dist,
            "pred_embed": pred_embed,
            "pred_bboxes": boxes_px,
            "anchor_points": anchors,
            "stride_tensor": stride_t,
            "grids": [(int(f.shape[-2]), int(f.shape[-1])) for f in pyramid],
        }

    def score_text(self, pred_embed: torch.Tensor, text_emb: torch.Tensor) -> torch.Tensor:
        text = self.refine_text(text_emb)
        scale = self.logit_scale.exp().clamp(max=20.0)
        vis = pred_embed.float()
        if self.score_kind == "bn" and self.embed_bn is not None:
            vis = self.embed_bn(vis.transpose(1, 2)).transpose(1, 2)
        return vis @ text.transpose(-1, -2) * scale + self.logit_shift

    def forward(self, feats: torch.Tensor, grid_hw: tuple[int, int], text_emb: torch.Tensor) -> dict:
        out = self.encode_pyramid(feats, grid_hw, text_emb)
        out["pred_scores"] = self.score_text(out["pred_embed"], text_emb)
        return out

    def load_closed_yolo(self, ckpt: dict) -> list[str]:
        """Copy FPN + DFL box weights from closed-set YOLOv8 `best.pt`."""
        sd = ckpt.get("model", ckpt)
        own = self.state_dict()
        loaded = []
        for k, v in sd.items():
            if not (k.startswith("fpn.") or k.startswith("head.box.")):
                continue
            if k in own and tuple(own[k].shape) == tuple(v.shape):
                own[k].copy_(v)
                loaded.append(k)
        self.load_state_dict(own)
        if self.fpn_kind == "vitdet_all" and "fpn.projector.projs.0.1.weight" in sd:
            n = self.fpn.all_layers.load_legacy_taps(sd, "fpn.projector.projs", "fpn.fusion")
            loaded.extend(f"fpn.all_layers[tap{i}]" for i in range(n))
        return loaded

    def load_matching(self, ckpt: dict) -> list[str]:
        """Load every same-shaped tensor (OV stage-2: FPN + box + CLIP embed).

        A 6-tap `fpn.projector` is mapped into `fpn.all_layers` when present.
        """
        sd = ckpt.get("model", ckpt)
        own = self.state_dict()
        loaded = []
        for k, v in sd.items():
            if k in own and tuple(own[k].shape) == tuple(v.shape):
                own[k].copy_(v)
                loaded.append(k)
        self.load_state_dict(own)
        if self.fpn_kind == "vitdet_all" and "fpn.projector.projs.0.1.weight" in sd:
            n = self.fpn.all_layers.load_legacy_taps(sd, "fpn.projector.projs", "fpn.fusion")
            loaded.extend(f"fpn.all_layers[tap{i}]" for i in range(n))
        return loaded
