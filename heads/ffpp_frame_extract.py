"""Torch-free FaceForensics++ frame extraction (safe for process pools)."""

from __future__ import annotations

from pathlib import Path
from typing import List


def extract_uniform_video_frames(
    video_path: str,
    out_dir: str,
    n_frames: int = 16,
    max_side: int = 256,
) -> List[str]:
    """Write up to n_frames JPEGs uniformly sampled from a video. Skip if already extracted."""
    import cv2

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    existing = sorted(p for p in out.glob("*.jpg") if p.is_file())
    if len(existing) >= n_frames:
        return [str(p) for p in existing[:n_frames]]
    for p in existing:
        p.unlink()

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frames = []
    if total <= 0:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
        cap.release()
        if not frames:
            return []
        n = min(n_frames, len(frames))
        idxs = [0] if n == 1 else [int(round(i * (len(frames) - 1) / (n - 1))) for i in range(n)]
        picked = [frames[i] for i in idxs]
    else:
        n = min(n_frames, total)
        idxs = [0] if n == 1 else [int(round(i * (total - 1) / (n - 1))) for i in range(n)]
        picked = []
        for idx in idxs:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if ok:
                picked.append(frame)
        cap.release()

    saved = []
    for k, frame in enumerate(picked):
        path = out / f"{k:04d}.jpg"
        h, w = frame.shape[:2]
        if max_side > 0 and max(h, w) > max_side:
            scale = max_side / float(max(h, w))
            frame = cv2.resize(
                frame,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        if cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92]):
            saved.append(str(path))
    return saved
