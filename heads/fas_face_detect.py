"""Face boxes from the loc v2 sidecar head, for cropping video FAS inputs.

Loc v2 (checkpoints/loc_v2) is the YOLO-World style detector on the frozen
mix87 tower; its "face" query is the WIDER-trained face class. Taps are read
with forward hooks because this machine's transformers has no
``fast_pos_embed_interpolate``, which ``FrozenOmniVisionEncoder.encode_pixels``
needs.

    det = LocV2FaceDetector(device="cuda:0")            # size="auto"
    boxes = det.detect([rgb_frame, ...])   # per frame: (x1, y1, x2, y2, score) or None

With size="auto" the letterbox canvas is the frame's longer side rounded up to
a multiple of 32 and clamped to 256..512: a 224x224 VoxCeleb2 clip is detected
at 256 (3x faster than 512, median box IoU 0.94 against 512), broadcast video
and photos at 512. Frames in one call must share a size.
"""
from __future__ import annotations

import os
from typing import Sequence

import numpy as np
import torch
import torch.nn.functional as F

from omni_grounding.data.loc_v2 import letterbox, qwen_patchify
from omni_grounding.evaluation.decode_yolo_ultralytics import decode_yolo_v8
from omni_grounding.grounding.yolo_world import OmniYoloWorldDetector
from omni_vision_encoder import FrozenOmniVisionEncoder

REPO = os.path.dirname(os.path.abspath(__file__))
LOC_V2_CKPT = os.path.join(REPO, "checkpoints/loc_v2/best.pt")
TEXT_CACHE = os.path.join(REPO, "checkpoints/text/qwen_thinker_text_cache_body_caption.pt")
ENCODER_PATH = "/projects/capire/arfarh/mix87_ckpt54000-merged-MXFP4A16mse-fused"


def encode_taps(encoder: FrozenOmniVisionEncoder, pix: torch.Tensor, grid: torch.Tensor,
                taps: Sequence[int]) -> torch.Tensor:
    """Equal-size images only. Returns (B, tokens, len(taps), D) in the encoder dtype."""
    captured: dict[int, torch.Tensor] = {}
    hooks = []
    for index in set(taps):
        def _save(_m, _i, out, layer=index):
            captured[layer] = out[0] if isinstance(out, tuple) else out
        hooks.append(encoder.encoder.blocks[index].register_forward_hook(_save))
    try:
        with torch.no_grad():
            encoder.encoder(pix, grid)
    finally:
        for h in hooks:
            h.remove()
    batch = grid.shape[0]
    return torch.stack([captured[t].view(batch, -1, captured[t].shape[-1]) for t in taps], dim=2)


class LocV2FaceDetector:
    def __init__(self, device="cuda:0", size: int | str = "auto", encoder: FrozenOmniVisionEncoder | None = None,
                 encoder_path: str = ENCODER_PATH):
        self.device = torch.device(device)
        self.size = size
        loc = torch.load(LOC_V2_CKPT, map_location="cpu", weights_only=False)
        args = loc["args"]
        self.taps = list(loc["taps"])
        if encoder is None:
            encoder = FrozenOmniVisionEncoder(encoder_path, dtype=torch.bfloat16).to(self.device).eval()
        self.encoder = encoder
        self.model = OmniYoloWorldDetector(
            feature_dim=encoder.d_model,
            d_model=int(args.get("d_model", 256)),
            num_streams=len(self.taps),
            text_dim=int(loc.get("text_dim") or 2048),
            reg_max=int(args.get("reg_max", 16)),
            use_p2=True,
            drop_p5=False,
            fpn_kind=str(loc.get("fpn_kind")),
        ).to(self.device).eval()
        self.model.load_state_dict(loc["model"])
        cache = torch.load(TEXT_CACHE, map_location="cpu", weights_only=False)
        face = cache["vocab"].index("face")
        self.text = F.normalize(cache["eval_text"].float()[face], dim=-1).to(self.device)[None]

    @staticmethod
    def canvas(frame: np.ndarray) -> int:
        return int(min(512, max(256, 32 * int(np.ceil(max(frame.shape[:2]) / 32)))))

    @torch.no_grad()
    def detect(self, frames: Sequence[np.ndarray], score_thresh: float = 0.2):
        """frames: RGB uint8. Returns the best face per frame as (x1, y1, x2, y2, score) px, or None.

        The best face is the highest score times sqrt(area), so a large frontal
        face wins over a small, slightly more confident face in the background.
        """
        size = self.canvas(frames[0]) if self.size == "auto" else int(self.size)
        canvases, ratios = zip(*(letterbox(f, size) for f in frames))
        pix, grids = zip(*(qwen_patchify(c) for c in canvases))
        pix = torch.cat(pix).to(self.device, torch.bfloat16)
        grid = torch.stack(grids).to(self.device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            feats = encode_taps(self.encoder, pix, grid, self.taps)
            outs = self.model(feats, (int(grid[0, 1]), int(grid[0, 2])), self.text)
        result = []
        for i, (frame, r) in enumerate(zip(frames, ratios)):
            boxes, scores, _ = decode_yolo_v8(outs, size, score_thresh=score_thresh, nms_iou=0.5, batch_index=i)
            if len(boxes) == 0:
                result.append(None)
                continue
            h, w = frame.shape[:2]
            px = (boxes.float() * size / r).cpu().numpy()
            px[:, [0, 2]] = px[:, [0, 2]].clip(0, w)
            px[:, [1, 3]] = px[:, [1, 3]].clip(0, h)
            sc = scores.float().cpu().numpy()
            area = (px[:, 2] - px[:, 0]) * (px[:, 3] - px[:, 1])
            best = int(np.argmax(sc * np.sqrt(np.maximum(area, 1.0))))
            result.append((*px[best].tolist(), float(sc[best])))
        return result
