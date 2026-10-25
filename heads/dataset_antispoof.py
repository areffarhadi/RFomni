"""Datasets for audio (SpoofCeleb) and face (140k) antispoof training."""

from __future__ import annotations

import csv
import json
import os
import random
from pathlib import Path
from typing import List, Sequence, Tuple

import numpy as np
import soundfile as sf
import torch
from PIL import Image
from torch.utils.data import Dataset

SAMPLE_RATE = 16000
FACE140K_ROOT = Path("/data/d01/speech/tts/data/Aref-data/140k_real_and_fake_faces")
FACE140K_IMG = FACE140K_ROOT / "real_vs_fake" / "real_vs_fake"
CELEBA_SPOOF_ROOT = Path(
    "/data/d01/speech/tts/data/Aref-data/celeba-spoof/extracted/CelebA_Spoof_/CelebA_Spoof"
)
FACE_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def compute_eer(scores: np.ndarray, labels: np.ndarray) -> Tuple[float, float]:
    """EER for binary labels (1 = positive class / spoof)."""
    order = np.argsort(scores)[::-1]
    labels = labels[order]
    tp = np.cumsum(labels)
    fp = np.cumsum(1 - labels)
    fnr = 1.0 - tp / max(labels.sum(), 1)
    fpr = fp / max((1 - labels).sum(), 1)
    idx = int(np.nanargmin(np.abs(fnr - fpr)))
    return float((fnr[idx] + fpr[idx]) / 2), float(np.sort(scores)[::-1][idx])


def read_crop(path: str, num_samples: int, rng: np.random.Generator) -> np.ndarray:
    info = sf.info(path)
    total = info.frames
    if total >= num_samples:
        start = int(rng.integers(0, total - num_samples + 1))
        wav, _ = sf.read(path, start=start, frames=num_samples, dtype="float32", always_2d=True)
        wav = wav[:, 0]
    else:
        wav, _ = sf.read(path, dtype="float32", always_2d=True)
        wav = np.tile(wav[:, 0], int(np.ceil(num_samples / max(total, 1))))[:num_samples]
    peak = np.abs(wav).max()
    if peak > 0:
        wav = wav / peak
    return wav


def build_spoofceleb_manifest(
    metadata_csv: str,
    flac_root: str,
    cache_path: str,
    *,
    attacks: Sequence[str] | None = None,
    max_entries: int = 0,
    seed: int = 42,
) -> List[Tuple[str, int]]:
    """Return (relative_flac_path, label) with label 0=bonafide, 1=spoof."""
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            rows = [line.rstrip("\n").split("\t") for line in f if line.strip()]
        return [(p, int(l)) for p, l in rows]

    bonafide, spoof = [], []
    with open(metadata_csv, newline="") as f:
        for row in csv.DictReader(f):
            rel, attack = row["file"], row["attack"]
            if attacks is not None and attack not in attacks:
                continue
            full = os.path.join(flac_root, rel)
            if not os.path.isfile(full):
                continue
            (bonafide if attack == "a00" else spoof).append((rel, 0 if attack == "a00" else 1))

    rng = random.Random(seed)
    rng.shuffle(bonafide)
    rng.shuffle(spoof)
    n = min(len(bonafide), len(spoof))
    if max_entries:
        n = min(n, max_entries // 2)
    entries = bonafide[:n] + spoof[:n]
    rng.shuffle(entries)

    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, "w") as f:
        for rel, lab in entries:
            f.write(f"{rel}\t{lab}\n")
    return entries


def load_sdd_protocol(protocol_csv: str, flac_root: str) -> List[Tuple[str, int]]:
    """SDD protocol: file, spoof label string -> (path, 0/1)."""
    out = []
    with open(protocol_csv, newline="") as f:
        for row in csv.DictReader(f):
            rel = row["file"]
            lab = 0 if row["spoof"] == "bonafide" else 1
            path = os.path.join(flac_root, rel)
            if os.path.isfile(path):
                out.append((path, lab))
    return out


class SpoofCelebAudio(Dataset):
    def __init__(
        self,
        entries: Sequence[Tuple[str, int]],
        flac_root: str,
        crop_seconds: float = 3.0,
        seed: int = 0,
        augment=None,
    ):
        self.entries = list(entries)
        self.flac_root = flac_root
        self.num_samples = int(crop_seconds * SAMPLE_RATE)
        self._seed = seed
        self.augment = augment

    def set_epoch(self, epoch: int) -> None:
        self._seed = epoch

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, idx: int):
        rel, label = self.entries[idx]
        rng = np.random.default_rng((self._seed * 1_000_003 + idx) % (2**32))
        wav = read_crop(os.path.join(self.flac_root, rel), self.num_samples, rng)
        if self.augment is not None:
            wav = self.augment(wav, rng)
        return torch.from_numpy(wav), label


