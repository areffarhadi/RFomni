"""MHFA embedding + binary spoof classifier (bonafide vs spoof)."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from model_speaker import MHFASpeakerHead


class AntispoofModel(nn.Module):
    """MHFA pooling head + 2-way linear classifier."""

    def __init__(
        self,
        feature_dim: int,
        num_streams: int,
        num_heads: int = 8,
        compression_dim: int = 128,
        embedding_dim: int = 256,
        adapter_bottleneck: int = 128,
        dropout: float = 0.1,
        class_weights: tuple[float, float] | None = None,
    ):
        super().__init__()
        self.head = MHFASpeakerHead(
            feature_dim=feature_dim,
            num_streams=num_streams,
            num_heads=num_heads,
            compression_dim=compression_dim,
            embedding_dim=embedding_dim,
            adapter_bottleneck=adapter_bottleneck,
            dropout=dropout,
        )
        self.classifier = nn.Linear(embedding_dim, 2)
        if class_weights is not None:
            self.register_buffer(
                "class_weights",
                torch.tensor(class_weights, dtype=torch.float32),
                persistent=False,
            )
        else:
            self.class_weights = None

    @property
    def embedding_dim(self) -> int:
        return self.head.embedding_dim

    def embed(self, feats: torch.Tensor, lengths=None) -> torch.Tensor:
        return self.head(feats, lengths=lengths)

    def forward(self, feats: torch.Tensor, labels: torch.Tensor, lengths=None):
        emb = self.embed(feats, lengths=lengths)
        logits = self.classifier(emb)
        loss = F.cross_entropy(logits, labels, weight=self.class_weights)
        return loss, logits

    def spoof_score(self, feats: torch.Tensor, lengths=None) -> torch.Tensor:
        """Higher = more likely spoof."""
        logits = self.classifier(self.embed(feats, lengths=lengths))
        return logits[:, 1] - logits[:, 0]


# Fine-grained classes of the unified spoof head (v2). Index 0 must stay bonafide.
UNIFIED_CLASSES = (
    "bonafide",
    "physical",   # print / replay / mask presentation attacks (CelebA-Spoof, CASIA-FASD)
    "faceswap",   # identity swaps (FF++ Deepfakes/FaceSwap/FaceShifter/DFD, Celeb-DF)
    "reenact",    # expression re-enactment (FF++ Face2Face/NeuralTextures)
    "synthetic",  # fully generated faces (140k StyleGAN)
)


class UnifiedSpoofModel(nn.Module):
    """One MHFA head for face anti-spoofing *and* deepfake detection.

    Trained on fine-grained attack classes so physical attacks, swaps and
    re-enactments don't have to share one "spoof" direction; the deployed
    output is a single real-vs-fake log-odds,
        score = logsumexp(logits[attack classes]) - logits[bonafide],
    which is also trained directly with a binary loss.
    """

    def __init__(
        self,
        feature_dim: int,
        num_streams: int,
        num_classes: int = len(UNIFIED_CLASSES),
        num_heads: int = 8,
        compression_dim: int = 128,
        embedding_dim: int = 256,
        adapter_bottleneck: int = 128,
        dropout: float = 0.1,
        binary_weight: float = 1.0,
        label_smoothing: float = 0.05,
    ):
        super().__init__()
        self.head = MHFASpeakerHead(
            feature_dim=feature_dim,
            num_streams=num_streams,
            num_heads=num_heads,
            compression_dim=compression_dim,
            embedding_dim=embedding_dim,
            adapter_bottleneck=adapter_bottleneck,
            dropout=dropout,
        )
        self.classifier = nn.Linear(embedding_dim, num_classes)
        self.binary_weight = binary_weight
        self.label_smoothing = label_smoothing

    @property
    def embedding_dim(self) -> int:
        return self.head.embedding_dim

    def embed(self, feats: torch.Tensor, lengths=None) -> torch.Tensor:
        return self.head(feats, lengths=lengths)

    @staticmethod
    def binary_logit(logits: torch.Tensor) -> torch.Tensor:
        return torch.logsumexp(logits[:, 1:], dim=1) - logits[:, 0]

    def forward(self, feats: torch.Tensor, labels: torch.Tensor, lengths=None, sample_weight=None):
        logits = self.classifier(self.embed(feats, lengths=lengths)).float()
        ce = F.cross_entropy(logits, labels, label_smoothing=self.label_smoothing, reduction="none")
        bce = F.binary_cross_entropy_with_logits(
            self.binary_logit(logits), (labels > 0).float(), reduction="none"
        )
        loss = ce + self.binary_weight * bce
        if sample_weight is not None:
            loss = (loss * sample_weight).sum() / sample_weight.sum().clamp_min(1e-6)
        else:
            loss = loss.mean()
        return loss, logits

    def spoof_score(self, feats: torch.Tensor, lengths=None) -> torch.Tensor:
        """Higher = more likely spoof / fake (log-odds)."""
        return self.binary_logit(self.classifier(self.embed(feats, lengths=lengths)).float())
