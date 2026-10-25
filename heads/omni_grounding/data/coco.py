"""COCO detection dataset: stretch-resize to IMAGE_SIZE, labels in 0..79 coco-id order."""

from __future__ import annotations

from pathlib import Path
from typing import List

from PIL import Image
from pycocotools.coco import COCO
from torch.utils.data import Dataset

DEFAULT_COCO_ROOT = Path("/data/d01/speech/tts/data/Aref-data/coco2017")
IMAGE_SIZE = 512


def xywh_to_cxcywh_norm(x: float, y: float, w: float, h: float, W: int, H: int) -> List[float]:
    cx = (x + 0.5 * w) / W
    cy = (y + 0.5 * h) / H
    return [
        max(0.0, min(1.0, cx)),
        max(0.0, min(1.0, cy)),
        max(1e-4, min(1.0, w / W)),
        max(1e-4, min(1.0, h / H)),
    ]


class CocoDet(Dataset):
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
        inst_path = self.root / "annotations" / f"instances_{split}2017.json"
        if not inst_path.is_file():
            raise FileNotFoundError(inst_path)
        self.coco = COCO(str(inst_path))
        cats = sorted(self.coco.loadCats(self.coco.getCatIds()), key=lambda c: c["id"])
        self.cat_ids = [c["id"] for c in cats]
        self.vocab = [c["name"] for c in cats]
        self.cat_id_to_contig = {cid: i for i, cid in enumerate(self.cat_ids)}
        self.contig_to_cat_id = {i: cid for i, cid in enumerate(self.cat_ids)}
        ids = sorted(self.coco.imgs.keys())
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
        boxes, labels, coco_cats = [], [], []
        for ann in self.coco.imgToAnns.get(img_id, []):
            if ann.get("iscrowd"):
                continue
            x, y, w, h = ann["bbox"]
            if w * h < self.min_area:
                continue
            cid = ann["category_id"]
            if cid not in self.cat_id_to_contig:
                continue
            boxes.append(xywh_to_cxcywh_norm(x, y, w, h, W, H))
            labels.append(self.cat_id_to_contig[cid])
            coco_cats.append(cid)
        img = img.resize((self.image_size, self.image_size), Image.BILINEAR)
        return img, boxes, labels, img_id, (W, H), coco_cats


def coco_collate(batch):
    images, boxes, labels, ids, orig, coco_cats = zip(*batch)
    return list(images), list(boxes), list(labels), list(ids), list(orig), list(coco_cats)
