"""Loc v2 data: COCO-WholeBody + WIDER FACE + CrowdHuman on one 11-class vocab.

Each source only labels some classes. A per-image `cls_mask` says which classes
are exhaustively annotated, and ignore regions (box + class mask) mark objects
that exist but carry no usable box (crowds, invalid faces, small people without
whole-body labels). The loss gives no negative signal there.

Images are letterboxed onto an S x S canvas (aspect kept, no stretch). Training
adds YOLO-style mosaic, random scale/translate, HSV jitter and h-flip with
left/right name swap. Pixel patchify for the Omni tower runs in the workers.
"""

from __future__ import annotations

import json
import os
import pickle
import random
from pathlib import Path

import cv2
import numpy as np
import torch
from pycocotools.coco import COCO
from torch.utils.data import Dataset

from omni_grounding.data.coco_body_parts import BODY_PART_VOCAB, FLIP_NAME, KP, derive_coarse_parts

DATA_ROOT = Path("/data/d01/speech/tts/data/Aref-data")
COCO_ROOT = DATA_ROOT / "coco2017"
WB_ROOT = DATA_ROOT / "coco_wholebody"
WIDER_ROOT = DATA_ROOT / "widerface"
CH_ROOT = DATA_ROOT / "crowdhuman"

VOCAB = list(BODY_PART_VOCAB)
NC = len(VOCAB)
CID = {n: i for i, n in enumerate(VOCAB)}
FLIP_IDX = [VOCAB.index(FLIP_NAME[n]) for n in VOCAB]
PAD_VALUE = 114


def _mask(names) -> np.ndarray:
    m = np.zeros(NC, dtype=bool)
    for n in names:
        m[CID[n]] = True
    return m


ALL = _mask(VOCAB)
# COCO head boxes are keypoint-derived, not real heads; COCO-WholeBody face boxes are the
# 68-landmark extent (brows to chin, w/h ~1.0) while WIDER boxes include the forehead
# (w/h ~0.78). Faces are learned from WIDER only; COCO faces become ignore regions.
COCO_MASK = _mask([n for n in VOCAB if n not in ("head", "face")])
WIDER_MASK = _mask(["face"])
CH_MASK = _mask(["person", "head"])
PARTS_MASK = _mask([n for n in VOCAB if n != "person"])


def _empty_sample(img, img_id, src, cls_mask):
    return {
        "img": img, "boxes": np.zeros((0, 4), np.float32), "labels": np.zeros((0,), np.int64),
        "ign_boxes": np.zeros((0, 4), np.float32), "ign_masks": np.zeros((0, NC), bool),
        "cls_mask": cls_mask.copy(), "img_id": img_id, "src": src,
    }


def _finish(s, boxes, labels, ign, ign_masks):
    s["boxes"] = np.asarray(boxes, np.float32).reshape(-1, 4)
    s["labels"] = np.asarray(labels, np.int64).reshape(-1)
    s["ign_boxes"] = np.asarray(ign, np.float32).reshape(-1, 4)
    s["ign_masks"] = np.asarray(ign_masks, bool).reshape(-1, NC)
    return s