class SpoofCelebEvalAudio(Dataset):
    def __init__(self, items: Sequence[Tuple[str, int]], crop_seconds: float = 3.0):
        self.items = list(items)
        self.num_samples = int(crop_seconds * SAMPLE_RATE)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int):
        path, label = self.items[idx]
        wav = read_crop(path, self.num_samples, np.random.default_rng(idx))
        return torch.from_numpy(wav), label, idx


class Face140kSpoof(Dataset):
    def __init__(self, split: str = "train", seed: int = 0, max_per_class: int = 0):
        split_file = {"train": "train", "valid": "valid", "val": "valid", "test": "test"}[split]
        csv_path = FACE140K_ROOT / f"{split_file}.csv"
        rows: List[Tuple[str, int]] = []
        with csv_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rel = (row.get("path") or "").strip()
                raw = (row.get("label") or "").strip()
                if not rel:
                    continue
                lab = 0 if raw in {"1", "real"} else 1
                p = FACE140K_IMG / rel
                if p.is_file():
                    rows.append((str(p), lab))
        by_lab = {0: [], 1: []}
        for p, l in rows:
            by_lab[l].append(p)
        rng = random.Random(seed)
        n = min(len(by_lab[0]), len(by_lab[1]))
        if max_per_class:
            n = min(n, max_per_class)
        self.paths = []
        self.labels = []
        for lab in (0, 1):
            pool = list(by_lab[lab])
            rng.shuffle(pool)
            self.paths.extend(pool[:n])
            self.labels.extend([lab] * n)
        combined = list(zip(self.paths, self.labels))
        rng.shuffle(combined)
        self.paths, self.labels = [list(x) for x in zip(*combined)] if combined else ([], [])

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, idx: int):
        return Image.open(self.paths[idx]).convert("RGB"), self.labels[idx]


class FaceManifestSpoof(Dataset):
    """Face spoof list from a TSV manifest: path, label[, source]."""

    def __init__(self, manifest_path: str | Path, max_side: int = 0):
        self.manifest_path = str(manifest_path)
        self.max_side = int(max_side)
        self.paths: List[str] = []
        self.labels: List[int] = []
        self.sources: List[str] = []
        with open(self.manifest_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                path, lab = parts[0], int(parts[1])
                src = parts[2] if len(parts) > 2 else ""
                self.paths.append(path)
                self.labels.append(lab)
                self.sources.append(src)

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, idx: int):
        image = Image.open(self.paths[idx]).convert("RGB")
        if self.max_side > 0 and max(image.size) > self.max_side:
            image.thumbnail((self.max_side, self.max_side), Image.Resampling.BICUBIC)
        return image, self.labels[idx]


class CelebASpoofSplit(Dataset):
    """Full CelebA-Spoof train or test split (live=0, spoof=1)."""

    def __init__(self, split: str = "test", max_side: int = 256):
        by_lab = list_celeba_spoof_images(split)
        self.paths = by_lab[0] + by_lab[1]
        self.labels = [0] * len(by_lab[0]) + [1] * len(by_lab[1])
        self.max_side = int(max_side)
        self.split = split
        self.num_live = len(by_lab[0])
        self.num_spoof = len(by_lab[1])

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, idx: int):
        image = Image.open(self.paths[idx]).convert("RGB")
        if self.max_side > 0 and max(image.size) > self.max_side:
            image.thumbnail((self.max_side, self.max_side), Image.Resampling.BICUBIC)
        return image, self.labels[idx]


def list_celeba_spoof_images(split: str) -> dict[int, List[str]]:
    """Return {0: live paths, 1: spoof paths} for CelebA-Spoof train/test."""
    root = CELEBA_SPOOF_ROOT / "Data" / split
    by_lab: dict[int, List[str]] = {0: [], 1: []}
    if not root.is_dir():
        raise FileNotFoundError(f"CelebA-Spoof split missing: {root}")
    for sid_dir in sorted(root.iterdir()):
        if not sid_dir.is_dir():
            continue
        for lab, name in ((0, "live"), (1, "spoof")):
            folder = sid_dir / name
            if not folder.is_dir():
                continue
            for dirpath, _, filenames in os.walk(folder):
                for fn in filenames:
                    if Path(fn).suffix.lower() in FACE_IMAGE_EXTS:
                        by_lab[lab].append(os.path.join(dirpath, fn))
    return by_lab


