"""Decode YOLOv8-style DFL predictions to COCO boxes (xyxy-norm)."""

from __future__ import annotations

import torch
from torchvision.ops import batched_nms


def decode_yolo_v8(
    outputs: dict,
    image_size: int,
    score_thresh: float = 0.01,
    nms_iou: float = 0.7,
    max_dets: int = 300,
    batch_index: int = 0,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """One image: xyxy-norm boxes, scores, class ids."""
    scores = outputs["pred_scores"][batch_index].float().sigmoid()
    boxes = outputs["pred_bboxes"][batch_index].float() / float(image_size)
    boxes = boxes.clamp(0, 1)
    best, cls = scores.max(-1)
    keep = best > score_thresh
    if not keep.any():
        empty = boxes.new_zeros((0, 4))
        return empty, boxes.new_zeros((0,)), boxes.new_zeros((0,), dtype=torch.long)
    boxes, best, cls = boxes[keep], best[keep], cls[keep]
    order = best.argsort(descending=True)
    if order.numel() > 3000:
        order = order[:3000]
    boxes, best, cls = boxes[order], best[order], cls[order]
    keep_n = batched_nms(boxes, best, cls, nms_iou)
    if keep_n.numel() > max_dets:
        keep_n = keep_n[:max_dets]
    return boxes[keep_n], best[keep_n], cls[keep_n]
