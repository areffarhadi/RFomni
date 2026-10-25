"""Human sidecar v2: loc v2 (person / head / face / hands / limbs) + pose v2.

Drop-in for `OmniHumanSidecar` (same `analyze` / `to_omni_messages`), plus
`detect()` for any of the 11 body classes. One frozen mix87 vision tower.

    from omni_grounding.omni_human_v2 import OmniHumanSidecarV2
    side = OmniHumanSidecarV2(device="cuda:0")
    faces = side.detect(pil_image, ["face"])
    out = side.analyze(pil_image)

Loc: letterbox 1024, all 27 blocks, optional h-flip TTA. Pose: 384x288 crops,
flip test + DARK decoding.
"""

from __future__ import annotations

import os
from typing import Sequence

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision.ops import batched_nms

from omni_grounding.data.coco_pose import COCO_KPT_FLIP
from omni_grounding.data.loc_v2 import FLIP_IDX, VOCAB, letterbox, qwen_patchify
from omni_grounding.data.pose_v2 import box_to_center_scale, warp_matrix
from omni_grounding.evaluation.decode_yolo_ultralytics import decode_yolo_v8
from omni_grounding.grounding.pose_draw import KPT_NAMES, draw_skeleton, pose_cues
from omni_grounding.grounding.pose_head_v2 import OmniPoseV2, dark_decode, flip_heatmaps, heatmap_to_input
from omni_grounding.grounding.yolo_world import OmniYoloWorldDetector
from omni_grounding.omni_human import TEXT_CACHE, OmniHumanSidecar
from omni_vision_encoder import DEFAULT_MIX87_ENCODER_PATH, FrozenOmniVisionEncoder

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOC_V2_CKPT = os.path.join(REPO, "exp/loc_v2/best.pt")
POSE_V2_CKPT = os.path.join(REPO, "exp/pose_v2/best.pt")
ALL_TAPS = list(range(27))


def _invert(M: np.ndarray) -> np.ndarray:
    Ai = np.linalg.inv(M[:, :2])
    return np.concatenate([Ai, (-Ai @ M[:, 2])[:, None]], 1)