FFPP_C23_ROOT = Path("/home/aref.farhadipour/datasets/FaceForensics++_C23")
FFPP_SPLITS_DIR = Path("/home/aref.farhadipour/omni-mix87-sidecar-heads/data/ffpp_splits")
FFPP_FRAMES_DIR = Path("/data/d01/speech/tts/data/Aref-data/ffpp_c23_frames")
FFPP_REAL_FOLDER = "original"
FFPP_FAKE_FOLDERS = (
    "Deepfakes",
    "Face2Face",
    "FaceShifter",
    "FaceSwap",
    "NeuralTextures",
    "DeepFakeDetection",
)
FFPP_CLASSIC_FOLDERS = (
    "original",
    "Deepfakes",
    "Face2Face",
    "FaceShifter",
    "FaceSwap",
    "NeuralTextures",
)


def load_ffpp_id_splits(splits_dir: str | Path = FFPP_SPLITS_DIR) -> dict[str, str]:
    """Map original video id ('000'..'999') to train/val/test from official FF++ pairs."""
    splits_dir = Path(splits_dir)
    id_to_split: dict[str, str] = {}
    for split in ("train", "val", "test"):
        data = json.loads((splits_dir / f"{split}.json").read_text(encoding="utf-8"))
        for pair in data:
            for vid in pair:
                vid = str(vid).zfill(3)
                prev = id_to_split.get(vid)
                if prev and prev != split:
                    raise ValueError(f"FF++ id {vid} in both {prev} and {split}")
                id_to_split[vid] = split
    return id_to_split


def ffpp_video_split(stem: str, method: str, id_to_split: dict[str, str], dfd_split: dict[str, str]) -> str | None:
    if method == "DeepFakeDetection":
        return dfd_split.get(stem)
    orig_id = stem.split("_")[0].zfill(3)
    return id_to_split.get(orig_id)


def extract_ffpp_c23_frames(
    *,
    root: str | Path = FFPP_C23_ROOT,
    frames_dir: str | Path = FFPP_FRAMES_DIR,
    splits_dir: str | Path = FFPP_SPLITS_DIR,
    n_frames: int = 16,
    max_side: int = 256,
    seed: int = 42,
    workers: int = 16,
) -> dict[str, list[tuple[str, int, str]]]:
    """Extract frames and return {train,val,test} lists of (path, label, source)."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor, as_completed

    from ffpp_frame_extract import extract_uniform_video_frames

    root = Path(root)
    frames_dir = Path(frames_dir)
    id_to_split = load_ffpp_id_splits(splits_dir)
    dfd_dir = root / "DeepFakeDetection"
    dfd_stems = sorted(p.stem for p in dfd_dir.glob("*.mp4")) if dfd_dir.is_dir() else []
    rng = random.Random(seed)
    rng.shuffle(dfd_stems)
    n_val = max(1, int(round(0.2 * len(dfd_stems))))
    dfd_split = {stem: ("val" if i < n_val else "train") for i, stem in enumerate(dfd_stems)}

    jobs = []
    meta = []
    for method in (FFPP_REAL_FOLDER,) + FFPP_FAKE_FOLDERS:
        folder = root / method
        if not folder.is_dir():
            continue
        label = 0 if method == FFPP_REAL_FOLDER else 1
        source = f"ffpp_{method.lower()}"
        for video in sorted(folder.glob("*.mp4")):
            split = ffpp_video_split(video.stem, method, id_to_split, dfd_split)
            if split is None:
                continue
            out = frames_dir / method / video.stem
            jobs.append((str(video), str(out), n_frames, max_side))
            meta.append((split, label, source))

    rows = {"train": [], "val": [], "test": []}
    done = 0
    n_jobs = len(jobs)
    print(f"  FF++ videos to extract: {n_jobs}", flush=True)
    if workers <= 1:
        for job, (split, label, source) in zip(jobs, meta):
            for path in extract_uniform_video_frames(*job):
                rows[split].append((path, label, source))
            done += 1
            if done % 50 == 0 or done == n_jobs:
                print(f"  extracted {done}/{n_jobs}", flush=True)
        return rows

    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
        fut_to_meta = {
            ex.submit(extract_uniform_video_frames, *job): m for job, m in zip(jobs, meta)
        }
        for fut in as_completed(fut_to_meta):
            split, label, source = fut_to_meta[fut]
            for path in fut.result():
                rows[split].append((path, label, source))
            done += 1
            if done % 50 == 0 or done == n_jobs:
                print(f"  extracted {done}/{n_jobs}", flush=True)
    return rows


def write_face_manifest(path: str | Path, rows: Sequence[Tuple[str, int, str]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for img, lab, src in rows:
            f.write(f"{img}\t{lab}\t{src}\n")


def face_collate(batch):
    images, labels = zip(*batch)
    return list(images), torch.tensor(labels, dtype=torch.long)
