"""Video face-attack head on a frozen vision encoder.

The backbone in He et al. (CVPR Workshop 2024, arXiv:2404.08450) is a ResNet-50.
Here that backbone is replaced by the frozen mix87 vision tower. What sits on
top is their head: one pooled feature, a 2-way linear classifier (live = 0,
attack = 1), and the optional single-center loss from their training code
(Li et al., CVPR 2021; weight 0.001 in the challenge repo).

One shared feature serves both attack types, with a separate classifier for
each, matching the challenge protocols rather than a single mixed label:

    spoof     physical attacks (print, replay)     protocol 2.1
    deepfake  digital attacks                      protocol 2.2

A live clip supervises both classifiers. An attack clip supervises only its
own classifier. The joint score used at inference is the larger of the two
attack scores (protocol 1: any attack versus live).

Input features are tapped encoder blocks, shape (B, frames, tokens, streams, D).
Tokens are averaged, streams are mixed with a learned softmax, and frames are
averaged. The encoder is not part of this module and must stay frozen.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class SingleCenterLoss(nn.Module):
    """Pull live features to one center and hold attacks beyond a margin.

    Matches ``losses/single_center_loss.py`` in
    https://github.com/Xianhua-He/cvpr2024-face-anti-spoofing-challenge
    (MIT). ``attack`` is 1 for an attack and 0 for live.
    """

    def __init__(self, feat_dim: int, margin: float = 0.3):
        super().__init__()
        self.margin = margin * feat_dim ** 0.5
        self.center = nn.Parameter(torch.randn(feat_dim))

    def forward(self, features: torch.Tensor, attack: torch.Tensor) -> torch.Tensor:
        dist = (features - self.center).pow(2).sum(dim=-1).clamp(min=1e-12).sqrt()
        live = attack == 0
        fake = attack == 1
        dist_live = dist[live].mean() if live.any() else features.new_zeros(())
        dist_fake = dist[fake].mean() if fake.any() else features.new_zeros(())
        return dist_live + (dist_live - dist_fake + self.margin).clamp(min=0)


class VideoFASHead(nn.Module):
    def __init__(self, feature_dim: int = 1152, num_streams: int = 8, live_weight: float = 1.0):
        super().__init__()
        self.feature_dim = feature_dim
        self.stream_weights = nn.Parameter(torch.zeros(num_streams))
        self.spoof = nn.Linear(feature_dim, 2)
        self.deepfake = nn.Linear(feature_dim, 2)
        self.center_loss = SingleCenterLoss(feature_dim)
        self.register_buffer("ce_weight", torch.tensor([live_weight, 1.0], dtype=torch.float32))

    def embed(self, feats: torch.Tensor) -> torch.Tensor:
        """feats (B, frames, tokens, streams, D) -> (B, D)."""
        if feats.ndim != 5:
            raise ValueError(f"expected (B, frames, tokens, streams, D), got {tuple(feats.shape)}")
        pooled = feats.float().mean(dim=2)
        mix = torch.softmax(self.stream_weights, dim=0)
        frame = (pooled * mix.view(1, 1, -1, 1)).sum(dim=2)
        return frame.mean(dim=1)

    def logits(self, features: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        return self.spoof(features), self.deepfake(features)

    def scores(self, features: torch.Tensor) -> dict[str, torch.Tensor]:
        """Higher means more likely an attack. Joint is the max of the two."""
        spoof, deepfake = self.logits(features)
        spoof_s = spoof[:, 1] - spoof[:, 0]
        deep_s = deepfake[:, 1] - deepfake[:, 0]
        return {"spoof": spoof_s, "deepfake": deep_s, "joint": torch.maximum(spoof_s, deep_s)}

    def forward(
        self,
        feats: torch.Tensor,
        spoof_y: torch.Tensor,
        deepfake_y: torch.Tensor,
        spoof_mask: torch.Tensor,
        deepfake_mask: torch.Tensor,
        center_weight: float = 0.001,
    ) -> tuple[torch.Tensor, dict]:
        features = self.embed(feats)
        spoof_logits, deep_logits = self.logits(features)
        loss_spoof = _masked_ce(spoof_logits, spoof_y, spoof_mask, self.ce_weight)
        loss_deep = _masked_ce(deep_logits, deepfake_y, deepfake_mask, self.ce_weight)
        attack = ((spoof_y == 1) | (deepfake_y == 1)).long()
        loss_center = self.center_loss(features, attack)
        loss = loss_spoof + loss_deep + center_weight * loss_center
        return loss, {
            "loss_spoof": float(loss_spoof.detach()),
            "loss_deepfake": float(loss_deep.detach()),
            "loss_center": float(loss_center.detach()),
            "features": features,
        }


def _masked_ce(logits: torch.Tensor, target: torch.Tensor, mask: torch.Tensor, weight: torch.Tensor) -> torch.Tensor:
    per = F.cross_entropy(logits, target.long(), weight=weight, reduction="none")
    mask = mask.float()
    return (per * mask).sum() / mask.sum().clamp(min=1.0)
