"""Draw COCO-17 skeletons and turn keypoints into short Omni cues."""

from __future__ import annotations

from PIL import Image, ImageDraw

KPT_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]
SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]
LEFT = {1, 3, 5, 7, 9, 11, 13, 15}
RIGHT = {2, 4, 6, 8, 10, 12, 14, 16}


def _col(j: int):
    if j in LEFT:
        return (40, 180, 255)
    if j in RIGHT:
        return (255, 140, 40)
    return (80, 220, 80)


def draw_skeleton(img: Image.Image, xy_px, conf, thresh: float = 0.2, width: int = 3) -> Image.Image:
    dr = ImageDraw.Draw(img)
    for a, b in SKELETON:
        if conf[a] < thresh or conf[b] < thresh:
            continue
        dr.line([tuple(xy_px[a]), tuple(xy_px[b])], fill=_col(a), width=width)
    r = max(3, width + 1)
    for j, (x, y) in enumerate(xy_px):
        if conf[j] < thresh:
            continue
        dr.ellipse([x - r, y - r, x + r, y + r], fill=_col(j), outline=(0, 0, 0))
    return img


def pose_cues(kpts: list[dict], box_xyxy) -> list[str]:
    """Coarse activity phrases. Only fire when the supporting joints are confident."""
    by = {k["name"]: k for k in kpts}
    x1, y1, x2, y2 = box_xyxy
    h = max(y2 - y1, 1.0)
    cues = []

    def ok(name, t=0.25):
        p = by.get(name)
        return p is not None and p["conf"] >= t

    def y(name):
        return by[name]["y"]

    def x(name):
        return by[name]["x"]

    if ok("left_wrist") and ok("left_shoulder") and y("left_wrist") < y("left_shoulder") - 0.08 * h:
        cues.append("left arm raised")
    if ok("right_wrist") and ok("right_shoulder") and y("right_wrist") < y("right_shoulder") - 0.08 * h:
        cues.append("right arm raised")
    if ok("left_hip") and ok("left_ankle"):
        if (y("left_ankle") - y("left_hip")) < 0.35 * h:
            cues.append("crouching or sitting")
        elif (y("left_ankle") - y("left_hip")) > 0.55 * h:
            cues.append("standing")
    if ok("left_ear") and ok("right_ear") and ok("nose"):
        if abs(x("left_ear") - x("right_ear")) < 0.08 * max(x2 - x1, 1.0):
            cues.append("facing sideways")
        else:
            cues.append("facing camera or away")
    return cues
