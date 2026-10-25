"""Top-down COCO person crops for ViTPose-style heatmap training."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image
from pycocotools.coco import COCO
from torch.utils.data import Dataset

from omni_grounding.data.coco import DEFAULT_COCO_ROOT
from omni_grounding.data.coco_pose import COCO_KPT_FLIP, NUM_KPTS

CROP = 256
PAD = 1.25


def _square_box(x, y, w, h, W, H, pad: float = PAD):
    cx, cy = x + w / 2.0, y + h / 2.0
    side = max(w, h) * pad
    x1 = cx - side / 2.0
    y1 = cy - side / 2.0
    return x1, y1, side


class CocoPoseTopDown(Dataset):
    """One person instance per row. Crop is square 256 for the Omni encoder."""

    def __init__(
        self,
        root: str | Path = DEFAULT_COCO_ROOT,
        split: str = "train",
        crop: int = CROP,
        max_images: int = 0,
        min_kpts: int = 1,
        seed: int = 0,
    ):
        self.root = Path(root)
        self.crop = crop
        self.split = split
        split_dir = "train2017" if split == "train" else "val2017"
        self.img_dir = self.root / split_dir
        inst_path = self.root / "annotations" / f"person_keypoints_{split}2017.json"
        self.coco = COCO(str(inst_path))
        img_ids = sorted(self.coco.getImgIds(catIds=[1]))
        if max_images:
            img_ids = img_ids[:max_images]
        self.items: list[tuple[int, int]] = []  # (img_id, ann_id)
        for iid in img_ids:
            for ann in self.coco.imgToAnns.get(iid, []):
                if ann.get("iscrowd") or ann.get("category_id") != 1:
                    continue
                kp = ann.get("keypoints") or []
                nvis = sum(1 for i in range(0, min(len(kp), NUM_KPTS * 3), 3) if kp[i + 2] > 0)
                if nvis < min_kpts:
                    continue
                x, y, w, h = ann["bbox"]
                if w * h < 16:
                    continue
                self.items.append((iid, int(ann["id"])))
        self._rng = random.Random(seed)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx: int):
        img_id, ann_id = self.items[idx]
        info = self.coco.imgs[img_id]
        img = Image.open(self.img_dir / info["file_name"]).convert("RGB")
        W, H = img.size
        ann = self.coco.anns[ann_id]
        x, y, w, h = [float(v) for v in ann["bbox"]]
        x1, y1, side = _square_box(x, y, w, h, W, H)
        raw = ann.get("keypoints") or [0] * (NUM_KPTS * 3)
        kxy, vis = [], []
        for i in range(NUM_KPTS):
            kx, ky, v = raw[3 * i : 3 * i + 3]
            kxy.append((float(kx), float(ky)))
            vis.append(float(v))

        do_flip = self.split == "train" and self._rng.random() < 0.5
        if self.split == "train":
            scale = self._rng.uniform(0.75, 1.25)
            x1 = x1 - (scale - 1.0) * side / 2.0
            y1 = y1 - (scale - 1.0) * side / 2.0
            side = side * scale

        crop = img.crop((int(x1), int(y1), int(x1 + side), int(y1 + side)))
        crop = crop.resize((self.crop, self.crop), Image.BILINEAR)
        s = self.crop / max(side, 1e-6)
        kpts = []
        for (kx, ky), v in zip(kxy, vis):
            cx = (kx - x1) * s
            cy = (ky - y1) * s
            kpts.append([cx, cy, v])
        if do_flip:
            crop = crop.transpose(Image.FLIP_LEFT_RIGHT)
            flipped = [None] * NUM_KPTS
            for i, j in enumerate(COCO_KPT_FLIP):
                px, py, v = kpts[i]
                flipped[j] = [self.crop - 1 - px, py, v]
            kpts = flipped
        return {
            "image": crop,
            "kpts": kpts,
            "img_id": img_id,
            "ann_id": ann_id,
            "orig": (W, H),
            "crop_xy": (x1, y1, side),
            "bbox": (x, y, w, h),
        }


def topdown_collate(batch):
    images = [b["image"] for b in batch]
    kpts = [b["kpts"] for b in batch]
    meta = [{k: b[k] for k in ("img_id", "ann_id", "orig", "crop_xy", "bbox")} for b in batch]
    return images, kpts, meta
