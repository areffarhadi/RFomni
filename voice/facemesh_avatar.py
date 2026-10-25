"""3D MediaPipe-style face-mesh Bot Video driven by TTS audio blendshapes.

Renders the canonical 468-point face mesh with jaw/lip morphs from speech energy.
No Wav2Lip / paid avatar APIs — numpy + OpenCV only.
"""

from __future__ import annotations

import math
import os
import struct
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    OutputImageRawFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# MediaPipe Face Mesh lip / eye landmark indices (0-based).
_LOWER_FACE = np.array(
    [
        61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 308, 324, 318, 402,
        317, 14, 87, 178, 88, 95, 78, 191, 80, 81, 82, 13, 312, 311, 310, 415,
        308, 324, 318, 402, 317, 14, 87, 178, 88, 95, 185, 40, 39, 37, 0, 267,
        269, 270, 409, 291, 375, 321, 405, 314, 17, 84, 181, 91, 146, 61, 57, 43,
        106, 182, 83, 18, 313, 406, 335, 273, 287, 432, 422, 424, 418, 421, 200,
        199, 175, 152, 377, 400, 378, 379, 365, 397, 288, 361, 323, 454, 356,
        389, 251, 284, 332, 297, 338, 10, 109, 67, 103, 54, 21, 162, 127, 234,
        93, 132, 58, 172, 136, 150, 149, 176, 148, 152, 377, 400, 378, 379, 365,
        397, 288, 435, 410, 454, 323, 361, 340, 346, 347, 348, 349, 350, 451,
        452, 453, 464, 435, 410, 287, 273, 335, 406, 313, 18, 83, 182, 106, 43,
        57, 186, 92, 165, 167, 164, 393, 391, 322, 410, 287,
    ],
    dtype=np.int32,
)
_UPPER_LIP = np.array(
    [61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191, 78],
    dtype=np.int32,
)
_LOWER_LIP = np.array(
    [146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 308, 324, 318, 402, 317, 14, 87, 178, 88, 95],
    dtype=np.int32,
)
_LEFT_EYE = np.array([33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246], dtype=np.int32)
_RIGHT_EYE = np.array([362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398], dtype=np.int32)

_ASSET = Path(__file__).resolve().parent / "assets" / "canonical_face_model.obj"


