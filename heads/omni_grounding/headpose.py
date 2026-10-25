"""Headpose expert — head Euler angles from the frozen mix87 pose head.

The pose head localizes nose/eyes/ears well on face crops; the angles come from the
*geometry* of those keypoints, not from the body-pose cues. Yaw and roll are read from
hand geometry, pitch from a linear probe on the frozen encoder. Both are flip-augmented,
and keypoints are averaged over multi-scale + mirrored passes (mirror averaging cancels
the head's left/right bias, which is what corrupts an eye-line angle).

    expert = HeadposeExpert(human_sidecar)
    out = expert.angles(pil_image)     # {'yaw':.., 'pitch':.., 'roll':.., 'conf':..}

Measured on FaceXBench headpose (400 jobs): MAE 7.1 / 6.8 / 8.7 deg (yaw/pitch/roll),
head-only MCQ 50.25% vs 36.67% for the LLM alone. See
docs/localization_inference_sweep.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from omni_grounding.data.coco_pose_topdown import PAD, _square_box

REPO = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = REPO / "checkpoints/headpose_expert/model.json"
# The shipped model is fitted on ALL FaceXBench angle labels, so scoring FaceXBench with
# it would leak. The out-of-fold cache is therefore the DEFAULT: pass ``oof_cache=None``
# to opt out deliberately (e.g. genuinely unseen images).
DEFAULT_OOF = REPO / "checkpoints/headpose_expert/facex_oof_angles.json"
_UNSET = object()

# COCO left/right joint pairs, swapped when the image is mirrored
SWAP = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16)]

# Abstain below this mean confidence over the 5 facial keypoints: the geometry is then
# too noisy for a one-bin answer and the expert should stay quiet rather than mislead.
MIN_FACE_CONF = 0.25


def _wrap(a: float) -> float:
    while a > 90.0:
        a -= 180.0
    while a <= -90.0:
        a += 180.0
    return a


def head_geom(kp: list[list[float]]) -> dict[str, float] | None:
    """Geometric head-pose proxies from nose/eyes/ears. kp: 17x[x_norm, y_norm, conf]."""
    if not kp or len(kp) < 5:
        return None
    (nx, ny, nc), (lex, ley, lec), (rex, rey, rec), (lax, lay, lac), (rax, ray, rac) = \
        [tuple(kp[i]) for i in range(5)]
    ex, ey = rex - lex, rey - ley
    eye_d = float(np.hypot(ex, ey)) + 1e-6
    mid_x, mid_y = (lex + rex) / 2, (ley + rey) / 2
    ear_x, ear_y = rax - lax, ray - lay
    ear_d = float(np.hypot(ear_x, ear_y)) + 1e-6
    ear_mid_x, ear_mid_y = (lax + rax) / 2, (lay + ray) / 2
    return {
        "roll_eye": _wrap(float(np.degrees(np.arctan2(ey, ex)))),
        "roll_ear": _wrap(float(np.degrees(np.arctan2(ear_y, ear_x)))),
        "roll_nose": float(np.degrees(np.arctan2(nx - mid_x, ny - mid_y + 1e-6))),
        "yaw_nose_off": (nx - mid_x) / eye_d,
        "yaw_eye_ear": (mid_x - ear_mid_x) / ear_d,
        "yaw_ear_ratio": eye_d / ear_d,
        "yaw_ear_conf": rac - lac,
        "yaw_eye_conf": rec - lec,
        "pitch_nose_off": (ny - mid_y) / eye_d,
        "pitch_ear_off": (ear_mid_y - mid_y) / eye_d,
        "pitch_nose_ear": (ny - ear_mid_y) / eye_d,
        "eye_d": eye_d,
        "ear_d": ear_d,
        "conf5": (nc + lec + rec + lac + rac) / 5,
    }


def _unmirror(kp: np.ndarray) -> np.ndarray:
    out = kp.copy()
    out[:, 0] = 1.0 - out[:, 0]
    for a, b in SWAP:
        out[[a, b]] = out[[b, a]]
    return out


class HeadposeExpert:
    """Wraps an :class:`OmniHumanSidecar` and turns its keypoints into head angles."""

    def __init__(self, human, model_path: str | os.PathLike = DEFAULT_MODEL,
                 oof_cache: str | os.PathLike | None = _UNSET):
        if oof_cache is _UNSET:
            oof_cache = DEFAULT_OOF
        if getattr(human, "pose_version", "v1") != "v1" or getattr(human, "loc_version", "v1") != "v1":
            # The angle ridge was fitted on v1 boxes + keypoints with v1 crop-size TTA; keep
            # those heads (sharing the same frozen encoder) so validated numbers are unchanged.
            from omni_grounding.omni_human import LOC_V1_CKPT, POSE_V1_CKPT, OmniHumanSidecar

            human = OmniHumanSidecar(device=human.device, encoder=human.encoder,
                                     loc_ckpt=LOC_V1_CKPT, pose_ckpt=POSE_V1_CKPT)
        self.human = human
        self.spec = json.loads(Path(model_path).read_text())
        self.geom_keys = self.spec["geom_keys"]
        self.tta = self.spec["tta"]
        self._oof: dict[str, dict] = {}
        if oof_cache and Path(oof_cache).exists():
            self._oof = json.loads(Path(oof_cache).read_text())

    # -- model ---------------------------------------------------------------
    def _apply(self, axis: str, x: np.ndarray) -> float:
        m = self.spec["models"][axis]
        mu, sd, w = np.array(m["mu"]), np.array(m["sd"]), np.array(m["w"])
        xn = np.concatenate([(np.asarray(x, float) - mu) / sd, [1.0]])
        return float(xn @ w)

    # -- keypoints -----------------------------------------------------------
    @torch.no_grad()
    def _pose_at(self, img: Image.Image, size: int) -> np.ndarray | None:
        old = self.human.pose_crop
        self.human.pose_crop = size
        try:
            people = self.human.detect_people(img, score_thresh=0.05, max_people=1)
            if not people:
                return None
            rec = self.human.pose_person(img, people[0]["box_xyxy_px"])
            return np.array([[k["x_norm"], k["y_norm"], k["conf"]] for k in rec["keypoints"]], float)
        finally:
            self.human.pose_crop = old

    @torch.no_grad()
    def keypoints(self, img: Image.Image) -> np.ndarray | None:
        """Multi-scale + mirror-averaged keypoints."""
        mats = []
        flip = img.transpose(Image.FLIP_LEFT_RIGHT) if self.tta["mirror"] else None
        for s in self.tta["sizes"]:
            a = self._pose_at(img, s)
            if a is not None:
                mats.append(a)
            if flip is not None:
                b = self._pose_at(flip, s)
                if b is not None:
                    mats.append(_unmirror(b))
        if not mats:
            return None
        M = np.stack(mats)
        if self.tta["conf_weight"]:
            w = M[:, :, 2:3] + 1e-3
            xy = (M[:, :, :2] * w).sum(0) / w.sum(0)
        else:
            xy = M[:, :, :2].mean(0)
        return np.concatenate([xy, M[:, :, 2].mean(0)[:, None]], 1)

    @torch.no_grad()
    def _enc_pooled(self, img: Image.Image, size: int = 256) -> np.ndarray:
        people = self.human.detect_people(img, score_thresh=0.05, max_people=1)
        crop = img
        if people:
            ow, oh = img.size
            x1, y1, x2, y2 = people[0]["box_xyxy_px"]
            cx1, cy1, side = _square_box(x1, y1, max(x2 - x1, 1.0), max(y2 - y1, 1.0), ow, oh, pad=PAD)
            crop = img.crop((int(cx1), int(cy1), int(cx1 + side), int(cy1 + side)))
        vis = crop.convert("RGB").resize((size, size), Image.BILINEAR)
        feats, _, _ = self.human._encode([vis], self.human.pose_taps, size)
        f = feats.float()
        if f.dim() == 4:
            f = f.mean(1)
        return torch.cat([f.mean(1), f.amax(1)], -1)[0].cpu().numpy().astype(np.float32)

    # -- public --------------------------------------------------------------
    @torch.no_grad()
    def angles(self, img: Image.Image, path: str | None = None) -> dict | None:
        """Head Euler angles in degrees, or None if the expert abstains."""
        if path and path in self._oof:            # leak-free FaceXBench evaluation
            c = self._oof[path]
            return {"yaw": c.get("yaw"), "pitch": c.get("pitch"), "roll": c.get("roll"),
                    "conf": float(c.get("conf", 0.5)), "source": "oof_cache"}
        kp = self.keypoints(img)
        if kp is None:
            return None
        g = head_geom(kp.tolist())
        if g is None or g["conf5"] < MIN_FACE_CONF:
            return None                            # abstain: keypoints too weak
        gx = np.array([g[k] for k in self.geom_keys])
        out = {"conf": round(float(g["conf5"]), 3), "source": "live"}
        for a in self.spec["axes"]:
            x = self._enc_pooled(img) if self.spec["models"][a]["kind"] == "enc" else gx
            out[a] = round(self._apply(a, x), 1)
        return out