def _read_rgb(path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _xywh_to_xyxy(b):
    x, y, w, h = [float(v) for v in b]
    return [x, y, x + w, y + h]


def _clip_xyxy(b, W, H):
    x1, y1, x2, y2 = b
    return [max(0.0, min(W, x1)), max(0.0, min(H, y1)), max(0.0, min(W, x2)), max(0.0, min(H, y2))]


def _valid(b, min_side=1.0):
    return (b[2] - b[0]) >= min_side and (b[3] - b[1]) >= min_side


# ---------------------------------------------------------------- sources


class CocoWholeBodyParts(Dataset):
    """Real face / hand boxes (COCO-WholeBody v1.0), foot boxes from foot kpts,
    person boxes from COCO, arms / legs derived from body keypoints."""

    def __init__(self, split: str = "train", max_images: int = 0, coco_root=COCO_ROOT, wb_root=WB_ROOT):
        self.split = split
        self.img_dir = Path(coco_root) / ("train2017" if split == "train" else "val2017")
        with open(Path(wb_root) / f"compact_{split}.pkl", "rb") as f:
            d = pickle.load(f)
        self.images = d["images"]
        self.anns = d["anns"]
        ids = sorted(i for i, a in self.anns.items() if any((not x["iscrowd"]) for x in a))
        if max_images:
            ids = ids[:max_images]
        self.ids = ids

    def __len__(self):
        return len(self.ids)

    def targets(self, img_id: int):
        fname, W, H = self.images[img_id]
        boxes, labels, ign, ign_m = [], [], [], []
        for a in self.anns.get(img_id, []):
            pb = _clip_xyxy(_xywh_to_xyxy(a["bbox"]), W, H)
            if a["iscrowd"]:
                ign.append(pb)
                ign_m.append(ALL)
                continue
            if (pb[2] - pb[0]) * (pb[3] - pb[1]) < 16:
                continue
            boxes.append(pb)
            labels.append(CID["person"])
            kp = a["kpts"]
            if a["num_kpts"] == 0:
                # Small people: no part labels at all, so parts inside are unknown.
                ign.append(pb)
                ign_m.append(PARTS_MASK)
                continue
            derived = {name: box for box, name in derive_coarse_parts(kp, a["bbox"], W, H)}
            # face: ignore region only (see COCO_MASK)
            if a["face_valid"]:
                fb = _clip_xyxy(_xywh_to_xyxy(a["face_box"]), W, H)
                if _valid(fb, 1):
                    ign.append(fb)
                    ign_m.append(_mask(["face"]))
            elif "face" in derived:
                ign.append(derived["face"])
                ign_m.append(_mask(["face"]))
            # hands
            for side, key in (("left", "lefthand"), ("right", "righthand")):
                name = f"{side} hand"
                if a[f"{key}_valid"]:
                    hb = _clip_xyxy(_xywh_to_xyxy(a[f"{key}_box"]), W, H)
                    if _valid(hb, 2):
                        boxes.append(hb)
                        labels.append(CID[name])
                        continue
                if name in derived:
                    ign.append(derived[name])
                    ign_m.append(_mask([name]))
            # arms / legs (derived, as in v1)
            for name in ("left arm", "right arm", "left leg", "right leg"):
                if name in derived:
                    boxes.append(derived[name])
                    labels.append(CID[name])
            # feet: ankle + big toe / small toe / heel
            foot = a["foot"]
            for side, off in (("left", 0), ("right", 3)):
                name = f"{side} foot"
                pts = [(foot[3 * j], foot[3 * j + 1]) for j in range(off, off + 3) if foot[3 * j + 2] > 0]
                ank = KP[f"{side}_ankle"]
                if a["foot_valid"] and len(pts) >= 2:
                    if kp[3 * ank + 2] > 0:
                        pts.append((kp[3 * ank], kp[3 * ank + 1]))
                    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                    side_len = max(max(xs) - min(xs), max(ys) - min(ys), 4.0)
                    pad = max(0.2 * side_len, 3.0)
                    fb = _clip_xyxy([min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad], W, H)
                    if _valid(fb, 2):
                        boxes.append(fb)
                        labels.append(CID[name])
                        continue
                if name in derived:
                    ign.append(derived[name])
                    ign_m.append(_mask([name]))
        return boxes, labels, ign, ign_m

    def __getitem__(self, idx: int):
        img_id = self.ids[idx]
        fname, W, H = self.images[img_id]
        s = _empty_sample(_read_rgb(self.img_dir / fname), img_id, "coco_wb", COCO_MASK)
        return _finish(s, *self.targets(img_id))


class WiderFaceDet(Dataset):
    """WIDER FACE: face only. `invalid` faces become ignore regions."""

    def __init__(self, split: str = "train", root=WIDER_ROOT, max_images: int = 0):
        self.root = Path(root)
        self.img_dir = self.root / f"WIDER_{split}" / "images"
        with open(self.root / f"compact_{split}.pkl", "rb") as f:
            self.items = pickle.load(f)  # list of (rel_path, [[x,y,w,h,invalid], ...]), split-file order
        if max_images:
            self.items = self.items[:max_images]

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx: int):
        rel, faces = self.items[idx]
        img = _read_rgb(self.img_dir / rel)
        H, W = img.shape[:2]
        s = _empty_sample(img, idx, "wider", WIDER_MASK)
        boxes, labels, ign, ign_m = [], [], [], []
        for x, y, w, h, invalid in faces:
            b = _clip_xyxy([x, y, x + w, y + h], W, H)
            if not _valid(b, 1):
                continue
            if invalid:
                ign.append(b)
                ign_m.append(WIDER_MASK)
            else:
                boxes.append(b)
                labels.append(CID["face"])
        return _finish(s, boxes, labels, ign, ign_m)