class OmniHumanSidecarV2(OmniHumanSidecar):
    def __init__(
        self,
        loc_ckpt: str = LOC_V2_CKPT,
        pose_ckpt: str = POSE_V2_CKPT,
        text_cache: str = TEXT_CACHE,
        encoder_path: str = DEFAULT_MIX87_ENCODER_PATH,
        device: str | torch.device = "cuda:0",
    ):
        self.device = torch.device(device)
        self.amp = self.device.type == "cuda"
        self.encoder = FrozenOmniVisionEncoder(encoder_path, dtype=torch.bfloat16 if self.amp else torch.float32)
        self.encoder.eval().to(self.device)

        loc = torch.load(loc_ckpt, map_location="cpu", weights_only=False)
        la = loc["args"]
        self.loc_size = int(loc.get("image_size", la.get("image_size", 1024)))
        self.loc = OmniYoloWorldDetector(
            feature_dim=self.encoder.d_model, d_model=int(la["d_model"]), num_streams=27,
            text_dim=int(loc["text_dim"]), reg_max=int(la["reg_max"]), use_p2=True, drop_p5=False,
            fpn_kind="vitdet_all",
        ).to(self.device)
        self.loc.load_state_dict(loc["model"])
        self.loc.eval()

        self.pose = None
        self.pose_chunk = 8
        if pose_ckpt:
            self._load_pose(pose_ckpt)

        cache = torch.load(text_cache, map_location="cpu", weights_only=False)
        assert cache["vocab"] == VOCAB, cache["vocab"]
        self.text = F.normalize(cache["eval_text"].float(), dim=-1).to(self.device)

    def _load_pose(self, pose_ckpt: str):
        pose = torch.load(pose_ckpt, map_location="cpu", weights_only=False)
        pa = pose["args"]
        self.pose_hw = tuple(pose["input_hw"])
        self.pose = OmniPoseV2(
            self.encoder.d_model, int(pa["d_model"]), 27, int(pa["num_blocks"]), int(pa["heads"]),
            tuple(pose["grid_hw"]), num_sources=len(pose.get("sources", ["coco"])),
        ).to(self.device)
        self.pose.load_state_dict(pose["model"])
        self.pose.eval()

    # ------------------------------------------------------------ encoder

    def _feats(self, canvases: Sequence[np.ndarray]):
        pix, grids = zip(*(qwen_patchify(c) for c in canvases))
        grid = torch.stack(grids, 0)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=self.amp):
            feats = self.encoder.encode_pixels(
                torch.cat(pix, 0), grid, ALL_TAPS, self.device,
                out_dtype=torch.bfloat16 if self.amp else torch.float32,
            )
        return feats, (int(grid[0, 1]), int(grid[0, 2]))

    # ------------------------------------------------------------ detection

    @torch.no_grad()
    def detect(
        self,
        image: Image.Image | np.ndarray,
        classes: Sequence[str] = ("person",),
        score_thresh: float = 0.25,
        nms_iou: float = 0.5,
        max_dets: int = 300,
        flip_tta: bool = False,
        sizes: Sequence[int] | None = None,
    ) -> list[dict]:
        """Boxes in original pixels: [{"label", "score", "box_xyxy_px"}], best first.

        `sizes` (canvas sides, multiples of 32) and `flip_tta` add test-time views,
        merged by class-wise NMS.
        """
        img = np.asarray(image.convert("RGB")) if isinstance(image, Image.Image) else image
        H, W = img.shape[:2]
        cls_idx = [VOCAB.index(c) for c in classes]
        boxes, scores, labels = [], [], []
        for size in sizes or [self.loc_size]:
            canvas, r = letterbox(img, size)
            views = [(canvas, cls_idx, False)]
            if flip_tta:
                views.append((np.ascontiguousarray(canvas[:, ::-1]), [FLIP_IDX[i] for i in cls_idx], True))
            for cv, idx, flipped in views:
                feats, grid = self._feats([cv])
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=self.amp):
                    outs = self.loc(feats, grid, self.text[idx])
                pb, ps, pc = decode_yolo_v8(outs, size, score_thresh=score_thresh, nms_iou=nms_iou, max_dets=max_dets)
                pb = pb.float() * size
                if flipped:
                    pb = torch.stack([size - pb[:, 2], pb[:, 1], size - pb[:, 0], pb[:, 3]], 1)
                boxes.append(pb / r)
                scores.append(ps.float())
                labels.append(pc)
        pb, ps, pc = torch.cat(boxes), torch.cat(scores), torch.cat(labels)
        if len(boxes) > 1 and pb.numel():
            keep = batched_nms(pb, ps, pc, nms_iou)[:max_dets]
            pb, ps, pc = pb[keep], ps[keep], pc[keep]
        pb = pb.cpu().numpy()
        pb[:, 0::2] = pb[:, 0::2].clip(0, W)
        pb[:, 1::2] = pb[:, 1::2].clip(0, H)
        out = [{"label": classes[int(c)], "score": float(s), "box_xyxy_px": [float(v) for v in b]}
               for b, s, c in zip(pb, ps.cpu().tolist(), pc.cpu().tolist())]
        return sorted(out, key=lambda d: -d["score"])

    @torch.no_grad()
    def detect_people(self, image: Image.Image, score_thresh: float = 0.25, max_people: int = 5):
        W, H = image.size
        people = []
        for d in self.detect(image, ["person"], score_thresh=score_thresh)[:max_people]:
            x1, y1, x2, y2 = d["box_xyxy_px"]
            people.append({"score": d["score"], "box_xyxy_px": [x1, y1, x2, y2],
                           "box_xyxy_norm": [x1 / W, y1 / H, x2 / W, y2 / H]})
        return people

    # ------------------------------------------------------------ pose

    @torch.no_grad()
    def pose_people(self, image: Image.Image | np.ndarray, boxes_xyxy_px, flip_test: bool = True):
        """(N, 17, 3) keypoints [x, y, conf] in original pixels for N person boxes."""
        img = np.asarray(image.convert("RGB")) if isinstance(image, Image.Image) else image
        if len(boxes_xyxy_px) == 0:
            return np.zeros((0, len(KPT_NAMES), 3), np.float32)
        oh, ow = self.pose_hw
        crops, Ms = [], []
        for x1, y1, x2, y2 in boxes_xyxy_px:
            c, s = box_to_center_scale(x1, y1, x2 - x1, y2 - y1, ow / oh)
            M = warp_matrix(c, s, 0.0, self.pose_hw)
            crops.append(cv2.warpAffine(img, M, (ow, oh), flags=cv2.INTER_LINEAR))
            Ms.append(M)
        hms = []
        for i in range(0, len(crops), self.pose_chunk):  # bounded memory in crowded images
            part = crops[i:i + self.pose_chunk]
            feats, _ = self._feats(part)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=self.amp):
                h = self.pose(feats).float()
                if flip_test:
                    ff, _ = self._feats([np.ascontiguousarray(c[:, ::-1]) for c in part])
                    h = 0.5 * (h + flip_heatmaps(self.pose(ff).float(), COCO_KPT_FLIP))
            hms.append(h)
        hm = torch.cat(hms, 0)
        xy, conf = dark_decode(hm)
        xy = heatmap_to_input(xy, self.pose_hw, hm.shape[-2:])
        out = np.zeros((len(crops), xy.shape[1], 3), np.float32)
        for i, M in enumerate(Ms):
            Mi = _invert(M)
            out[i, :, :2] = xy[i] @ Mi[:, :2].T + Mi[:, 2]
            out[i, :, 2] = conf[i]
        return out

    @torch.no_grad()
    def pose_person(self, image: Image.Image, box_xyxy_px, conf_thresh: float = 0.2):
        W, H = image.size
        k = self.pose_people(image, [box_xyxy_px])[0]
        kpts = [{"name": n, "x": float(x), "y": float(y), "x_norm": float(x) / W, "y_norm": float(y) / H,
                 "conf": float(c), "visible": float(c) >= conf_thresh} for n, (x, y, c) in zip(KPT_NAMES, k)]
        return {"keypoints": kpts, "xy_px": [(float(x), float(y)) for x, y, _ in k],
                "conf": [float(c) for c in k[:, 2]], "mean_conf": float(k[:, 2].mean())}

    @torch.no_grad()
    def analyze(self, image: Image.Image, score_thresh: float = 0.25, kpt_thresh: float = 0.2,
                max_people: int = 5) -> dict:
        image = image.convert("RGB")
        people = self.detect_people(image, score_thresh=score_thresh, max_people=max_people)
        kps = self.pose_people(image, [p["box_xyxy_px"] for p in people])
        overlay = image.copy()
        records = []
        W, H = image.size
        for i, (p, k) in enumerate(zip(people, kps)):
            kpts = [{"name": n, "x": float(x), "y": float(y), "x_norm": float(x) / W, "y_norm": float(y) / H,
                     "conf": float(c), "visible": float(c) >= kpt_thresh} for n, (x, y, c) in zip(KPT_NAMES, k)]
            records.append({"id": i, "det_score": p["score"], "box_xyxy_px": p["box_xyxy_px"],
                            "box_xyxy_norm": p["box_xyxy_norm"], "pose_mean_conf": float(k[:, 2].mean()),
                            "cues": pose_cues(kpts, p["box_xyxy_px"]), "keypoints": kpts})
            draw_skeleton(overlay, [(float(x), float(y)) for x, y, _ in k],
                          [float(c) if c >= kpt_thresh else 0.0 for c in k[:, 2]], thresh=kpt_thresh)
        return {"n_people": len(records), "people": records, "omni_text": self.to_tool_text(records, image.size),
                "overlay": overlay}
