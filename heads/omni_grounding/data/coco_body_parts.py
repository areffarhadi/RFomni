"""Coarse body + body-part boxes from COCO-17 keypoints.

Not the 21 tiny keypoint squares from `dataset_localize.py`, and not YOLO-pose
OKS. Person box plus large parts: head/face, hands, arms, legs, feet.
"""

from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from PIL import Image
from pycocotools.coco import COCO
from torch.utils.data import Dataset

from omni_grounding.data.coco import DEFAULT_COCO_ROOT, IMAGE_SIZE, xywh_to_cxcywh_norm
from omni_grounding.data.augment import mosaic_samples

KP = {
    "nose": 0, "left_eye": 1, "right_eye": 2, "left_ear": 3, "right_ear": 4,
    "left_shoulder": 5, "right_shoulder": 6, "left_elbow": 7, "right_elbow": 8,
    "left_wrist": 9, "right_wrist": 10, "left_hip": 11, "right_hip": 12,
    "left_knee": 13, "right_knee": 14, "left_ankle": 15, "right_ankle": 16,
}

BODY_PART_VOCAB = [
    "person",
    "head",
    "face",
    "left hand",
    "right hand",
    "left arm",
    "right arm",
    "left leg",
    "right leg",
    "left foot",
    "right foot",
]

FLIP_NAME = {
    "person": "person",
    "head": "head",
    "face": "face",
    "left hand": "right hand",
    "right hand": "left hand",
    "left arm": "right arm",
    "right arm": "left arm",
    "left leg": "right leg",
    "right leg": "left leg",
    "left foot": "right foot",
    "right foot": "left foot",
}


def _vis(kpts, i: int) -> bool:
    return kpts[i * 3 + 2] > 0


def _xy(kpts, i: int) -> tuple[float, float]:
    return float(kpts[i * 3]), float(kpts[i * 3 + 1])


def _clip_box(x1, y1, x2, y2, W, H, min_side: float) -> list[float] | None:
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(W), x2), min(float(H), y2)
    if x2 - x1 < min_side or y2 - y1 < min_side:
        return None
    return [x1, y1, x2, y2]


def _hull(pts: Sequence[tuple[float, float]], pad: float, W: int, H: int, min_side: float):
    if len(pts) < 1:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return _clip_box(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad, W, H, min_side)


def _around(x, y, side, W, H, min_side: float):
    h = side / 2.0
    return _clip_box(x - h, y - h, x + h, y + h, W, H, min_side)


def derive_coarse_parts(kpts, person_xywh, W: int, H: int) -> list[tuple[list[float], str]]:
    """xyxy pixel boxes + query name. Skip tiny / missing parts."""
    px, py, pw, ph = person_xywh
    scale = max(float(pw), float(ph), 1.0)
    min_side = max(16.0, 0.04 * scale)
    pad_face = 0.08 * scale
    out: list[tuple[list[float], str]] = []

    person = _clip_box(px, py, px + pw, py + ph, W, H, min_side)
    if person is not None:
        out.append((person, "person"))

    face_ids = [KP["nose"], KP["left_eye"], KP["right_eye"], KP["left_ear"], KP["right_ear"]]
    face_pts = [_xy(kpts, i) for i in face_ids if _vis(kpts, i)]
    face = _hull(face_pts, pad_face, W, H, min_side)
    if face is not None:
        out.append((face, "face"))
        hx1, hy1, hx2, hy2 = face
        grow = 0.35 * (hy2 - hy1)
        head = _clip_box(hx1 - 0.1 * (hx2 - hx1), hy1 - grow, hx2 + 0.1 * (hx2 - hx1), hy2 + 0.15 * (hy2 - hy1), W, H, min_side)
        if head is not None:
            out.append((head, "head"))

    for side in ("left", "right"):
        sh, el, wr = KP[f"{side}_shoulder"], KP[f"{side}_elbow"], KP[f"{side}_wrist"]
        hip, kn, an = KP[f"{side}_hip"], KP[f"{side}_knee"], KP[f"{side}_ankle"]

        if _vis(kpts, wr):
            wx, wy = _xy(kpts, wr)
            pts = [(wx, wy)]
            if _vis(kpts, el):
                ex, ey = _xy(kpts, el)
                pts.append((wx + 0.25 * (wx - ex), wy + 0.25 * (wy - ey)))
            hand = _hull(pts, max(0.10 * scale, 18.0), W, H, min_side)
            if hand is not None:
                out.append((hand, f"{side} hand"))

        arm_pts = [_xy(kpts, i) for i in (sh, el, wr) if _vis(kpts, i)]
        if len(arm_pts) >= 2:
            arm = _hull(arm_pts, 0.07 * scale, W, H, min_side)
            if arm is not None:
                out.append((arm, f"{side} arm"))

        leg_pts = [_xy(kpts, i) for i in (hip, kn, an) if _vis(kpts, i)]
        if len(leg_pts) >= 2:
            leg = _hull(leg_pts, 0.07 * scale, W, H, min_side)
            if leg is not None:
                out.append((leg, f"{side} leg"))

        if _vis(kpts, an):
            ax, ay = _xy(kpts, an)
            pts = [(ax, ay)]
            if _vis(kpts, kn):
                kx, ky = _xy(kpts, kn)
                pts.append((ax + 0.2 * (ax - kx), ay + 0.2 * (ay - ky)))
            foot = _hull(pts, max(0.08 * scale, 16.0), W, H, min_side)
            if foot is not None:
                out.append((foot, f"{side} foot"))

    return out


