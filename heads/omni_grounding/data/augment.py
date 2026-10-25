"""OWL-style detection augmentations: horizontal flip + image mosaics.

Mosaic sizes (1, 2, 3) with probabilities (0.4, 0.3, 0.3) match scenic
`clip_b32.py` (`mosaic_sizes` / `mosaic_probs`). Encoder stays untouched.
"""

from __future__ import annotations

import random
from typing import Sequence

from PIL import Image
from torch.utils.data import Dataset


def hflip_sample(img: Image.Image, boxes: list, labels: list, coco_cats: list):
    img = img.transpose(Image.FLIP_LEFT_RIGHT)
    boxes = [[1.0 - cx, cy, w, h] for cx, cy, w, h in boxes]
    return img, boxes, labels, coco_cats


def mosaic_samples(samples: Sequence, k: int, image_size: int):
    """Stitch k×k images. Boxes are cxcywh in [0, 1] of each tile."""
    cell = image_size // k
    canvas = Image.new("RGB", (image_size, image_size), (114, 114, 114))
    out_boxes, out_labels, out_cats = [], [], []
    for i, sample in enumerate(samples):
        img, boxes, labels, _, _, cats = sample
        row, col = divmod(i, k)
        tile = img.resize((cell, cell), Image.BILINEAR)
        canvas.paste(tile, (col * cell, row * cell))
        for bb, lab, cat in zip(boxes, labels, cats):
            cx, cy, w, h = bb
            out_boxes.append(
                [
                    (col + cx) / k,
                    (row + cy) / k,
                    max(1e-4, w / k),
                    max(1e-4, h / k),
                ]
            )
            out_labels.append(lab)
            out_cats.append(cat)
    img_id, orig = samples[0][3], samples[0][4]
    return canvas, out_boxes, out_labels, img_id, orig, out_cats


class OwlDetAugment(Dataset):
    """Wraps CocoDet with OWL mosaic + flip. Length unchanged."""

    def __init__(
        self,
        base: Dataset,
        image_size: int = 512,
        hflip: bool = True,
        mosaic_sizes: tuple[int, ...] = (1, 2, 3),
        mosaic_probs: tuple[float, ...] = (0.4, 0.3, 0.3),
        seed: int = 0,
    ):
        self.base = base
        self.image_size = image_size
        self.hflip = hflip
        self.mosaic_sizes = mosaic_sizes
        self.mosaic_probs = mosaic_probs
        self.seed = seed

    def __len__(self):
        return len(self.base)

    def __getitem__(self, idx: int):
        rng = random.Random(self.seed * 1_000_003 + idx * 9176 + 13)
        k = rng.choices(self.mosaic_sizes, weights=self.mosaic_probs, k=1)[0]
        if k <= 1:
            img, boxes, labels, img_id, orig, cats = self.base[idx]
            sample = (img, list(boxes), list(labels), img_id, orig, list(cats))
        else:
            n = k * k
            extra = [rng.randrange(len(self.base)) for _ in range(n - 1)]
            samples = [self.base[idx]] + [self.base[j] for j in extra]
            sample = mosaic_samples(samples, k, self.image_size)
        img, boxes, labels, img_id, orig, cats = sample
        if self.hflip and rng.random() < 0.5:
            img, boxes, labels, cats = hflip_sample(img, boxes, labels, cats)
        return img, boxes, labels, img_id, orig, cats
