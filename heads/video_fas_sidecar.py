"""Video face-attack sidecar: physical spoof and deepfake scores for a face video.

Weights: ``checkpoints/video_fas/best.pt``, written by ship_video_fas_v2.py. The
checkpoint names its head (token-level MHFA or a pooled head, see
model_video_fas_v2.py), the encoder blocks it reads, the crop recipe, and
decision thresholds calibrated on validation. Report: docs/video_fas_report.md.

Pipeline, the same one the head was trained and tested on:

    video --decode at ``fps``--> RGB frames
          --loc v2 "face" query (letterbox 256..512, see fas_face_detect)--> best face box per frame
          --square crop, side 1.5 x max(w, h), 256x256--> face crops
          --frozen mix87 vision tower (bf16)--> block outputs
          --head--> per-frame spoof / deepfake / joint scores (logit differences)
          --mean of the 3 highest frames--> video scores and decisions

    side = VideoFASSidecar(device="cuda:0")          # or encoder=<shared tower>
    out = side.score_video("clip.mp4")
    out["video"]      # {"spoof": .., "deepfake": .., "joint": ..}
    out["decision"]   # {"spoof": bool, "deepfake": bool, "attack": bool}
    out["frames"]     # per sampled frame: time, box, scores

The stored thresholds were set on validation frames, with one deepfake
threshold for all datasets. Applied to top-3 video scores they over-flag some
domains (69% of real FF++ videos end to end), so set the operating point on
target-domain video before relying on ``decision`` (report section 7.5).
"""
from __future__ import annotations

import os
from typing import Sequence

import cv2
import numpy as np
import torch
import torch.nn as nn

from extract_video_fas_v2 import patchify_gpu, square_crop
from fas_face_detect import ENCODER_PATH, LocV2FaceDetector, encode_taps
from model_fsfm_fas import MultiLayerFSFMHead
from model_video_fas import VideoFASHead
from model_video_fas_v2 import MultiLayerMLPHead, TokenFASHead

REPO = os.path.dirname(os.path.abspath(__file__))
CKPT = os.path.join(REPO, "checkpoints", "video_fas", "best.pt")


def build_head(blob: dict) -> nn.Module:
    head, taps = blob["head"], blob["taps"]
    if head == "mhfa_tokens":
        return TokenFASHead(num_streams=len(taps))
    if head == "mlp":
        return MultiLayerMLPHead(1152, num_streams=len(taps))
    if head == "fsfm":
        return MultiLayerFSFMHead(1152, num_streams=len(taps))
    if head == "he":
        return VideoFASHead(1152, num_streams=len(taps))
    raise ValueError(f"unknown head {head!r}")


