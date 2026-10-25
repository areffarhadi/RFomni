"""Human sidecar for mix87 Omni: loc person boxes + heatmap pose.

One frozen vision encoder. Localization finds people; the pose head runs on
each person crop (same recipe as training). Default pose head is v2
(checkpoints/pose_v2: 384x288 crop, all 27 blocks, flip + DARK) and the default
person detector is loc v2 (checkpoints/loc_v2: 1024 letterbox). Pass
`loc_ckpt=LOC_V1_CKPT, pose_ckpt=POSE_V1_CKPT` for the v1 heads. Omni consumes the result
as tool text and/or a skeleton overlay image — no LLM training required.

    from omni_grounding.omni_human import OmniHumanSidecar
    side = OmniHumanSidecar(device="cuda:0")
    out = side.analyze(pil_image)
    messages = side.to_omni_messages(pil_image, out, question="What is the person doing?")
"""

from __future__ import annotations

import os
from typing import Sequence

import torch
import torch.nn.functional as F
from PIL import Image

import cv2
import numpy as np

from omni_grounding.data.coco_pose import COCO_KPT_FLIP
from omni_grounding.data.coco_pose_topdown import PAD, _square_box
from omni_grounding.data.loc_v2 import letterbox, qwen_patchify
from omni_grounding.data.pose_v2 import box_to_center_scale, warp_matrix
from omni_grounding.grounding.pose_head_v2 import OmniPoseV2, dark_decode, flip_heatmaps, heatmap_to_input
from omni_grounding.evaluation.decode_yolo_ultralytics import decode_yolo_v8
from omni_grounding.grounding.pose_draw import KPT_NAMES, draw_skeleton, pose_cues
from omni_grounding.grounding.vitpose_head import OmniVitPoseHeatmap, decode_heatmap
from omni_grounding.grounding.yolo_world import OmniYoloWorldDetector
from omni_vision_encoder import DEFAULT_MIX87_ENCODER_PATH, FrozenOmniVisionEncoder

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _first_existing(*paths: str) -> str:
    for p in paths:
        if os.path.isfile(p):
            return p
    return paths[0]


LOC_V1_CKPT = _first_existing(
    os.path.join(REPO, "checkpoints/localize_body_caption/best.pt"),
    os.path.join(REPO, "exp/localize_yolo_ov_qwen_body_caption/best.pt"),
)
# Loc v2 (1024 letterbox, P2-P5, WIDER/CrowdHuman/WholeBody labels) is the default person detector.
LOC_CKPT = _first_existing(os.path.join(REPO, "checkpoints/loc_v2/best.pt"), LOC_V1_CKPT)
POSE_V1_CKPT = _first_existing(
    os.path.join(REPO, "checkpoints/pose_heatmap/best.pt"),
    os.path.join(REPO, "exp/localize_vitpose_heatmap/best.pt"),
)
# Pose v2 (384x288 crops, all 27 blocks, flip + DARK; GT-box AP 0.750 vs 0.237) is the default.
POSE_CKPT = _first_existing(os.path.join(REPO, "checkpoints/pose_v2/best.pt"), POSE_V1_CKPT)
TEXT_CACHE = _first_existing(
    os.path.join(REPO, "checkpoints/text/qwen_thinker_text_cache_body_caption.pt"),
    os.path.join(REPO, "exp/qwen_thinker_text_cache_body_caption.pt"),
)


