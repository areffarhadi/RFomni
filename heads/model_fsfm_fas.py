"""FS-Adapter head on a frozen multilayer vision encoder.

Wang et al. (CVPR 2025, FSFM / FS-VFM) put a bottleneck adapter on a pooled
ViT embedding, classify the concatenation of that embedding and the adapter
output, and add a real-anchor contrastive loss on the bottleneck
(https://github.com/wolo-wolo/FSFM-CVPR25, branch FSVFM-extension). Their ViT
is fine-tuned. Here the backbone is the frozen mix87 vision tower: every
requested block is mixed with a learned softmax, and only the adapter, the
two attack classifiers, and that mix are trained.

Their model scores one image. A clip is scored from its frames with a soft
max, so the frame the head ranks most like an attack dominates. Averaging
frames hides a fake segment that occupies only a small part of the clip.

Input features are tapped encoder blocks, shape (B, frames, tokens, streams, D).
The encoder is not part of this module and must stay frozen.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class RealAnchorLoss(nn.Module):
    """Pull real embeddings together and push real-vs-attack pairs apart.

    Attack-vs-attack pairs are ignored, matching the FS-Adapter real-anchor
    loss. ``features`` is (B, D) and already L2-normalized. Label 0 is real.
    """

    def __init__(self, temperature: float = 0.07):
        super().__init__()
        self.temperature = temperature

    def forward(self, features: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        labels = labels.long().view(-1)
        real = labels == 0
        if int(real.sum()) < 2:
            return features.new_zeros(())
        logits = features @ features.T / self.temperature
        logits = logits - logits.max(dim=1, keepdim=True).values.detach()
        eye = torch.eye(labels.shape[0], device=features.device, dtype=torch.bool)
        keep = ~eye
        exp = torch.exp(logits) * keep
        log_prob = logits - torch.log(exp.sum(dim=1, keepdim=True).clamp(min=1e-6))
        same = labels[:, None] == labels[None, :]
        positive = same & real[:, None] & real[None, :] & keep
        # Denominator still contains attacks, so a real anchor is pushed off them.
        count = positive.sum(dim=1).clamp(min=1)
        per = -(positive * log_prob).sum(dim=1) / count
        return per[real].mean()


class MultiLayerFSFMHead(nn.Module):
    def __init__(
        self,
        feature_dim: int = 1152,
        num_streams: int = 27,
        reduction: int = 4,
        live_weight: float = 1.0,
        temperature: float = 0.07,
    ):
        super().__init__()
        if feature_dim % reduction != 0:
            raise ValueError(f"feature_dim {feature_dim} is not divisible by reduction {reduction}")
        bottleneck = feature_dim // reduction
        self.feature_dim = feature_dim
        self.stream_weights = nn.Parameter(torch.zeros(num_streams))
        self.norm = nn.LayerNorm(feature_dim)
        self.down = nn.Linear(feature_dim, bottleneck, bias=False)
        self.up = nn.Linear(bottleneck, feature_dim, bias=False)
        self.projector = nn.Linear(bottleneck, bottleneck, bias=False)
        self.spoof = nn.Linear(feature_dim * 2, 2)
        self.deepfake = nn.Linear(feature_dim * 2, 2)
        self.contrast = RealAnchorLoss(temperature)
        self.register_buffer("ce_weight", torch.tensor([live_weight, 1.0], dtype=torch.float32))

    def frame_embed(self, feats: torch.Tensor) -> torch.Tensor:
        """feats (B, frames, tokens, streams, D) -> (B, frames, D)."""
        if feats.ndim != 5:
            raise ValueError(f"expected (B, frames, tokens, streams, D), got {tuple(feats.shape)}")
        pooled = feats.float().mean(dim=2)
        mix = torch.softmax(self.stream_weights, dim=0)
        mixed = (pooled * mix.view(1, 1, -1, 1)).sum(dim=2)
        return self.norm(mixed)

    def _adapted(self, frame: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        hidden = F.relu(self.down(frame))
        adapted = F.relu(self.up(hidden))
        proj = F.normalize(self.projector(hidden), dim=-1)
        return torch.cat([frame, adapted], dim=-1), proj

    def _pool_frames(self, logits: torch.Tensor) -> torch.Tensor:
        """logits (B, frames, 2) -> (B, 2), weighted toward the most attack-like frame."""
        score = logits[..., 1] - logits[..., 0]
        weight = torch.softmax(score, dim=1)
        return (logits * weight.unsqueeze(-1)).sum(dim=1)

    def clip_scores(self, feats: torch.Tensor) -> dict[str, torch.Tensor]:
        """Higher means more likely an attack. Joint is the max of the two."""
        frame = self.frame_embed(feats)
        cat, _proj = self._adapted(frame)
        spoof = self._pool_frames(self.spoof(cat))
        deep = self._pool_frames(self.deepfake(cat))
        spoof_s = spoof[:, 1] - spoof[:, 0]
        deep_s = deep[:, 1] - deep[:, 0]
        return {"spoof": spoof_s, "deepfake": deep_s, "joint": torch.maximum(spoof_s, deep_s)}

    def forward(
        self,
        feats: torch.Tensor,
        spoof_y: torch.Tensor,
        deepfake_y: torch.Tensor,
        spoof_mask: torch.Tensor,
        deepfake_mask: torch.Tensor,
        contrast_weight: float = 0.1,
    ) -> tuple[torch.Tensor, dict]:
        frame = self.frame_embed(feats)
        cat, proj = self._adapted(frame)
        spoof_logits = self._pool_frames(self.spoof(cat))
        deep_logits = self._pool_frames(self.deepfake(cat))
        loss_spoof = _masked_ce(spoof_logits, spoof_y, spoof_mask, self.ce_weight)
        loss_deep = _masked_ce(deep_logits, deepfake_y, deepfake_mask, self.ce_weight)
        clip_proj = F.normalize(proj.mean(dim=1), dim=-1)
        attack = ((spoof_y == 1) | (deepfake_y == 1)).long()
        loss_contrast = self.contrast(clip_proj, attack)
        loss = loss_spoof + loss_deep + contrast_weight * loss_contrast
        return loss, {
            "loss_spoof": float(loss_spoof.detach()),
            "loss_deepfake": float(loss_deep.detach()),
            "loss_contrast": float(loss_contrast.detach()),
        }


def _masked_ce(logits: torch.Tensor, target: torch.Tensor, mask: torch.Tensor, weight: torch.Tensor) -> torch.Tensor:
    per = F.cross_entropy(logits, target.long(), weight=weight, reduction="none")
    mask = mask.float()
    return (per * mask).sum() / mask.sum().clamp(min=1.0)