class CrowdHumanDet(Dataset):
    """CrowdHuman: person = visible box (matches COCO person), head = hbox."""

    def __init__(self, split: str = "train", root=CH_ROOT, max_images: int = 0):
        self.root = Path(root)
        self.img_dir = self.root / "Images"
        with open(self.root / f"compact_{split}.pkl", "rb") as f:
            self.items = pickle.load(f)  # list of (ID, W, H, gtboxes)
        self.items = sorted(self.items, key=lambda t: t[0])
        if max_images:
            self.items = self.items[:max_images]

    def __len__(self):
        return len(self.items)

    def targets(self, idx: int):
        _, W, H, gts = self.items[idx]
        boxes, labels, ign, ign_m = [], [], [], []
        for g in gts:
            if g["tag"] != "person":
                ign.append(_clip_xyxy(_xywh_to_xyxy(g["fbox"]), W, H))
                ign_m.append(CH_MASK)
                continue
            vb = _clip_xyxy(_xywh_to_xyxy(g["vbox"]), W, H)
            if g.get("ignore", 0):
                ign.append(vb)
                ign_m.append(_mask(["person"]))
            elif _valid(vb, 2):
                boxes.append(vb)
                labels.append(CID["person"])
            if "hbox" in g:
                hb = _clip_xyxy(_xywh_to_xyxy(g["hbox"]), W, H)
                if not _valid(hb, 1):
                    continue
                if g.get("head_ignore", 0):
                    ign.append(hb)
                    ign_m.append(_mask(["head"]))
                else:
                    boxes.append(hb)
                    labels.append(CID["head"])
        return boxes, labels, ign, ign_m

    def __getitem__(self, idx: int):
        iid = self.items[idx][0]
        s = _empty_sample(_read_rgb(self.img_dir / f"{iid}.jpg"), idx, "crowdhuman", CH_MASK)
        return _finish(s, *self.targets(idx))


# ---------------------------------------------------------------- geometry


def letterbox(img: np.ndarray, size: int) -> tuple[np.ndarray, float]:
    """Resize longest side to `size`, paste top-left on a size x size gray canvas."""
    H, W = img.shape[:2]
    r = size / max(H, W)
    nh, nw = max(1, round(H * r)), max(1, round(W * r))
    out = np.full((size, size, 3), PAD_VALUE, np.uint8)
    out[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if r < 1 else cv2.INTER_LINEAR)
    return out, r


def _apply_affine(boxes: np.ndarray, M: np.ndarray) -> np.ndarray:
    if len(boxes) == 0:
        return boxes.copy()
    x1, y1, x2, y2 = boxes.T
    pts = np.stack([np.stack([x1, y1], 1), np.stack([x2, y1], 1), np.stack([x1, y2], 1), np.stack([x2, y2], 1)], 1)
    pts = pts @ M[:, :2].T + M[:, 2]
    return np.concatenate([pts.min(1), pts.max(1)], 1).astype(np.float32)


def _hsv(img: np.ndarray, rng: random.Random, hgain=0.015, sgain=0.7, vgain=0.4) -> np.ndarray:
    r = np.array([rng.uniform(-1, 1) * hgain + 1, rng.uniform(-1, 1) * sgain + 1, rng.uniform(-1, 1) * vgain + 1])
    hue, sat, val = cv2.split(cv2.cvtColor(img, cv2.COLOR_RGB2HSV))
    x = np.arange(0, 256, dtype=r.dtype)
    lut_hue = ((x * r[0]) % 180).astype(np.uint8)
    lut_sat = np.clip(x * r[1], 0, 255).astype(np.uint8)
    lut_val = np.clip(x * r[2], 0, 255).astype(np.uint8)
    hsv = cv2.merge((cv2.LUT(hue, lut_hue), cv2.LUT(sat, lut_sat), cv2.LUT(val, lut_val)))
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)