class OmniHumanSidecar:
    def __init__(
        self,
        loc_ckpt: str = LOC_CKPT,
        pose_ckpt: str = POSE_CKPT,
        text_cache: str = TEXT_CACHE,
        encoder_path: str = DEFAULT_MIX87_ENCODER_PATH,
        device: str | torch.device = "cuda:0",
        loc_size: int | None = None,
        pose_crop: int | None = None,
        encoder: FrozenOmniVisionEncoder | None = None,
    ):
        self.device = torch.device(device)
        loc = torch.load(loc_ckpt, map_location="cpu", weights_only=False)
        pose = torch.load(pose_ckpt, map_location="cpu", weights_only=False)
        la, pa = loc.get("args") or {}, pose.get("args") or {}
        self.loc_taps = loc.get("taps") or la["taps"]
        self.pose_taps = pose.get("taps") or pa["taps"]
        self.loc_size = int(loc_size or la.get("image_size", 512))
        self.pose_crop = int(pose_crop or pa.get("crop", 256))
        self.enc_path = la.get("encoder_path") or encoder_path

        if encoder is not None:
            self.encoder = encoder
        else:
            self.encoder = FrozenOmniVisionEncoder(self.enc_path, dtype=torch.bfloat16)
            self.encoder.eval().to(self.device)
            for p in self.encoder.parameters():
                p.requires_grad = False

        self.loc_version = "v2" if loc.get("fpn_kind") == "vitdet_all" else "v1"
        if self.loc_version == "v2":
            self.loc_size = int(loc_size or loc.get("image_size", 1024))
        self.loc = OmniYoloWorldDetector(
            feature_dim=self.encoder.d_model,
            d_model=int(la.get("d_model", 256)),
            num_streams=len(self.loc_taps),
            text_dim=int(loc.get("text_dim") or 2048),
            reg_max=int(la.get("reg_max", 16)),
            use_p2=True if self.loc_version == "v2" else not la.get("no_p2", False),
            drop_p5=False if self.loc_version == "v2" else not la.get("keep_p5", False),
            fpn_kind=str(loc.get("fpn_kind") or la.get("fpn") or "vitdet"),
        ).to(self.device)
        self.loc.load_state_dict(loc["model"])
        self.loc.eval()

        self.pose_version = "v2" if pose.get("head") == "pose_v2_heatmap" else "v1"
        if self.pose_version == "v2":
            self.pose_hw = tuple(pose["input_hw"])
            self.pose = OmniPoseV2(
                self.encoder.d_model, int(pa["d_model"]), len(self.pose_taps), int(pa["num_blocks"]),
                int(pa["heads"]), tuple(pose["grid_hw"]), num_sources=len(pose.get("sources", ["coco"])),
            ).to(self.device)
        else:
            self.pose = OmniVitPoseHeatmap(
                feature_dim=self.encoder.d_model,
                d_model=int(pa.get("d_model", 256)),
                num_streams=len(self.pose_taps),
                num_deconv=int(pa.get("num_deconv", 2)),
            ).to(self.device)
        self.pose.load_state_dict(pose["model"])
        self.pose.eval()

        cache = torch.load(text_cache, map_location="cpu", weights_only=False)
        vocab = cache.get("vocab") or []
        person_i = vocab.index("person") if "person" in vocab else 0
        self.person_text = F.normalize(cache["eval_text"].float()[person_i], dim=-1).to(self.device)

    def _encode(self, images: Sequence[Image.Image], taps, size: int):
        self.encoder.set_image_pixel_budget(size * size, size * size)
        feats, grid = self.encoder.encode_images_spatial(list(images), taps, self.device)
        return feats, int(grid[0, 1]), int(grid[0, 2])

    @torch.no_grad()
    def detect_people(self, image: Image.Image, score_thresh: float = 0.25, max_people: int = 5):
        if self.loc_version == "v2":
            return self._detect_people_v2(image, score_thresh, max_people)
        vis = image.convert("RGB").resize((self.loc_size, self.loc_size), Image.BILINEAR)
        feats, gh, gw = self._encode([vis], self.loc_taps, self.loc_size)
        outs = self.loc(feats, (gh, gw), self.person_text.unsqueeze(0))
        pb, ps, _pc = decode_yolo_v8(outs, self.loc_size, score_thresh=score_thresh)
        ow, oh = image.size
        people = []
        for box, sc in zip(pb, ps):
            x1, y1, x2, y2 = box.tolist()
            people.append({
                "score": float(sc),
                "box_xyxy_norm": [x1, y1, x2, y2],
                "box_xyxy_px": [x1 * ow, y1 * oh, x2 * ow, y2 * oh],
            })
            if len(people) >= max_people:
                break
        people.sort(key=lambda d: d["score"], reverse=True)
        return people

    @torch.no_grad()
    def _detect_people_v2(self, image: Image.Image, score_thresh: float, max_people: int):
        """Loc v2: letterbox (aspect kept), all 27 blocks, boxes mapped back to original pixels."""
        img = np.asarray(image.convert("RGB"))
        oh, ow = img.shape[:2]
        canvas, r = letterbox(img, self.loc_size)
        pix, grid = qwen_patchify(canvas)
        amp = self.device.type == "cuda"
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp):
            feats = self.encoder.encode_pixels(pix, grid[None], self.loc_taps, self.device,
                                               out_dtype=torch.bfloat16 if amp else torch.float32)
            outs = self.loc(feats, (int(grid[1]), int(grid[2])), self.person_text.unsqueeze(0))
        pb, ps, _pc = decode_yolo_v8(outs, self.loc_size, score_thresh=score_thresh, nms_iou=0.5)
        people = []
        for box, sc in zip((pb.float() * self.loc_size / r).tolist(), ps.tolist()):
            x1, y1, x2, y2 = min(max(box[0], 0.0), ow), min(max(box[1], 0.0), oh), min(max(box[2], 0.0), ow), min(max(box[3], 0.0), oh)
            people.append({"score": float(sc), "box_xyxy_norm": [x1 / ow, y1 / oh, x2 / ow, y2 / oh],
                           "box_xyxy_px": [x1, y1, x2, y2]})
        people.sort(key=lambda d: d["score"], reverse=True)
        return people[:max_people]

    @torch.no_grad()
    def _pose_v2(self, image: Image.Image, box_xyxy_px, flip_test: bool = True):
        """Pose v2: UDP-warped 384x288 crop, flip test, DARK decoding -> (17, 2) px, (17,) conf, M."""
        img = np.asarray(image.convert("RGB"))
        oh_c, ow_c = self.pose_hw
        x1, y1, x2, y2 = [float(v) for v in box_xyxy_px]
        c, s = box_to_center_scale(x1, y1, max(x2 - x1, 1.0), max(y2 - y1, 1.0), ow_c / oh_c)
        M = warp_matrix(c, s, 0.0, self.pose_hw)
        crop = cv2.warpAffine(img, M, (ow_c, oh_c), flags=cv2.INTER_LINEAR)
        views = [crop] + ([np.ascontiguousarray(crop[:, ::-1])] if flip_test else [])
        pix, grids = zip(*(qwen_patchify(v) for v in views))
        amp = self.device.type == "cuda"
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp):
            feats = self.encoder.encode_pixels(torch.cat(pix, 0), torch.stack(grids, 0), self.pose_taps, self.device,
                                               out_dtype=torch.bfloat16 if amp else torch.float32)
            hm = self.pose(feats).float()
        if flip_test:
            hm = 0.5 * (hm[:1] + flip_heatmaps(hm[1:], COCO_KPT_FLIP))
        xy, conf = dark_decode(hm)
        xy = heatmap_to_input(xy, self.pose_hw, hm.shape[-2:])[0]
        Ai = np.linalg.inv(M[:, :2])
        xy = (xy - M[:, 2]) @ Ai.T
        return xy, conf[0], M, (float(c[0] - s[0] / 2), float(c[1] - s[1] / 2), float(s[1]))

    @torch.no_grad()
    def pose_person(self, image: Image.Image, box_xyxy_px, conf_thresh: float = 0.2):
        ow, oh = image.size
        if self.pose_version == "v2":
            xy, conf, _, crop_xy = self._pose_v2(image, box_xyxy_px)
            kpts = [{"name": n, "x": float(x), "y": float(y), "x_norm": float(x) / ow, "y_norm": float(y) / oh,
                     "conf": float(c), "visible": float(c) >= conf_thresh}
                    for n, (x, y), c in zip(KPT_NAMES, xy, conf)]
            cf = [float(c) for c in conf]
            return {"crop_xy": list(crop_xy), "keypoints": kpts, "xy_px": [(float(x), float(y)) for x, y in xy],
                    "conf": cf, "mean_conf": float(sum(cf) / max(len(cf), 1))}
        x1, y1, x2, y2 = [float(v) for v in box_xyxy_px]
        bw, bh = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
        cx1, cy1, side = _square_box(x1, y1, bw, bh, ow, oh, pad=PAD)
        crop = image.crop((int(cx1), int(cy1), int(cx1 + side), int(cy1 + side)))
        crop = crop.resize((self.pose_crop, self.pose_crop), Image.BILINEAR)
        feats, gh, gw = self._encode([crop], self.pose_taps, self.pose_crop)
        hm = self.pose(feats, (gh, gw))
        xy, conf = decode_heatmap(hm.float())
        hh, ww = hm.shape[-2], hm.shape[-1]
        kpts = []
        xy_px, cf = [], []
        for j, name in enumerate(KPT_NAMES):
            px = float(xy[0, j, 0]) * (side / ww) + cx1
            py = float(xy[0, j, 1]) * (side / hh) + cy1
            c = float(conf[0, j])
            xy_px.append((px, py))
            cf.append(c)
            kpts.append({
                "name": name,
                "x": px,
                "y": py,
                "x_norm": px / ow,
                "y_norm": py / oh,
                "conf": c,
                "visible": c >= conf_thresh,
            })
        return {
            "crop_xy": [cx1, cy1, side],
            "keypoints": kpts,
            "xy_px": xy_px,
            "conf": cf,
            "mean_conf": float(sum(cf) / max(len(cf), 1)),
        }

    @torch.no_grad()
    def analyze(
        self,
        image: Image.Image,
        score_thresh: float = 0.25,
        kpt_thresh: float = 0.2,
        max_people: int = 5,
    ) -> dict:
        image = image.convert("RGB")
        people = self.detect_people(image, score_thresh=score_thresh, max_people=max_people)
        overlay = image.copy()
        records = []
        for i, p in enumerate(people):
            pose = self.pose_person(image, p["box_xyxy_px"], conf_thresh=kpt_thresh)
            cues = pose_cues(pose["keypoints"], p["box_xyxy_px"])
            rec = {
                "id": i,
                "det_score": p["score"],
                "box_xyxy_px": p["box_xyxy_px"],
                "box_xyxy_norm": p["box_xyxy_norm"],
                "pose_mean_conf": pose["mean_conf"],
                "cues": cues,
                "keypoints": pose["keypoints"],
            }
            records.append(rec)
            vis = [c if c >= kpt_thresh else 0.0 for c in pose["conf"]]
            draw_skeleton(overlay, pose["xy_px"], vis, thresh=kpt_thresh)
        text = self.to_tool_text(records, image.size)
        return {
            "n_people": len(records),
            "people": records,
            "omni_text": text,
            "overlay": overlay,
        }

    @staticmethod
    def to_tool_text(people: list[dict], size) -> str:
        w, h = size
        lines = [f"[HUMAN_POSE] image {w}x{h}px  people={len(people)}"]
        if not people:
            lines.append("No person detected.")
            return "\n".join(lines)
        for p in people:
            x1, y1, x2, y2 = p["box_xyxy_px"]
            cue = (", ".join(p["cues"])) if p["cues"] else "pose uncertain"
            lines.append(
                f"person {p['id']}  det={p['det_score']:.2f}  pose={p['pose_mean_conf']:.2f}  "
                f"box=[{x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f}]  {cue}"
            )
            pts = []
            for k in p["keypoints"]:
                if not k["visible"]:
                    continue
                pts.append(f"{k['name']}=({k['x_norm']:.2f},{k['y_norm']:.2f},{k['conf']:.2f})")
            if pts:
                lines.append("  " + " ".join(pts))
        lines.append(
            "Use boxes and coarse cues. Joints are approximate; ignore low-conf or crowded/overhead poses."
        )
        return "\n".join(lines)

    def to_omni_messages(self, image: Image.Image, result: dict, question: str) -> list[dict]:
        """Chat content for Qwen3-Omni: original + skeleton overlay + tool text."""
        return [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image.convert("RGB")},
                    {"type": "image", "image": result["overlay"]},
                    {
                        "type": "text",
                        "text": result["omni_text"] + "\n\n" + question.strip(),
                    },
                ],
            }
        ]