def _load_obj(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("v "):
                parts = line.split()
                verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif line.startswith("f "):
                idxs = []
                for tok in line.split()[1:]:
                    idxs.append(int(tok.split("/")[0]) - 1)
                if len(idxs) >= 3:
                    # triangulate fan
                    for i in range(1, len(idxs) - 1):
                        faces.append([idxs[0], idxs[i], idxs[i + 1]])
    v = np.asarray(verts, dtype=np.float32)
    # Center + normalize to unit-ish scale
    v -= v.mean(axis=0)
    scale = 1.35 / (np.abs(v).max() + 1e-6)
    v *= scale
    return v, np.asarray(faces, dtype=np.int32)


class FaceMeshRenderer:
    """Shaded MediaPipe canonical face with audio-driven blendshapes."""

    def __init__(self, width: int = 640, height: int = 640):
        self.w = width
        self.h = height
        if not _ASSET.is_file():
            raise FileNotFoundError(f"Missing face mesh asset: {_ASSET}")
        self.base, self.faces = _load_obj(_ASSET)
        self._unique_lower = np.unique(_LOWER_FACE)
        self._unique_upper_lip = np.unique(_UPPER_LIP)
        self._unique_lower_lip = np.unique(_LOWER_LIP)
        self._left_eye = np.unique(_LEFT_EYE)
        self._right_eye = np.unique(_RIGHT_EYE)
        # Precompute rest mouth opening reference (lower vs upper lip Y)
        self._mouth_y0 = float(self.base[self._unique_lower_lip, 1].mean())
        self._chin_idxs = np.where(self.base[:, 1] < self._mouth_y0 - 0.05)[0]

    def morph(
        self,
        jaw_open: float,
        smile: float,
        blink_l: float,
        blink_r: float,
    ) -> np.ndarray:
        v = self.base.copy()
        jaw = float(np.clip(jaw_open, 0.0, 1.0))
        smile = float(np.clip(smile, 0.0, 1.0))
        # Jaw / lower face drop + slight back
        if jaw > 1e-4 and self._chin_idxs.size:
            drop = jaw * 0.22
            v[self._chin_idxs, 1] -= drop
            v[self._chin_idxs, 2] -= jaw * 0.06
        # Lower lip extra open
        if jaw > 1e-4:
            v[self._unique_lower_lip, 1] -= jaw * 0.12
            v[self._unique_upper_lip, 1] += jaw * 0.02
        # Smile: corners outward + up
        if smile > 1e-4:
            # approx lip corners 61 (left) and 291 (right)
            for idx, sign in ((61, -1.0), (291, 1.0)):
                if idx < len(v):
                    v[idx, 0] += sign * smile * 0.08
                    v[idx, 1] += smile * 0.04
        # Blink: squash eye verts toward eye center
        for eye_idx, amount in ((self._left_eye, blink_l), (self._right_eye, blink_r)):
            a = float(np.clip(amount, 0.0, 1.0))
            if a < 1e-4 or eye_idx.size == 0:
                continue
            c = v[eye_idx].mean(axis=0)
            v[eye_idx] = c + (v[eye_idx] - c) * np.array([1.0, 1.0 - 0.85 * a, 1.0], dtype=np.float32)
        return v

    def render(
        self,
        jaw_open: float = 0.0,
        smile: float = 0.05,
        blink_l: float = 0.0,
        blink_r: float = 0.0,
        yaw: float = 0.0,
        pitch: float = 0.0,
        show_wire: bool = True,
    ) -> bytes:
        v = self.morph(jaw_open, smile, blink_l, blink_r)
        # Rotate
        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        # yaw around Y, pitch around X
        x, y, z = v[:, 0], v[:, 1], v[:, 2]
        xz = cy * x + sy * z
        zz = -sy * x + cy * z
        yy = cp * y - sp * zz
        zz = sp * y + cp * zz
        v_r = np.stack([xz, yy, zz], axis=1)

        # Weak perspective
        f = 0.92 * min(self.w, self.h)
        z_cam = v_r[:, 2] + 3.2
        px = (v_r[:, 0] / z_cam) * f + self.w * 0.5
        py = (-v_r[:, 1] / z_cam) * f + self.h * 0.52
        pts2 = np.stack([px, py], axis=1)

        # Triangle depths + normals for lighting
        tri = self.faces
        v0, v1, v2 = v_r[tri[:, 0]], v_r[tri[:, 1]], v_r[tri[:, 2]]
        n = np.cross(v1 - v0, v2 - v0)
        n_len = np.linalg.norm(n, axis=1, keepdims=True) + 1e-8
        n = n / n_len
        light = np.array([0.35, 0.55, 0.75], dtype=np.float32)
        light /= np.linalg.norm(light)
        ndot = np.clip(n @ light, 0.0, 1.0)
        depth = (v0[:, 2] + v1[:, 2] + v2[:, 2]) / 3.0
        order = np.argsort(depth)  # far → near

        # Background
        img = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        yy = np.linspace(18, 48, self.h, dtype=np.float32)[:, None]
        img[:, :, 0] = (yy * 0.9).astype(np.uint8)
        img[:, :, 1] = (yy * 1.4).astype(np.uint8)
        img[:, :, 2] = (yy * 1.8).astype(np.uint8)

        skin = np.array([210, 175, 155], dtype=np.float32)  # BGR-ish via RGB write then...
        # We'll draw in RGB
        skin_rgb = np.array([210.0, 178.0, 155.0], dtype=np.float32)
        ambient = 0.28

        for ti in order:
            i0, i1, i2 = tri[ti]
            poly = np.array(
                [[pts2[i0, 0], pts2[i0, 1]], [pts2[i1, 0], pts2[i1, 1]], [pts2[i2, 0], pts2[i2, 1]]],
                dtype=np.int32,
            )
            shade = ambient + 0.72 * float(ndot[ti])
            color = np.clip(skin_rgb * shade, 0, 255).astype(np.uint8)
            cv2.fillConvexPoly(img, poly, color.tolist(), lineType=cv2.LINE_AA)

        if show_wire:
            # Contour-style edges only (unique undirected edges, subsampled)
            edges = set()
            for a, b, c in tri:
                for u, v in ((a, b), (b, c), (c, a)):
                    if u > v:
                        u, v = v, u
                    edges.add((u, v))
            edge_col = (70, 140, 155)
            for i, (u, v) in enumerate(edges):
                if i % 3 != 0:
                    continue
                p0 = (int(pts2[u, 0]), int(pts2[u, 1]))
                p1 = (int(pts2[v, 0]), int(pts2[v, 1]))
                cv2.line(img, p0, p1, edge_col, 1, cv2.LINE_AA)

        # Iris / pupil markers so the mesh reads as a face, not a blank mask
        for eye_idx in (self._left_eye, self._right_eye):
            c = pts2[eye_idx].mean(axis=0)
            cv2.circle(img, (int(c[0]), int(c[1])), 7, (40, 55, 70), -1, cv2.LINE_AA)
            cv2.circle(img, (int(c[0]), int(c[1])), 3, (220, 230, 240), -1, cv2.LINE_AA)

        return img.tobytes()


class FaceMeshAvatarService(FrameProcessor):
    """Bot Video: 3D face mesh animated from TTSAudioRawFrame energy."""

    def __init__(
        self,
        *,
        width: int = 640,
        height: int = 640,
        fps: int = 15,
        show_wire: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._w = width
        self._h = height
        self._fps = fps
        self._show_wire = show_wire
        self._renderer = FaceMeshRenderer(width, height)
        self._speaking = False
        self._level = 0.0
        self._smile = 0.08
        self._t0 = time.time()
        self._audio_chunks = 0
        self._blink_until = 0.0
        self._idle = self._renderer.render(0.0, self._smile, 0.0, 0.0, 0.0, 0.0, show_wire)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            await self.push_frame(
                OutputImageRawFrame(image=self._idle, size=(self._w, self._h), format="RGB")
            )
            return

        if isinstance(frame, (EndFrame, CancelFrame, InterruptionFrame)):
            self._speaking = False
            self._level = 0.0
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSStartedFrame):
            self._speaking = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSStoppedFrame):
            self._speaking = False
            self._level *= 0.3
            await self.push_frame(
                OutputImageRawFrame(
                    image=self._frame(0.05),
                    size=(self._w, self._h),
                    format="RGB",
                )
            )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSAudioRawFrame):
            rms = self._rms(frame.audio)
            self._level = 0.65 * self._level + 0.35 * rms
            self._audio_chunks += 1
            # Emit ~fps while speaking
            every = max(1, int(16000 / (320 * self._fps)))
            if self._speaking and (self._audio_chunks % every == 0):
                await self.push_frame(
                    OutputImageRawFrame(
                        image=self._frame(self._level),
                        size=(self._w, self._h),
                        format="RGB",
                    )
                )
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    def _frame(self, level: float) -> bytes:
        now = time.time()
        t = now - self._t0
        # Occasional blink
        if now > self._blink_until and (int(t * 2) % 17 == 0):
            self._blink_until = now + 0.12
        blink = 1.0 if now < self._blink_until else 0.0
        jaw = min(1.0, level * 1.35)
        smile = self._smile + (0.06 if self._speaking else 0.0)
        yaw = 0.12 * math.sin(t * 0.7)
        pitch = 0.05 * math.sin(t * 0.9 + 1.2)
        return self._renderer.render(jaw, smile, blink, blink, yaw, pitch, self._show_wire)

    @staticmethod
    def _rms(pcm: bytes) -> float:
        if len(pcm) < 4:
            return 0.0
        n = len(pcm) // 2
        samples = struct.unpack(f"<{n}h", pcm[: n * 2])
        if not samples:
            return 0.0
        mean = sum(s * s for s in samples) / len(samples)
        return min(1.0, math.sqrt(mean) / 7000.0)
