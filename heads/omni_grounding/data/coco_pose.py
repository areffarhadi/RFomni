"""COCO person keypoints. Boxes + 17×(x,y,v) in stretch-resized [0,1] coords."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image
from pycocotools.coco import COCO
from torch.utils.data import Dataset

from omni_grounding.data.coco import DEFAULT_COCO_ROOT, IMAGE_SIZE, xywh_to_cxcywh_norm  # noqa: F401

COCO_KPT_FLIP = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]
NUM_KPTS = 17


class CocoPose(Dataset):
    def __init__(
        self,
        root: str | Path = DEFAULT_COCO_ROOT,
        split: str = "train",
        image_size: int = IMAGE_SIZE,
        max_images: int = 0,
        min_area: int = 16,
    ):
        self.root = Path(root)
        self.image_size = image_size
        self.min_area = min_area
        split_dir = "train2017" if split == "train" else "val2017"
        self.img_dir = self.root / split_dir
        inst_path = self.root / "annotations" / f"person_keypoints_{split}2017.json"
        if not inst_path.is_file():
            raise FileNotFoundError(inst_path)
        self.coco = COCO(str(inst_path))
        self.vocab = ["person"]
        self.cat_ids = [1]
        self.contig_to_cat_id = {0: 1}
        ids = sorted(self.coco.getImgIds(catIds=[1]))
        keep = []
        for iid in ids:
            anns = self.coco.imgToAnns.get(iid, [])
            if any((not a.get("iscrowd")) and a.get("category_id") == 1 for a in anns):
                keep.append(iid)
        ids = keep
        if max_images:
            ids = ids[:max_images]
        self.ids = ids

    def __len__(self) -> int:
        return len(self.ids)

    def __getitem__(self, idx: int):
        img_id = self.ids[idx]
        info = self.coco.imgs[img_id]
        img = Image.open(self.img_dir / info["file_name"]).convert("RGB")
        W, H = img.size
        boxes, labels, kpts = [], [], []
        for ann in self.coco.imgToAnns.get(img_id, []):
            if ann.get("iscrowd") or ann.get("category_id") != 1:
                continue
            x, y, w, h = ann["bbox"]
            if w * h < self.min_area:
                continue
            raw = ann.get("keypoints") or []
            if len(raw) < NUM_KPTS * 3:
                kp = [[0.0, 0.0, 0.0] for _ in range(NUM_KPTS)]
            else:
                kp = []
                for i in range(NUM_KPTS):
                    kx, ky, v = raw[3 * i : 3 * i + 3]
                    kp.append([float(kx) / W, float(ky) / H, float(v)])
            boxes.append(xywh_to_cxcywh_norm(x, y, w, h, W, H))
            labels.append(0)
            kpts.append(kp)
        img = img.resize((self.image_size, self.image_size), Image.BILINEAR)
        return img, boxes, labels, kpts, img_id, (W, H)


def pose_collate(batch):
    images, boxes, labels, kpts, ids, orig = zip(*batch)
    return list(images), list(boxes), list(labels), list(kpts), list(ids), list(orig)


def _hflip_kpts(kpts: list) -> list:
    out = []
    for kp in kpts:
        flipped = [[1.0 - x, y, v] for x, y, v in kp]
        out.append([flipped[i] for i in COCO_KPT_FLIP])
    return out


class PoseAugment(Dataset):
    def __init__(
        self,
        base: CocoPose,
        image_size: int = IMAGE_SIZE,
        hflip: bool = True,
        mosaic: float = 0.5,
        seed: int = 0,
    ):
        self.base = base
        self.image_size = image_size
        self.hflip = hflip
        self.mosaic = mosaic
        self.seed = seed

    def __len__(self):
        return len(self.base)

    def _maybe_flip(self, rng, img, boxes, labels, kpts):
        if self.hflip and rng.random() < 0.5:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
            boxes = [[1.0 - cx, cy, w, h] for cx, cy, w, h in boxes]
            kpts = _hflip_kpts(kpts)
        return img, boxes, labels, kpts

    def __getitem__(self, idx: int):
        rng = random.Random(self.seed * 1_000_003 + idx * 9176 + 17)
        if self.mosaic > 0 and rng.random() < self.mosaic:
            extra = [rng.randrange(len(self.base)) for _ in range(3)]
            samples = [self.base[idx]] + [self.base[j] for j in extra]
            cell = self.image_size // 2
            canvas = Image.new("RGB", (self.image_size, self.image_size), (114, 114, 114))
            boxes, labels, kpts = [], [], []
            for i, sample in enumerate(samples):
                img, bb, lab, kp, _, _ = sample
                row, col = divmod(i, 2)
                tile = img.resize((cell, cell), Image.BILINEAR)
                canvas.paste(tile, (col * cell, row * cell))
                for b, l, p in zip(bb, lab, kp):
                    cx, cy, w, h = b
                    boxes.append([(col + cx) / 2, (row + cy) / 2, max(1e-4, w / 2), max(1e-4, h / 2)])
                    labels.append(l)
                    kpts.append([[(col + x) / 2, (row + y) / 2, v] for x, y, v in p])
            img_id, orig = samples[0][4], samples[0][5]
            img, boxes, labels, kpts = self._maybe_flip(rng, canvas, boxes, labels, kpts)
            return img, boxes, labels, kpts, img_id, orig
        img, boxes, labels, kpts, img_id, orig = self.base[idx]
        img, boxes, labels, kpts = self._maybe_flip(rng, img, list(boxes), list(labels), list(kpts))
        return img, boxes, labels, kpts, img_id, orig