def xyxy_to_cxcywh_norm(box, W, H):
    x1, y1, x2, y2 = box
    return [
        max(0.0, min(1.0, ((x1 + x2) / 2.0) / W)),
        max(0.0, min(1.0, ((y1 + y2) / 2.0) / H)),
        max(1e-4, min(1.0, (x2 - x1) / W)),
        max(1e-4, min(1.0, (y2 - y1) / H)),
    ]


class CocoBodyParts(Dataset):
    """CocoDet-compatible item: image, cxcywh-norm boxes, labels, id, orig, cat_ids."""

    def __init__(
        self,
        root: str | Path = DEFAULT_COCO_ROOT,
        split: str = "train",
        image_size: int = IMAGE_SIZE,
        max_images: int = 0,
        min_area: int = 16,
        build_coco_gt: bool = False,
        require_person: bool = True,
    ):
        self.root = Path(root)
        self.image_size = image_size
        self.min_area = min_area
        split_dir = "train2017" if split == "train" else "val2017"
        self.img_dir = self.root / split_dir
        kp_path = self.root / "annotations" / f"person_keypoints_{split}2017.json"
        if not kp_path.is_file():
            raise FileNotFoundError(kp_path)
        self.kp = COCO(str(kp_path))
        self.vocab = list(BODY_PART_VOCAB)
        self.query_to_id = {q: i for i, q in enumerate(self.vocab)}
        self.cat_ids = [i + 1 for i in range(len(self.vocab))]
        self.contig_to_cat_id = {i: i + 1 for i in range(len(self.vocab))}
        self.cat_id_to_contig = {i + 1: i for i in range(len(self.vocab))}

        ids = sorted(self.kp.getImgIds(catIds=[1]) if require_person else self.kp.imgs.keys())
        if require_person:
            keep = []
            for iid in ids:
                anns = self.kp.imgToAnns.get(iid, [])
                if any((not a.get("iscrowd")) and a.get("category_id") == 1 for a in anns):
                    keep.append(iid)
            ids = keep
        if max_images:
            ids = ids[:max_images]
        self.ids = ids

        self.coco = None
        if build_coco_gt:
            self.coco = self._make_coco_gt()

    def __len__(self):
        return len(self.ids)

    def _targets(self, img_id: int, W: int, H: int):
        boxes, labels, cats = [], [], []
        for ann in self.kp.imgToAnns.get(img_id, []):
            if ann.get("iscrowd") or ann.get("category_id") != 1:
                continue
            x, y, w, h = ann["bbox"]
            if w * h < self.min_area:
                continue
            raw = ann.get("keypoints") or []
            if len(raw) < 51:
                raw = [0.0] * 51
            for xyxy, name in derive_coarse_parts(raw, (x, y, w, h), W, H):
                if name not in self.query_to_id:
                    continue
                boxes.append(xyxy_to_cxcywh_norm(xyxy, W, H))
                labels.append(self.query_to_id[name])
                cats.append(self.contig_to_cat_id[self.query_to_id[name]])
        return boxes, labels, cats

    def _make_coco_gt(self) -> COCO:
        images, anns = [], []
        aid = 1
        for img_id in self.ids:
            info = self.kp.imgs[img_id]
            W, H = int(info["width"]), int(info["height"])
            images.append({"id": int(img_id), "file_name": info["file_name"], "width": W, "height": H})
            boxes, labels, cats = self._targets(img_id, W, H)
            for bb, lab, cat in zip(boxes, labels, cats):
                cx, cy, bw, bh = bb
                anns.append(
                    {
                        "id": aid,
                        "image_id": int(img_id),
                        "category_id": int(cat),
                        "bbox": [(cx - 0.5 * bw) * W, (cy - 0.5 * bh) * H, bw * W, bh * H],
                        "area": float(bw * bh * W * H),
                        "iscrowd": 0,
                    }
                )
                aid += 1
        ds = {
            "images": images,
            "annotations": anns,
            "categories": [{"id": i + 1, "name": n} for i, n in enumerate(self.vocab)],
        }
        coco = COCO()
        coco.dataset = ds
        coco.createIndex()
        return coco

    def __getitem__(self, idx: int):
        img_id = self.ids[idx]
        info = self.kp.imgs[img_id]
        img = Image.open(self.img_dir / info["file_name"]).convert("RGB")
        W, H = img.size
        boxes, labels, cats = self._targets(img_id, W, H)
        img = img.resize((self.image_size, self.image_size), Image.BILINEAR)
        return img, boxes, labels, img_id, (W, H), cats


