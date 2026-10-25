"""Face-centred crops for the spoof / deepfake head (train and eval share this).

FaceXBench hands the head very different framings: CASIA/MSU are 256 px face
crops, Celeb-DF are ~850x480 video frames, FF++ are whole 16:9 frames squashed
to 224x224 (faces ~20x45 px). A whole-frame head sees the FF++ face as ~3
patches, so every image is reduced to the same thing first: the most confident
face box, grown to a square of `scale` x its long side, resized to `out_size`.
Images where no face is found are used whole.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

LINDEVS_FACE_WEIGHTS = Path(__file__).resolve().parent / "exp/widerface_baseline/yolov8n-face-lindevs.pt"


def square_crop(image: Image.Image, box, scale: float = 1.5, out_size: int = 256) -> Image.Image:
    """Square crop centred on box (x1,y1,x2,y2), side = scale * long side, edge-padded."""
    x1, y1, x2, y2 = [float(v) for v in box]
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side = max(x2 - x1, y2 - y1) * scale
    w, h = image.size
    side = max(8.0, min(side, max(w, h) * 1.0))
    left, top = int(round(cx - side / 2)), int(round(cy - side / 2))
    s = int(round(side))
    # PIL pads out-of-bounds with black; replicate edges instead (screens/paper borders matter for FAS)
    arr = np.asarray(image.convert("RGB"))
    pad_l, pad_t = max(0, -left), max(0, -top)
    pad_r, pad_b = max(0, left + s - w), max(0, top + s - h)
    if pad_l or pad_t or pad_r or pad_b:
        arr = np.pad(arr, ((pad_t, pad_b), (pad_l, pad_r), (0, 0)), mode="edge")
        left, top = left + pad_l, top + pad_t
    crop = Image.fromarray(arr[top : top + s, left : left + s])
    return crop.resize((out_size, out_size), Image.Resampling.BICUBIC)


def pick_face(boxes: np.ndarray, confs: np.ndarray, image_size) -> int | None:
    """Most confident face, ties broken by area (the subject, not a background face)."""
    if len(boxes) == 0:
        return None
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    area = area / float(image_size[0] * image_size[1])
    return int(np.argmax(confs + 0.5 * np.sqrt(area)))


class FaceCropper:
    """yolov8n-face (lindevs, WIDER-trained) + square_crop."""

    def __init__(self, weights: str | Path = LINDEVS_FACE_WEIGHTS, device: str = "cuda:0", conf: float = 0.3):
        from ultralytics import YOLO

        self.model = YOLO(str(weights))
        self.device = device
        self.conf = conf

    def detect(self, images: Sequence[Image.Image], imgsz: int = 640) -> list[tuple[np.ndarray, np.ndarray]]:
        arrs = [np.asarray(im.convert("RGB"))[:, :, ::-1] for im in images]  # BGR for ultralytics
        res = self.model.predict(arrs, conf=self.conf, imgsz=imgsz, device=self.device, verbose=False)
        return [(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy()) for r in res]

    def best_boxes(self, images: Sequence[Image.Image], imgsz: int = 640) -> list[list[float] | None]:
        out = []
        for im, (b, c) in zip(images, self.detect(images, imgsz)):
            k = pick_face(b, c, im.size)
            out.append(None if k is None else [float(v) for v in b[k]])
        return out

    def crops(
        self, images: Sequence[Image.Image], scale: float = 1.5, out_size: int = 256
    ) -> tuple[list[Image.Image], list[list[float] | None]]:
        boxes = self.best_boxes(images)
        out = []
        for im, box in zip(images, boxes):
            if box is None:
                out.append(im.convert("RGB").resize((out_size, out_size), Image.Resampling.BICUBIC))
            else:
                out.append(square_crop(im, box, scale, out_size))
        return out, boxes