class VideoFASSidecar(nn.Module):
    def __init__(self, ckpt: str = CKPT, device: str | torch.device = "cuda:0", encoder=None,
                 encoder_path: str = ENCODER_PATH, detector: LocV2FaceDetector | None = None):
        super().__init__()
        self.device = torch.device(device)
        blob = torch.load(ckpt, map_location="cpu", weights_only=False)
        self.meta = {k: blob[k] for k in blob if k != "model"}
        self.kind, self.taps = blob["head"], list(blob["taps"])
        self.size = int(blob.get("crop_size", 256))
        self.margin = float(blob.get("crop_margin", 1.5))
        self.top_k = int(blob.get("video_top_k", 3))
        self.thresholds = blob.get("thresholds", {})
        self.head = build_head(blob)
        self.head.load_state_dict(blob["model"])
        self.head.to(self.device).eval()
        for p in self.head.parameters():
            p.requires_grad = False
        if encoder is None:
            from omni_vision_encoder import FrozenOmniVisionEncoder

            encoder = FrozenOmniVisionEncoder(encoder_path, dtype=torch.bfloat16).to(self.device).eval()
        self.encoder = encoder
        self.detector = detector or LocV2FaceDetector(self.device, size="auto", encoder=encoder)

    # ------------------------------------------------------------------ frames

    @torch.no_grad()
    def score_crops(self, crops: np.ndarray, batch: int = 128) -> dict[str, np.ndarray]:
        """crops: (N, 256, 256, 3) uint8 RGB face crops -> per-frame scores, higher = attack."""
        out = {"spoof": [], "deepfake": [], "joint": []}
        for i in range(0, len(crops), batch):
            x = torch.from_numpy(np.ascontiguousarray(crops[i:i + batch])).to(self.device)
            pix, grid = patchify_gpu(x)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=self.device.type == "cuda"):
                feats = encode_taps(self.encoder, pix.to(torch.bfloat16), grid, self.taps)
            if self.kind == "mhfa_tokens":
                sc = self.head.scores(feats)
            else:
                pooled = feats.mean(dim=1, dtype=torch.float32)[:, None, None]  # (B, 1, 1, streams, D)
                sc = self.head.clip_scores(pooled) if hasattr(self.head, "clip_scores") else \
                    self.head.scores(self.head.embed(pooled))
            for k in out:
                out[k].append(sc[k].float().cpu().numpy())
        return {k: np.concatenate(v) if v else np.zeros(0, np.float32) for k, v in out.items()}

    def crop_faces(self, frames: Sequence[np.ndarray]) -> tuple[np.ndarray, list]:
        """RGB frames -> face crops and boxes. A frame without a face reuses the nearest frame's box."""
        boxes = []
        for i in range(0, len(frames), 16):
            boxes += self.detector.detect(list(frames[i:i + 16]))
        have = [i for i, b in enumerate(boxes) if b is not None]
        crops, used = [], []
        for i, frame in enumerate(frames):
            if have:
                box = boxes[min(have, key=lambda k: abs(k - i))]
                crops.append(square_crop(frame, box[:4], self.margin, self.size))
                used.append([round(float(v), 1) for v in box[:4]] + [round(float(box[4]), 3)])
            else:
                h, w = frame.shape[:2]
                s = min(h, w)
                crops.append(square_crop(frame, [(w - s) / 2, (h - s) / 2, (w + s) / 2, (h + s) / 2], 1.0, self.size))
                used.append(None)
        return np.stack(crops), used

    # ------------------------------------------------------------------ media

    def pool(self, values: np.ndarray) -> float:
        return float(np.sort(values)[-self.top_k:].mean())

    def decide(self, video: dict[str, float]) -> dict[str, bool]:
        thr = {k: self.thresholds.get(k, {}).get("eer", 0.0) for k in ("spoof", "deepfake", "joint")}
        return {"spoof": video["spoof"] >= thr["spoof"], "deepfake": video["deepfake"] >= thr["deepfake"],
                "attack": video["joint"] >= thr["joint"]}

    def score_frames(self, frames: Sequence[np.ndarray], times: Sequence[float] | None = None) -> dict:
        crops, boxes = self.crop_faces(frames)
        sc = self.score_crops(crops)
        video = {k: self.pool(v) for k, v in sc.items()}
        times = list(times) if times is not None else list(range(len(frames)))
        per = [{"t": round(float(t), 3), "box": b, **{k: round(float(sc[k][i]), 4) for k in sc}}
               for i, (t, b) in enumerate(zip(times, boxes))]
        return {"video": video, "decision": self.decide(video), "frames": per}

    def score_image(self, rgb: np.ndarray) -> dict:
        return self.score_frames([rgb], [0.0])

    def score_video(self, path: str, fps: float = 4.0, max_frames: int = 256) -> dict:
        """Uniform frames at ``fps`` (at most ``max_frames``), decoded sequentially so indexes are exact."""
        cap = cv2.VideoCapture(path)
        native = cap.get(cv2.CAP_PROP_FPS) or 25.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        step = max(1, int(round(native / fps)))
        wanted = list(range(step // 2, max(count, 1), step))
        if len(wanted) > max_frames:
            wanted = [wanted[int(i)] for i in np.linspace(0, len(wanted) - 1, max_frames)]
        want, frames, times, index = set(wanted), [], [], 0
        while wanted and index <= wanted[-1]:
            if not cap.grab():
                break
            if index in want:
                ok, bgr = cap.retrieve()
                if ok:
                    frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                    times.append(index / native)
            index += 1
        cap.release()
        if not frames:
            raise RuntimeError(f"no frames decoded from {path}")
        return self.score_frames(frames, times)