class BodyPartAugment(Dataset):
    """Mosaic + hflip with left/right part name swap."""

    def __init__(
        self,
        base: CocoBodyParts,
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
        vocab = base.vocab
        self.flip_idx = [vocab.index(FLIP_NAME[n]) for n in vocab]

    def __len__(self):
        return len(self.base)

    def _flip(self, img, boxes, labels, cats):
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
        boxes = [[1.0 - cx, cy, w, h] for cx, cy, w, h in boxes]
        labels = [self.flip_idx[int(i)] for i in labels]
        cats = [self.flip_idx[int(c) - 1] + 1 for c in cats]
        return img, boxes, labels, cats

    def __getitem__(self, idx: int):
        rng = random.Random(self.seed * 1_000_003 + idx * 9176 + 23)
        if self.mosaic > 0 and rng.random() < self.mosaic:
            extra = [rng.randrange(len(self.base)) for _ in range(3)]
            samples = [self.base[idx]] + [self.base[j] for j in extra]
            img, boxes, labels, img_id, orig, cats = mosaic_samples(samples, 2, self.image_size)
        else:
            img, boxes, labels, img_id, orig, cats = self.base[idx]
            boxes, labels, cats = list(boxes), list(labels), list(cats)
        if self.hflip and rng.random() < 0.5:
            img, boxes, labels, cats = self._flip(img, boxes, labels, cats)
        return img, boxes, labels, img_id, orig, cats


def part_count_report(ds: CocoBodyParts, n: int = 400) -> dict:
    counts = defaultdict(int)
    n = min(n, len(ds))
    for i in range(n):
        _, _, labels, _, _, _ = ds[i]
        for lab in labels:
            counts[ds.vocab[int(lab)]] += 1
    return dict(counts)


class CocoPersonOnly(Dataset):
    """COCO instance person boxes, label 0 in BODY_PART_VOCAB. No head/parts."""

    def __init__(
        self,
        root: str | Path = DEFAULT_COCO_ROOT,
        split: str = "train",
        image_size: int = IMAGE_SIZE,
        exclude_ids: Sequence[int] | None = None,
        min_area: int = 16,
    ):
        self.root = Path(root)
        self.image_size = image_size
        self.min_area = min_area
        split_dir = "train2017" if split == "train" else "val2017"
        self.img_dir = self.root / split_dir
        inst_path = self.root / "annotations" / f"instances_{split}2017.json"
        coco = COCO(str(inst_path))
        skip = set(int(x) for x in (exclude_ids or []))
        ids = []
        for iid in sorted(coco.getImgIds(catIds=[1])):
            if int(iid) in skip:
                continue
            anns = coco.imgToAnns.get(iid, [])
            if any(
                (not a.get("iscrowd")) and a.get("category_id") == 1 and a["bbox"][2] * a["bbox"][3] >= min_area
                for a in anns
            ):
                ids.append(int(iid))
        self.ids = ids
        self.coco = coco
        self.vocab = list(BODY_PART_VOCAB)

    def __len__(self) -> int:
        return len(self.ids)

    def __getitem__(self, idx: int):
        img_id = self.ids[idx]
        info = self.coco.imgs[img_id]
        img = Image.open(self.img_dir / info["file_name"]).convert("RGB")
        W, H = img.size
        boxes, labels, cats = [], [], []
        for ann in self.coco.imgToAnns.get(img_id, []):
            if ann.get("iscrowd") or ann.get("category_id") != 1:
                continue
            x, y, w, h = ann["bbox"]
            if w * h < self.min_area:
                continue
            boxes.append(xywh_to_cxcywh_norm(x, y, w, h, W, H))
            labels.append(0)
            cats.append(1)
        img = img.resize((self.image_size, self.image_size), Image.BILINEAR)
        return img, boxes, labels, img_id, (W, H), cats