class TrainAug(Dataset):
    """Letterbox + (mosaic) + random scale/translate + HSV + h-flip, all on an S canvas."""

    def __init__(self, base: Dataset, size: int = 1024, mosaic: float = 0.5, scale: float = 0.5,
                 translate: float = 0.1, hsv: bool = True, hflip: float = 0.5, seed: int = 0):
        self.base = base
        self.size = size
        self.mosaic = mosaic
        self.scale = scale
        self.translate = translate
        self.hsv = hsv
        self.hflip = hflip
        self.seed = seed
        self.epoch = 0

    def __len__(self):
        return len(self.base)

    def _mosaic(self, idx: int, rng: random.Random):
        S = self.size
        canvas = np.full((2 * S, 2 * S, 3), PAD_VALUE, np.uint8)
        xc, yc = int(rng.uniform(0.5 * S, 1.5 * S)), int(rng.uniform(0.5 * S, 1.5 * S))
        idxs = [idx] + [rng.randrange(len(self.base)) for _ in range(3)]
        out = None
        all_b, all_l, all_ib, all_im = [], [], [], []
        for k, j in enumerate(idxs):
            s = self.base[j]
            img, r = letterbox(s["img"], S)
            h, w = round(s["img"].shape[0] * r), round(s["img"].shape[1] * r)
            img = img[:h, :w]
            if k == 0:
                x1a, y1a, x2a, y2a = max(xc - w, 0), max(yc - h, 0), xc, yc
                x1b, y1b, x2b, y2b = w - (x2a - x1a), h - (y2a - y1a), w, h
                out = s
            elif k == 1:
                x1a, y1a, x2a, y2a = xc, max(yc - h, 0), min(xc + w, 2 * S), yc
                x1b, y1b, x2b, y2b = 0, h - (y2a - y1a), min(w, x2a - x1a), h
            elif k == 2:
                x1a, y1a, x2a, y2a = max(xc - w, 0), yc, xc, min(2 * S, yc + h)
                x1b, y1b, x2b, y2b = w - (x2a - x1a), 0, w, min(y2a - y1a, h)
            else:
                x1a, y1a, x2a, y2a = xc, yc, min(xc + w, 2 * S), min(2 * S, yc + h)
                x1b, y1b, x2b, y2b = 0, 0, min(w, x2a - x1a), min(y2a - y1a, h)
            canvas[y1a:y2a, x1a:x2a] = img[y1b:y2b, x1b:x2b]
            dx, dy = x1a - x1b, y1a - y1b
            off = np.array([dx, dy, dx, dy], np.float32)
            tile = np.array([x1a, y1a, x2a, y2a], np.float32)
            for boxes, dst in ((s["boxes"], all_b), (s["ign_boxes"], all_ib)):
                b = boxes * r + off
                b[:, 0::2] = b[:, 0::2].clip(tile[0], tile[2])
                b[:, 1::2] = b[:, 1::2].clip(tile[1], tile[3])
                dst.append(b)
            all_l.append(s["labels"])
            all_im.append(s["ign_masks"])
        # A mosaic mixes images of one source only, so one cls_mask holds for all tiles.
        out = dict(out)
        out.update(
            img=canvas, boxes=np.concatenate(all_b), labels=np.concatenate(all_l),
            ign_boxes=np.concatenate(all_ib), ign_masks=np.concatenate(all_im),
        )
        return out, 2 * S

    def __getitem__(self, idx: int):
        rng = random.Random((self.seed * 1_000_003 + self.epoch) * 7_919 + idx)
        S = self.size
        if self.mosaic > 0 and rng.random() < self.mosaic:
            s, C = self._mosaic(idx, rng)
            img, boxes, ign = s["img"], s["boxes"], s["ign_boxes"]
            sc = rng.uniform(1 - self.scale, 1 + self.scale)
        else:
            s = self.base[idx]
            img, r = letterbox(s["img"], S)
            boxes, ign = s["boxes"] * r, s["ign_boxes"] * r
            C = S
            sc = rng.uniform(1 - self.scale, 1 + self.scale)
        # x' = sc * (x - C/2) + S/2 + t
        tx = rng.uniform(-self.translate, self.translate) * S
        ty = rng.uniform(-self.translate, self.translate) * S
        M = np.array([[sc, 0, -sc * C / 2 + S / 2 + tx], [0, sc, -sc * C / 2 + S / 2 + ty]], np.float32)
        img = cv2.warpAffine(img, M, (S, S), flags=cv2.INTER_LINEAR, borderValue=(PAD_VALUE,) * 3)
        nb = _apply_affine(boxes, M)
        area0 = (nb[:, 2] - nb[:, 0]) * (nb[:, 3] - nb[:, 1])
        nb = nb.clip(0, S)
        w, h = nb[:, 2] - nb[:, 0], nb[:, 3] - nb[:, 1]
        keep = (w > 2) & (h > 2) & (w * h > 0.2 * area0) & (np.maximum(w / (h + 1e-6), h / (w + 1e-6)) < 20)
        nb, labels = nb[keep], s["labels"][keep]
        ib = _apply_affine(ign, M).clip(0, S)
        ikeep = ((ib[:, 2] - ib[:, 0]) > 1) & ((ib[:, 3] - ib[:, 1]) > 1)
        ib, im = ib[ikeep], s["ign_masks"][ikeep]
        if self.hsv:
            img = _hsv(img, rng)
        if self.hflip and rng.random() < self.hflip:
            img = np.ascontiguousarray(img[:, ::-1])
            nb = np.stack([S - nb[:, 2], nb[:, 1], S - nb[:, 0], nb[:, 3]], 1) if len(nb) else nb
            ib = np.stack([S - ib[:, 2], ib[:, 1], S - ib[:, 0], ib[:, 3]], 1) if len(ib) else ib
            labels = np.array([FLIP_IDX[int(i)] for i in labels], np.int64)
            im = im[:, FLIP_IDX]
        return {
            "img": img, "boxes": nb.astype(np.float32), "labels": labels, "ign_boxes": ib.astype(np.float32),
            "ign_masks": im, "cls_mask": s["cls_mask"], "img_id": s["img_id"], "src": s["src"],
            "scale": 1.0, "orig": (S, S),
        }


