"""Diarization dataset over cached Qwen3-Omni encoder features.

Differs from ``recipes/diar_ssl_pruning/dataset.py`` in two ways:

* It reads memory-mapped encoder features instead of decoding audio, so an epoch costs
  a disk read rather than a 630M-parameter forward pass.
* Labels are placed on the encoder's true frame grid (13 frames per second at 80 ms
  spacing, resetting each second) rather than on an assumed uniform grid. Targets are
  produced at ``upsample`` times that rate so the head can predict boundaries finer
  than the encoder's 77 ms native resolution.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

import numpy as np
import torch
from torch.utils.data import Dataset

from omni_frontend import FRAMES_PER_SECOND, upsampled_frame_centres


def load_scp(path: str) -> Dict[str, str]:
    with open(path) as f:
        return dict(line.strip().split(None, 1) for line in f if line.strip())


def load_uem(path: str) -> Dict[str, List[float]]:
    out = {}
    with open(path) as f:
        for line in f:
            p = line.split()
            if p:
                out[p[0]] = [float(p[-2]), float(p[-1])]
    return out


def load_rttm(path: str) -> Dict[str, List[Tuple[float, float, str]]]:
    """recording -> list of (start, end, speaker)."""
    out: Dict[str, List[Tuple[float, float, str]]] = {}
    with open(path) as f:
        for line in f:
            p = line.split()
            if not p:
                continue
            rec, start, dur = p[1], float(p[3]), float(p[4])
            spk = p[-2] if p[-2] != "<NA>" else p[-3]
            out.setdefault(rec, []).append((start, start + dur, spk))
    return out


class CachedDiarizationDataset(Dataset):
    def __init__(
        self,
        feat_dir: str,
        scp_file: str,
        rttm_file: str,
        uem_file: str,
        chunk_size: int = 12,
        chunk_shift: int = 6,
        upsample: int = 4,
        max_speakers_per_chunk: int = 4,
    ):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.feat_dir = feat_dir
        self.chunk_size = chunk_size
        self.upsample = upsample
        self.max_speakers_per_chunk = max_speakers_per_chunk

        with open(os.path.join(feat_dir, "meta.json")) as f:
            self.meta = json.load(f)
        if self.meta["frames_per_second"] != FRAMES_PER_SECOND:
            raise ValueError("cached features have an unexpected frame rate")
        self.num_streams = len(self.meta["layers"])
        self.d_model = self.meta["d_model"]

        self.frames_per_chunk = chunk_size * FRAMES_PER_SECOND
        self.target_frames = self.frames_per_chunk * upsample
        # centres are identical for every chunk because chunks start on whole seconds
        self._centres = upsampled_frame_centres(self.frames_per_chunk, upsample)

        scp, uem = load_scp(scp_file), load_uem(uem_file)
        self.annotations = load_rttm(rttm_file)
        cached = self.meta["num_frames"]

        self.chunks: List[Tuple[str, int]] = []  # (recording, start second)
        skipped = 0
        for rec in scp:
            if rec not in uem or rec not in cached:
                skipped += 1
                continue
            start_sec, end_sec = uem[rec]
            avail = cached[rec] // FRAMES_PER_SECOND
            last = min(int(end_sec), avail)
            for st in range(int(start_sec) + 1, last - chunk_size + 1, chunk_shift):
                self.chunks.append((rec, st))
        if skipped:
            print(f"  {skipped} recordings in the scp had no cached features and were skipped")

        self._mmap: Dict[str, np.ndarray] = {}

    def _features(self, rec: str) -> np.ndarray:
        arr = self._mmap.get(rec)
        if arr is None:
            arr = np.load(os.path.join(self.feat_dir, f"{rec}.npy"), mmap_mode="r")
            self._mmap[rec] = arr
        return arr

    def __len__(self) -> int:
        return len(self.chunks)

    def __getitem__(self, idx: int):
        rec, start_sec = self.chunks[idx]
        f0 = start_sec * FRAMES_PER_SECOND
        feats = np.asarray(
            self._features(rec)[f0 : f0 + self.frames_per_chunk], dtype=np.float32
        )
        if feats.shape[0] < self.frames_per_chunk:  # ragged tail
            pad = self.frames_per_chunk - feats.shape[0]
            feats = np.pad(feats, ((0, pad), (0, 0), (0, 0)))

        end_sec = start_sec + self.chunk_size
        segs = [
            (s, e, spk)
            for s, e, spk in self.annotations.get(rec, [])
            if s < end_sec and e > start_sec
        ]
        speakers = sorted({spk for _, _, spk in segs})
        labels = np.zeros((self.target_frames, max(len(speakers), 1)), dtype=np.uint8)
        index = {spk: i for i, spk in enumerate(speakers)}
        for s, e, spk in segs:
            active = (self._centres >= s - start_sec) & (self._centres < e - start_sec)
            labels[active, index[spk]] = 1

        return feats, labels, rec


def collate(batch, max_speakers_per_chunk: int = 4):
    xs, ts, names = [], [], []
    for feats, labels, name in batch:
        n = labels.shape[-1]
        if n > max_speakers_per_chunk:
            keep = np.argsort(-labels.sum(axis=0))[:max_speakers_per_chunk]
            labels = labels[:, keep]
        elif n < max_speakers_per_chunk:
            labels = np.pad(labels, ((0, 0), (0, max_speakers_per_chunk - n)))
        xs.append(feats)
        ts.append(labels)
        names.append(name)
    return {
        "xs": torch.from_numpy(np.stack(xs)).float(),   # (B, T, S, D)
        "ts": torch.from_numpy(np.stack(ts)),           # (B, T*up, max_spk)
        "names": names,
    }
