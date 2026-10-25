"""Build P3/P4/P5 (and optional P2) from depth-fused 32x32 maps.

Transformer taps are different *depths*, not resolutions. Spatial scales are
created here by learned up/downsampling.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from omni_grounding.features.feature_fusion import DepthFusion
from omni_grounding.features.layer_projection import LayerProjector


class OmniFPN(nn.Module):
    def __init__(
        self,
        feature_dim: int = 1152,
        d_model: int = 384,
        num_streams: int = 6,
        use_p2: bool = False,
    ):
        super().__init__()
        self.d_model = d_model
        self.use_p2 = use_p2
        self.projector = LayerProjector(feature_dim, d_model, num_streams)
        self.fusion = DepthFusion()
        self.c3 = nn.Conv2d(d_model, d_model, 1)
        self.c4 = nn.Sequential(
            nn.Conv2d(d_model, d_model, 3, stride=2, padding=1),
            nn.GELU(),
            nn.Conv2d(d_model, d_model, 3, padding=1),
        )
        self.c5 = nn.Sequential(
            nn.Conv2d(d_model, d_model, 3, stride=2, padding=1),
            nn.GELU(),
            nn.Conv2d(d_model, d_model, 3, stride=2, padding=1),
            nn.GELU(),
            nn.Conv2d(d_model, d_model, 3, padding=1),
        )
        self.lat3 = nn.Conv2d(d_model, d_model, 1)
        self.lat4 = nn.Conv2d(d_model, d_model, 1)
        self.lat5 = nn.Conv2d(d_model, d_model, 1)
        self.out3 = nn.Sequential(nn.Conv2d(d_model, d_model, 3, padding=1), nn.GELU())
        self.out4 = nn.Sequential(nn.Conv2d(d_model, d_model, 3, padding=1), nn.GELU())
        self.out5 = nn.Sequential(nn.Conv2d(d_model, d_model, 3, padding=1), nn.GELU())
        if use_p2:
            self.p2 = nn.Sequential(
                nn.Conv2d(d_model, d_model, 3, padding=1),
                nn.GELU(),
                nn.Conv2d(d_model, d_model, 3, padding=1),
            )

    def _to_map(self, tokens: torch.Tensor, gh: int, gw: int) -> torch.Tensor:
        bsz, n, dim = tokens.shape
        if n != gh * gw:
            tokens = tokens[:, : gh * gw]
        return tokens.transpose(1, 2).reshape(bsz, dim, gh, gw)

    def forward(self, feats: torch.Tensor, grid_hw: tuple[int, int]) -> list[torch.Tensor]:
        """feats (B,T,S,D) -> [P3,P4,P5] or [P2,P3,P4,P5], each (B,C,H,W)."""
        gh, gw = grid_hw
        streams = self.projector(feats)
        f_early, f_mid, f_late = self.fusion(streams)
        c3 = self.c3(self._to_map(f_early, gh, gw))
        c4 = self.c4(self._to_map(f_mid, gh, gw))
        c5 = self.c5(self._to_map(f_late, gh, gw))
        p5 = self.lat5(c5)
        p4 = self.lat4(c4) + F.interpolate(p5, size=c4.shape[-2:], mode="nearest")
        p3 = self.lat3(c3) + F.interpolate(p4, size=c3.shape[-2:], mode="nearest")
        p5 = self.out5(p5)
        p4 = self.out4(p4)
        p3 = self.out3(p3)
        if self.use_p2:
            p2 = self.p2(F.interpolate(p3, scale_factor=2.0, mode="nearest"))
            return [p2, p3, p4, p5]
        return [p3, p4, p5]


class _ResConv(nn.Module):
    """Unfrozen 3x3 residual conv (ViTDet-style local mixing on the token grid)."""

    def __init__(self, c: int):
        super().__init__()
        self.cv1 = nn.Conv2d(c, c, 3, padding=1)
        self.cv2 = nn.Conv2d(c, c, 3, padding=1)
        self.act = nn.GELU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.act(x + self.cv2(self.act(self.cv1(x))))


class VitDetSimpleFPN(nn.Module):
    """ViTDet simple pyramid on frozen 32x32 taps.

    Mix87 has no CNN backbone. This adds *new* unfrozen convs:
    residual mixing at stride 16, learned ConvTranspose P2 (stride 8),
    stride-2 P4/P5. Same [P2,P3,P4,P5] layout as OmniFPN so the YOLO
    head can warm-start box/embed weights.
    """

    def __init__(
        self,
        feature_dim: int = 1152,
        d_model: int = 384,
        num_streams: int = 6,
        use_p2: bool = True,
        n_stem: int = 2,
    ):
        super().__init__()
        self.d_model = d_model
        self.use_p2 = use_p2
        self.projector = LayerProjector(feature_dim, d_model, num_streams)
        self.fusion = DepthFusion()
        self.fuse = nn.Conv2d(d_model * 3, d_model, 1)
        self.stem = nn.Sequential(*[_ResConv(d_model) for _ in range(n_stem)])
        self.p3 = nn.Sequential(
            nn.Conv2d(d_model, d_model, 3, padding=1),
            nn.GELU(),
            nn.Conv2d(d_model, d_model, 3, padding=1),
        )
        self.p2_up = nn.ConvTranspose2d(d_model, d_model, kernel_size=2, stride=2)
        self.p2 = nn.Sequential(_ResConv(d_model), _ResConv(d_model))
        self.p4 = nn.Sequential(
            nn.Conv2d(d_model, d_model, 3, stride=2, padding=1),
            nn.GELU(),
            _ResConv(d_model),
        )
        self.p5 = nn.Sequential(
            nn.Conv2d(d_model, d_model, 3, stride=2, padding=1),
            nn.GELU(),
            nn.Conv2d(d_model, d_model, 3, stride=2, padding=1),
            nn.GELU(),
            nn.Conv2d(d_model, d_model, 3, padding=1),
        )

    def _to_map(self, tokens: torch.Tensor, gh: int, gw: int) -> torch.Tensor:
        bsz, n, dim = tokens.shape
        if n != gh * gw:
            tokens = tokens[:, : gh * gw]
        return tokens.transpose(1, 2).reshape(bsz, dim, gh, gw)

    def forward(self, feats: torch.Tensor, grid_hw: tuple[int, int]) -> list[torch.Tensor]:
        gh, gw = grid_hw
        streams = self.projector(feats)
        f_early, f_mid, f_late = self.fusion(streams)
        fused = self.fuse(torch.cat(
            [self._to_map(f_early, gh, gw), self._to_map(f_mid, gh, gw), self._to_map(f_late, gh, gw)],
            dim=1,
        ))
        stem = self.stem(fused)
        p3 = self.p3(stem)
        p4 = self.p4(stem)
        p5 = self.p5(stem)
        if self.use_p2:
            p2 = self.p2(self.p2_up(stem))
            return [p2, p3, p4, p5]
        return [p3, p4, p5]


class VitDetAllLayerFPN(VitDetSimpleFPN):
    """VitDetSimpleFPN fed by all 27 frozen blocks (learned depth mixes).

    Same module names after the projector, so FPN/box/embed weights from a
    6-tap VitDetSimpleFPN checkpoint load unchanged.
    """

    def __init__(self, feature_dim: int = 1152, d_model: int = 384, num_layers: int = 27, use_p2: bool = True, n_stem: int = 2):
        super().__init__(feature_dim, d_model, num_streams=1, use_p2=use_p2, n_stem=n_stem)
        from omni_grounding.features.all_layers import AllLayerFusion

        del self.projector, self.fusion
        self.all_layers = AllLayerFusion(feature_dim, d_model, num_layers, num_groups=3)

    def _to_map(self, tokens: torch.Tensor, gh: int, gw: int) -> torch.Tensor:
        from omni_grounding.features.all_layers import tokens_to_map

        return tokens_to_map(tokens, gh, gw)

    def forward(self, feats: torch.Tensor, grid_hw: tuple[int, int]) -> list[torch.Tensor]:
        gh, gw = grid_hw
        f_early, f_mid, f_late = self.all_layers(feats)
        fused = self.fuse(torch.cat(
            [self._to_map(f_early, gh, gw), self._to_map(f_mid, gh, gw), self._to_map(f_late, gh, gw)],
            dim=1,
        ))
        stem = self.stem(fused)
        p3 = self.p3(stem)
        p4 = self.p4(stem)
        p5 = self.p5(stem)
        if self.use_p2:
            p2 = self.p2(self.p2_up(stem))
            return [p2, p3, p4, p5]
        return [p3, p4, p5]