class EvalLetterbox(Dataset):
    """Letterbox only. Keeps the scale so boxes map back to original pixels."""

    def __init__(self, base: Dataset, size: int = 1024):
        self.base = base
        self.size = size

    def __len__(self):
        return len(self.base)

    def __getitem__(self, idx: int):
        s = self.base[idx]
        H, W = s["img"].shape[:2]
        img, r = letterbox(s["img"], self.size)
        return {
            "img": img, "boxes": s["boxes"] * r, "labels": s["labels"], "ign_boxes": s["ign_boxes"] * r,
            "ign_masks": s["ign_masks"], "cls_mask": s["cls_mask"], "img_id": s["img_id"], "src": s["src"],
            "scale": r, "orig": (W, H),
        }


# ---------------------------------------------------------------- batching


def qwen_patchify(img: np.ndarray, patch: int = 16, merge: int = 2, temporal: int = 2):
    """Qwen2-VL image processor on an already sized RGB uint8 canvas (H, W multiples of 32).

    Same rescale / normalise (mean = std = 0.5) and patch order as the HF processor,
    without its resize (the canvas is already the target size).
    """
    H, W = img.shape[:2]
    x = torch.from_numpy(img).permute(2, 0, 1).float().div_(255.0).sub_(0.5).div_(0.5)
    x = x.unsqueeze(0).expand(temporal, -1, -1, -1)
    gh, gw = H // patch, W // patch
    x = x.reshape(1, temporal, 3, gh // merge, merge, patch, gw // merge, merge, patch)
    x = x.permute(0, 3, 6, 4, 7, 2, 1, 5, 8).reshape(gh * gw, 3 * temporal * patch * patch)
    return x, torch.tensor([1, gh, gw], dtype=torch.long)


def loc_collate(batch):
    pix, grids = zip(*(qwen_patchify(b["img"]) for b in batch))
    S = batch[0]["img"].shape[0]
    gt_boxes, gt_labels, ign = [], [], []
    for b in batch:
        bx = b["boxes"]
        cxcywh = np.stack([(bx[:, 0] + bx[:, 2]) / 2 / S, (bx[:, 1] + bx[:, 3]) / 2 / S,
                           (bx[:, 2] - bx[:, 0]) / S, (bx[:, 3] - bx[:, 1]) / S], 1) if len(bx) else np.zeros((0, 4))
        gt_boxes.append(cxcywh.clip(0, 1).tolist())
        gt_labels.append(b["labels"].tolist())
        ign.append((torch.from_numpy(b["ign_boxes"] / S), torch.from_numpy(b["ign_masks"])))
    return {
        "pixel_values": torch.cat(pix, 0),
        "grid_thw": torch.stack(grids, 0),
        "gt_boxes": gt_boxes,
        "gt_labels": gt_labels,
        "cls_mask": torch.from_numpy(np.stack([b["cls_mask"] for b in batch])),
        "ignore": ign,
        "img_ids": [b["img_id"] for b in batch],
        "scales": [b["scale"] for b in batch],
        "origs": [b["orig"] for b in batch],
        "size": S,
    }


def build_cls_weight(cls_mask: torch.Tensor, ignore, anchor_px: torch.Tensor, size: int) -> torch.Tensor:
    """(B, A, C) weight: 0 for classes not annotated or anchors inside an ignore region."""
    B, C = cls_mask.shape
    A = anchor_px.shape[0]
    w = cls_mask.float().view(B, 1, C).expand(B, A, C).clone()
    pts = anchor_px / float(size)
    for b, (boxes, masks) in enumerate(ignore):
        if len(boxes) == 0:
            continue
        boxes = boxes.to(pts.device, pts.dtype)
        inside = ((pts[:, None, 0] >= boxes[None, :, 0]) & (pts[:, None, 0] <= boxes[None, :, 2])
                  & (pts[:, None, 1] >= boxes[None, :, 1]) & (pts[:, None, 1] <= boxes[None, :, 3]))
        hit = inside.float() @ masks.to(pts.device).float()  # (A, C)
        w[b] = w[b] * (hit <= 0).float()
    return w


# ---------------------------------------------------------------- COCO-format GT for eval


def make_coco_gt(ds: Dataset, classes: list[str]) -> COCO:
    """GT in original pixels. Ignore regions become iscrowd=1 boxes per masked class."""
    keep = [CID[c] for c in classes]
    images, anns = [], []
    aid = 1
    for idx in range(len(ds)):
        if isinstance(ds, CocoWholeBodyParts):
            img_id = ds.ids[idx]
            _, W, H = ds.images[img_id]
            boxes, labels, ign, ign_m = ds.targets(img_id)
        elif isinstance(ds, CrowdHumanDet):
            img_id = idx
            _, W, H, _ = ds.items[idx]
            boxes, labels, ign, ign_m = ds.targets(idx)
        else:
            raise TypeError(type(ds))
        images.append({"id": int(img_id), "width": int(W), "height": int(H)})
        for b, l in zip(boxes, labels):
            if l not in keep:
                continue
            anns.append({"id": aid, "image_id": int(img_id), "category_id": int(l) + 1,
                         "bbox": [b[0], b[1], b[2] - b[0], b[3] - b[1]],
                         "area": float((b[2] - b[0]) * (b[3] - b[1])), "iscrowd": 0})
            aid += 1
        for b, m in zip(ign, ign_m):
            for c in keep:
                if m[c]:
                    anns.append({"id": aid, "image_id": int(img_id), "category_id": c + 1,
                                 "bbox": [b[0], b[1], b[2] - b[0], b[3] - b[1]],
                                 "area": float((b[2] - b[0]) * (b[3] - b[1])), "iscrowd": 1})
                    aid += 1
    coco = COCO()
    coco.dataset = {"images": images, "annotations": anns,
                    "categories": [{"id": CID[c] + 1, "name": c} for c in classes]}
    coco.createIndex()
    return coco


def eval_image_ids(ds: Dataset) -> list[int]:
    if isinstance(ds, CocoWholeBodyParts):
        return list(ds.ids)
    return list(range(len(ds)))
