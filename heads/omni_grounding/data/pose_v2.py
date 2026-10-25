"""Top-down pose v2 data: affine person crops (H x W = 384 x 288) in COCO-17 order.

mmpose-style training transforms: aspect-fixed box with 1.25 padding, random
shift / scale / rotation, half-body, h-flip with joint swap, UDP warp. Extra
datasets (CrowdPose, MPII) map their joints onto COCO-17 and set weight 0 for
joints they do not label.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import cv2
import numpy as np
import torch
from pycocotools.coco import COCO
from torch.utils.data import Dataset

from omni_grounding.data.coco_pose import COCO_KPT_FLIP, NUM_KPTS
from omni_grounding.data.loc_v2 import DATA_ROOT, qwen_patchify

COCO_ROOT = DATA_ROOT / "coco2017"
UPPER = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
LOWER = [11, 12, 13, 14, 15, 16]
# COCO-17 index for each CrowdPose-14 joint (-1 = not in COCO: head top, neck).
CROWDPOSE_TO_COCO = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, -1, -1]
# MPII-16: r_ankle r_knee r_hip l_hip l_knee l_ankle pelvis thorax neck head_top r_wrist r_elbow r_shoulder l_shoulder l_elbow l_wrist
MPII_TO_COCO = [16, 14, 12, 11, 13, 15, -1, -1, -1, -1, 10, 8, 6, 5, 7, 9]


def box_to_center_scale(x, y, w, h, aspect: float, pad: float = 1.25):
    """aspect = W / H of the crop. Returns center (2,), scale (w, h) in pixels."""
    c = np.array([x + 0.5 * w, y + 0.5 * h], np.float32)
    if w > aspect * h:
        h = w / aspect
    else:
        w = h * aspect
    return c, np.array([w, h], np.float32) * pad


def warp_matrix(center, scale, rot_deg: float, out_hw) -> np.ndarray:
    """UDP warp: source pixel -> crop pixel, crop corners at 0 and (W-1, H-1)."""
    oh, ow = out_hw
    sx, sy = (ow - 1) / scale[0], (oh - 1) / scale[1]
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    R = np.array([[ca, -sa], [sa, ca]], np.float32)
    S = np.diag([sx, sy]).astype(np.float32)
    A = S @ R
    t = np.array([(ow - 1) / 2, (oh - 1) / 2], np.float32) - A @ center
    return np.concatenate([A, t[:, None]], 1)


class PoseItems:
    """One person per row: (img_path, bbox xywh, kpts (17, 3), img_id, area, source)."""

    @staticmethod
    def coco(split: str, max_images: int = 0, min_kpts: int = 1):
        coco = COCO(str(COCO_ROOT / "annotations" / f"person_keypoints_{split}2017.json"))
        img_dir = COCO_ROOT / ("train2017" if split == "train" else "val2017")
        ids = sorted(coco.getImgIds(catIds=[1]))
        if max_images:
            ids = ids[:max_images]
        rows = []
        for iid in ids:
            info = coco.imgs[iid]
            for a in coco.imgToAnns.get(iid, []):
                if a.get("iscrowd") or a.get("category_id") != 1 or a.get("num_keypoints", 0) < min_kpts:
                    continue
                x, y, w, h = a["bbox"]
                if w * h < 16:
                    continue
                k = np.array(a["keypoints"], np.float32).reshape(NUM_KPTS, 3)
                rows.append((str(img_dir / info["file_name"]), (x, y, w, h), k, iid, float(a["area"]), "coco"))
        return rows, coco

    @staticmethod
    def crowdpose(split: str = "trainval"):
        root = DATA_ROOT / "crowdpose"
        js = json.load(open(root / "annotations" / f"mmpose_crowdpose_{split}.json"))
        imgs = {im["id"]: im for im in js["images"]}
        rows = []
        for a in js["annotations"]:
            if a.get("iscrowd") or a.get("num_keypoints", 0) < 1:
                continue
            src = np.array(a["keypoints"], np.float32).reshape(14, 3)
            k = np.zeros((NUM_KPTS, 3), np.float32)
            for i, j in enumerate(CROWDPOSE_TO_COCO):
                if j >= 0:
                    k[j] = src[i]
            x, y, w, h = a["bbox"]
            rows.append((str(root / "images" / imgs[a["image_id"]]["file_name"]), (x, y, w, h), k,
                         a["image_id"], float(w * h), "crowdpose"))
        return rows

    @staticmethod
    def mpii(split: str = "train"):
        root = DATA_ROOT / "mpii"
        js = json.load(open(root / "annotations" / f"mpii_{split}.json"))
        rows = []
        for i, a in enumerate(js):
            src = np.array(a["joints"], np.float32)
            v = np.array(a["joints_vis"], np.float32)
            k = np.zeros((NUM_KPTS, 3), np.float32)
            for s, j in enumerate(MPII_TO_COCO):
                if j >= 0 and v[s] > 0:
                    k[j] = [src[s, 0], src[s, 1], 2]
            c = np.array(a["center"], np.float32)
            h = float(a["scale"]) * 200.0
            c[1] += 15 * a["scale"]  # mmpose MPII center fix
            w = h * 0.75
            rows.append((str(root / "images" / a["image"]), (c[0] - w / 2, c[1] - h / 2, w, h), k, i, w * h, "mpii"))
        return rows


class TopDownPose(Dataset):
    def __init__(self, rows, train: bool, input_hw=(384, 288), seed: int = 0, rot: float = 40.0,
                 scale_range=(0.65, 1.35), shift: float = 0.16, half_body: float = 0.3, flip: float = 0.5):
        self.rows = rows
        self.train = train
        self.input_hw = input_hw
        self.aspect = input_hw[1] / input_hw[0]
        self.seed, self.rot, self.scale_range, self.shift = seed, rot, scale_range, shift
        self.half_body, self.flip = half_body, flip
        self.epoch = 0

    def __len__(self):
        return len(self.rows)

    def _half_body(self, k, rng):
        vis = k[:, 2] > 0
        if vis.sum() < 8:
            return None
        up = [j for j in UPPER if vis[j]]
        lo = [j for j in LOWER if vis[j]]
        sel = up if (rng.random() < 0.5 and len(up) > 2) or len(lo) <= 2 else lo
        if len(sel) < 2:
            return None
        pts = k[sel, :2]
        x1, y1 = pts.min(0)
        x2, y2 = pts.max(0)
        c, s = box_to_center_scale(x1, y1, x2 - x1, y2 - y1, self.aspect, pad=1.5)
        return c, s

    def __getitem__(self, idx: int):
        path, (x, y, w, h), k, img_id, area, src = self.rows[idx]
        img = cv2.cvtColor(cv2.imread(path, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        k = k.copy()
        c, s = box_to_center_scale(x, y, w, h, self.aspect)
        rot, flipped = 0.0, False
        if self.train:
            rng = random.Random((self.seed * 1_000_003 + self.epoch) * 7_919 + idx)
            if rng.random() < self.half_body:
                hb = self._half_body(k, rng)
                if hb is not None:
                    c, s = hb
            if rng.random() < 0.3:
                c = c + np.array([rng.uniform(-1, 1), rng.uniform(-1, 1)], np.float32) * self.shift * s
            s = s * rng.uniform(*self.scale_range)
            if rng.random() < 0.6:
                rot = rng.uniform(-1, 1) * self.rot
            flipped = rng.random() < self.flip
        M = warp_matrix(c, s, rot, self.input_hw)
        oh, ow = self.input_hw
        crop = cv2.warpAffine(img, M, (ow, oh), flags=cv2.INTER_LINEAR)
        xy = k[:, :2] @ M[:, :2].T + M[:, 2]
        wgt = (k[:, 2] > 0).astype(np.float32)
        if flipped:
            crop = np.ascontiguousarray(crop[:, ::-1])
            xy[:, 0] = (ow - 1) - xy[:, 0]
            xy, wgt = xy[COCO_KPT_FLIP], wgt[COCO_KPT_FLIP]
        return {"img": crop, "kpts": xy.astype(np.float32), "weight": wgt, "M": M, "img_id": img_id,
                "bbox": (x, y, w, h), "area": area, "src": src}


def pose_collate(batch):
    pix, grids = zip(*(qwen_patchify(b["img"]) for b in batch))
    return {
        "pixel_values": torch.cat(pix, 0),
        "grid_thw": torch.stack(grids, 0),
        "flip_pixel_values": torch.cat([qwen_patchify(np.ascontiguousarray(b["img"][:, ::-1]))[0] for b in batch], 0),
        "kpts": torch.from_numpy(np.stack([b["kpts"] for b in batch])),
        "weight": torch.from_numpy(np.stack([b["weight"] for b in batch])),
        "M": [b["M"] for b in batch],
        "img_ids": [b["img_id"] for b in batch],
        "bboxes": [b["bbox"] for b in batch],
        "areas": [b["area"] for b in batch],
        "srcs": [b["src"] for b in batch],
    }


def pose_collate_train(batch):
    pix, grids = zip(*(qwen_patchify(b["img"]) for b in batch))
    return {
        "pixel_values": torch.cat(pix, 0),
        "grid_thw": torch.stack(grids, 0),
        "kpts": torch.from_numpy(np.stack([b["kpts"] for b in batch])),
        "weight": torch.from_numpy(np.stack([b["weight"] for b in batch])),
        "srcs": [b["src"] for b in batch],
    }
