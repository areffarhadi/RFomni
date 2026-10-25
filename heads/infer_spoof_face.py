#!/usr/bin/env python3
"""Run face antispoof inference with a trained MHFA sidecar checkpoint.

Examples:
  # Single image
  python infer_spoof_face.py --checkpoint exp/antispoof_mix87/face_140k/best.pt \\
      --image /path/to/face.jpg

  # Directory of images
  python infer_spoof_face.py --checkpoint exp/antispoof_mix87/face_140k/best.pt \\
      --image_dir /path/to/faces --output predictions.tsv

  # Evaluate 140k valid split
  python infer_spoof_face.py --checkpoint exp/antispoof_mix87/face_140k/best.pt \\
      --split valid

  # Full CelebA-Spoof test set
  python infer_spoof_face.py --checkpoint checkpoints/face_antispoof_140k_celeba/best.pt \\
      --celeba_split test --max_image_side 256
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable, List, Sequence

import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataset_antispoof import CelebASpoofSplit, Face140kSpoof, compute_eer, face_collate  # noqa: E402
from model_antispoof import AntispoofModel, UnifiedSpoofModel  # noqa: E402
from omni_vision_encoder import FrozenOmniVisionEncoder, enable_batched_vision_sdpa  # noqa: E402

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def load_checkpoint(path: str, device: torch.device) -> tuple[dict, AntispoofModel, list[int], float]:
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    if ckpt.get("modality") not in (None, "face"):
        raise ValueError(f"Expected face checkpoint, got modality={ckpt.get('modality')!r}")

    args = ckpt.get("args") or {}
    taps = ckpt.get("taps") or args.get("taps")
    if not taps:
        raise ValueError("Checkpoint is missing vision tap layers")

    if ckpt.get("model_type") == "unified":  # spoof v2: FAS + deepfake in one head
        model = UnifiedSpoofModel(
            feature_dim=1152,
            num_streams=len(taps),
            num_classes=len(ckpt["classes"]),
            num_heads=int(args.get("num_heads", 8)),
            compression_dim=int(args.get("compression_dim", 128)),
        )
    else:
        model = AntispoofModel(
            feature_dim=1152,
            num_streams=len(taps),
        )
    model.load_state_dict(ckpt["model"])
    model.eval().to(device)

    threshold = float(ckpt.get("eval", {}).get("threshold", 0.0))
    return ckpt, model, list(taps), threshold


def load_images_from_paths(paths: Sequence[str]) -> list[Image.Image]:
    images: list[Image.Image] = []
    for path in paths:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(path)
        images.append(Image.open(p).convert("RGB"))
    return images


def iter_image_paths(image_dir: str) -> list[str]:
    root = Path(image_dir)
    if not root.is_dir():
        raise NotADirectoryError(image_dir)
    paths = sorted(
        str(p)
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )
    if not paths:
        raise FileNotFoundError(f"No images found under {image_dir}")
    return paths


class FaceAntispoofInference:
    """Programmatic wrapper around the trained face antispoof head."""

    def __init__(
        self,
        checkpoint: str,
        *,
        encoder_path: str | None = None,
        device: str | torch.device | None = None,
        amp: str = "bf16",
        threshold: float | None = None,
        max_image_side: int | None = None,
    ):
        if device is None:
            device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        elif isinstance(device, str):
            device = torch.device(device)

        self.device = device
        self.amp_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "off": None}[amp]

        # A directory is an ensemble (e.g. checkpoints/spoof_v2: epochs 2-9 of one run): scores are
        # averaged over its *.pt members, which must share taps.
        members = sorted(Path(checkpoint).glob("*.pt")) if Path(checkpoint).is_dir() else [Path(checkpoint)]
        if not members:
            raise FileNotFoundError(f"no *.pt checkpoint in {checkpoint}")
        loaded = [load_checkpoint(str(m), device) for m in members]
        ckpt, self.model, self.taps, ckpt_thr = loaded[0]
        self.models = [m for _, m, _, _ in loaded]
        if any(list(t) != list(self.taps) for _, _, t, _ in loaded):
            raise ValueError("ensemble members use different taps")
        self.members = [str(m) for m in members]
        self.threshold = ckpt_thr if threshold is None else threshold
        self.ckpt_args = ckpt.get("args") or {}
        if max_image_side is None:
            max_image_side = int(self.ckpt_args.get("max_image_side") or 0)
        self.max_image_side = int(max_image_side)

        enc_path = encoder_path or self.ckpt_args.get("encoder_path")
        if not enc_path:
            raise ValueError("encoder_path not found in checkpoint and not provided")
        self.encoder = FrozenOmniVisionEncoder(enc_path, dtype=self.amp_dtype or torch.float32)
        self.encoder.eval().to(device)
        # spoof v2 checkpoints score a face-centred square crop (face_crop.py), not the whole image
        self.unified = ckpt.get("model_type") == "unified"
        self.crop_scale = float(self.ckpt_args.get("crop_scale") or 0.0) if self.unified else 0.0
        self._cropper = None
        self.task_thresholds: dict[str, float] = {}
        if self.unified:
            enable_batched_vision_sdpa(self.encoder.encoder)
            self.max_image_side = int(self.ckpt_args.get("side") or 256)
            # per-task decision thresholds = mean val-EER threshold of the FAS / deepfake val domains,
            # averaged over ensemble members (mean of (s_k - t_k) = mean s_k - mean t_k)
            per = []
            for c, _, _, _ in loaded:
                dom = (c.get("eval") or {}).get("domains") or {}
                fas = [v["thr"] for d, v in dom.items() if d.startswith("fas_")]
                df = [v["thr"] for d, v in dom.items() if d.startswith("df_")]
                if fas and df:
                    per.append((float(np.mean(fas)), float(np.mean(df))))
            if per and threshold is None:
                self.task_thresholds = {"fas": float(np.mean([a for a, _ in per])),
                                        "deepfakes": float(np.mean([b for _, b in per]))}
        if self.max_image_side > 0:
            side = self.max_image_side
            self.encoder.set_image_pixel_budget(
                min_pixels=min(256 * 256, side * side),
                max_pixels=side * side,
            )

    def prepare_paths(self, paths: Sequence[str]) -> list[Image.Image]:
        """Images exactly as this checkpoint expects them (face crop for v2, long-side cap for v1)."""
        images = [Image.open(p).convert("RGB") for p in paths]
        if not self.crop_scale:
            side = self.max_image_side
            for im in images:
                if side > 0 and max(im.size) > side:
                    im.thumbnail((side, side), Image.Resampling.BICUBIC)
            return images
        if self._cropper is None:
            from face_crop import FaceCropper

            self._cropper = FaceCropper(device=str(self.device))
        crops, _ = self._cropper.crops(images, scale=self.crop_scale, out_size=self.max_image_side)
        return crops

    def score_paths(self, paths: Sequence[str], batch_size: int = 32) -> np.ndarray:
        """Spoof score per image path. v2: every sizeable face is scored and the image takes the max
        (a frame is fake if any face is), exactly as eval_facex_spoof_v2.py --view facemax."""
        if not self.unified:
            out = [self.score_images(self.prepare_paths(paths[i : i + batch_size]))
                   for i in range(0, len(paths), batch_size)]
            return np.concatenate(out) if out else np.array([], dtype=np.float32)
        from face_crop import FaceCropper, pick_face, square_crop

        if self._cropper is None:
            self._cropper = FaceCropper(device=str(self.device))
        out = []
        for i in range(0, len(paths), batch_size):
            images = [Image.open(p).convert("RGB") for p in paths[i : i + batch_size]]
            # best face at conf 0.3 (fallback) + all faces at conf 0.5 within 4x area of the largest
            dets_lo = self._cropper.detect(images)
            crops, owner = [], []
            for k, (im, (b, c)) in enumerate(zip(images, dets_lo)):
                keep = [bb for bb, cc in zip(b, c) if cc >= 0.5]
                if keep:
                    areas = [(x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in keep]
                    keep = [bb for bb, a in zip(keep, areas) if a >= 0.25 * max(areas)]
                if not keep:
                    j = pick_face(b, c, im.size)
                    keep = [b[j]] if j is not None else []
                if keep:
                    crops += [square_crop(im, bb, self.crop_scale, self.max_image_side) for bb in keep]
                    owner += [k] * len(keep)
                else:
                    crops.append(im.resize((self.max_image_side, self.max_image_side), Image.Resampling.BICUBIC))
                    owner.append(k)
            s = self.score_images(crops)
            best = np.full(len(images), -np.inf, dtype=np.float32)
            for k, v in zip(owner, s):
                best[k] = max(best[k], v)
            out.append(best)
        return np.concatenate(out) if out else np.array([], dtype=np.float32)

    @torch.no_grad()
    def score_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        if not images:
            return np.array([], dtype=np.float32)

        with torch.autocast(
            self.device.type,
            dtype=self.amp_dtype,
            enabled=self.amp_dtype is not None and self.device.type == "cuda",
        ):
            views = [list(images)]
            if self.unified:  # flip TTA, as in eval_facex_spoof_v2.py --tta
                views.append([im.transpose(Image.Transpose.FLIP_LEFT_RIGHT) for im in images])
            scores = np.zeros(len(images), dtype=np.float64)
            for v in views:
                feats = self.encoder.encode_images(v, self.taps, self.device)
                for m in self.models:
                    scores += m.spoof_score(feats).float().cpu().numpy()
        return (scores / (len(views) * len(self.models))).astype(np.float32)

    def predict_images(self, images: Sequence[Image.Image]) -> list[dict]:
        scores = self.score_images(images)
        out = []
        for score in scores:
            is_spoof = bool(score > self.threshold)
            out.append(
                {
                    "score": float(score),
                    "threshold": float(self.threshold),
                    "label": "spoof" if is_spoof else "bonafide",
                    "is_spoof": is_spoof,
                }
            )
        return out

    @torch.no_grad()
    def evaluate_dataset(self, dataset, batch_size: int = 32, num_workers: int = 4, desc: str = "eval") -> dict:
        loader = DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=False,
            num_workers=num_workers,
            collate_fn=face_collate,
            pin_memory=self.device.type == "cuda",
        )
        scores, labels = [], []
        for images, y in tqdm(loader, desc=desc):
            s = self.score_images(images)
            scores.append(s)
            labels.append(y.numpy())
        scores = np.concatenate(scores)
        labels = np.concatenate(labels)
        eer, thr = compute_eer(scores, labels)
        pred = (scores >= thr).astype(int)
        n_live = int((labels == 0).sum())
        n_spoof = int((labels == 1).sum())
        live_acc = float((pred[labels == 0] == 0).mean()) if n_live else float("nan")
        spoof_acc = float((pred[labels == 1] == 1).mean()) if n_spoof else float("nan")
        return {
            "eer": eer,
            "threshold": thr,
            "acc": float((pred == labels).mean()),
            "live_acc": live_acc,
            "spoof_acc": spoof_acc,
            "bpcer": 1.0 - live_acc if n_live else float("nan"),
            "apcer": 1.0 - spoof_acc if n_spoof else float("nan"),
            "num_images": int(len(labels)),
            "num_live": n_live,
            "num_spoof": n_spoof,
        }

    @torch.no_grad()
    def evaluate_split(self, split: str, batch_size: int = 32, num_workers: int = 4) -> dict:
        dataset = Face140kSpoof(split=split, seed=0)
        stats = self.evaluate_dataset(dataset, batch_size=batch_size, num_workers=num_workers, desc=f"eval {split}")
        stats["split"] = split
        stats["dataset"] = "140k"
        return stats

    @torch.no_grad()
    def evaluate_celeba(self, split: str, batch_size: int = 32, num_workers: int = 4) -> dict:
        print(f"Scanning CelebA-Spoof {split}...", flush=True)
        dataset = CelebASpoofSplit(split=split, max_side=self.max_image_side)
        print(f"  live={dataset.num_live}  spoof={dataset.num_spoof}  n={len(dataset)}", flush=True)
        stats = self.evaluate_dataset(
            dataset, batch_size=batch_size, num_workers=num_workers, desc=f"celeba {split}"
        )
        stats["split"] = split
        stats["dataset"] = "celeba-spoof"
        stats["max_image_side"] = self.max_image_side
        return stats


def _write_predictions(path: str, rows: Iterable[dict]) -> None:
    rows = list(rows)
    ext = Path(path).suffix.lower()
    if ext == ".json":
        with open(path, "w") as f:
            json.dump(rows, f, indent=2)
        return

    with open(path, "w") as f:
        f.write("path\tscore\tthreshold\tlabel\tis_spoof\n")
        for row in rows:
            f.write(
                f"{row['path']}\t{row['score']:.6f}\t{row['threshold']:.6f}\t"
                f"{row['label']}\t{int(row['is_spoof'])}\n"
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--checkpoint",
        default="exp/antispoof_mix87/face_140k/best.pt",
        help="Path to trained face antispoof checkpoint",
    )
    ap.add_argument("--encoder_path", default=None, help="Override encoder path from checkpoint")
    ap.add_argument("--image", action="append", default=[], help="Image path (repeatable)")
    ap.add_argument("--image_list", default=None, help="Text file with one image path per line")
    ap.add_argument("--image_dir", default=None, help="Directory of images to score recursively")
    ap.add_argument("--split", choices=["valid", "val", "test"], default=None, help="Evaluate 140k split")
    ap.add_argument(
        "--celeba_split",
        choices=["train", "test"],
        default=None,
        help="Evaluate full CelebA-Spoof split (live/spoof folders)",
    )
    ap.add_argument("--output", default=None, help="Write predictions or eval JSON")
    ap.add_argument("--batch_size", type=int, default=32)
    ap.add_argument("--num_workers", type=int, default=8)
    ap.add_argument("--threshold", type=float, default=None, help="Override decision threshold")
    ap.add_argument("--task", choices=["fas", "deepfakes"], default=None,
                    help="v2 (unified) checkpoints: decide with this task's validation threshold")
    ap.add_argument("--max_image_side", type=int, default=None, help="Long-side cap (default: checkpoint)")
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--amp", default="bf16", choices=["bf16", "fp16", "off"])
    args = ap.parse_args()

    device = torch.device(f"cuda:{args.gpu}" if torch.cuda.is_available() else "cpu")
    engine = FaceAntispoofInference(
        args.checkpoint,
        encoder_path=args.encoder_path,
        device=device,
        amp=args.amp,
        threshold=args.threshold,
        max_image_side=args.max_image_side,
    )

    if args.celeba_split or args.split:
        if args.celeba_split:
            stats = engine.evaluate_celeba(
                args.celeba_split, batch_size=args.batch_size, num_workers=args.num_workers
            )
        else:
            stats = engine.evaluate_split(
                args.split, batch_size=args.batch_size, num_workers=args.num_workers
            )
        print(
            f"dataset={stats.get('dataset')}  split={stats['split']}  "
            f"EER={stats['eer']:.4%}  acc={stats['acc']:.4f}  "
            f"live_acc={stats.get('live_acc', float('nan')):.4f}  "
            f"spoof_acc={stats.get('spoof_acc', float('nan')):.4f}  "
            f"threshold={stats['threshold']:.4f}  n={stats['num_images']}"
        )
        if args.output:
            with open(args.output, "w") as f:
                json.dump(stats, f, indent=2)
        return

    paths: List[str] = list(args.image)
    if args.image_list:
        with open(args.image_list) as f:
            paths.extend(line.strip() for line in f if line.strip())
    if args.image_dir:
        paths.extend(iter_image_paths(args.image_dir))
    if not paths:
        ap.error("Provide --image, --image_list, --image_dir, --split, or --celeba_split")

    if engine.unified:  # v2: face crops, max over faces, flip TTA, as in the FaceXBench numbers
        thr = args.threshold if args.threshold is not None else (
            engine.task_thresholds[args.task] if args.task else engine.threshold)
        rows = []
        for path, score in zip(paths, engine.score_paths(paths, batch_size=args.batch_size)):
            is_spoof = bool(score > thr)
            rows.append({"path": path, "score": float(score), "threshold": float(thr),
                         "label": "spoof" if is_spoof else "bonafide", "is_spoof": is_spoof})
            print(f"{path}\tscore={score:.3f}\tlabel={rows[-1]['label']}\tthreshold={thr:.3f}")
        if args.output:
            _write_predictions(args.output, rows)
        return

    rows = []
    for i in tqdm(range(0, len(paths), args.batch_size), desc="infer"):
        batch_paths = paths[i : i + args.batch_size]
        images = load_images_from_paths(batch_paths)
        preds = engine.predict_images(images)
        for path, pred in zip(batch_paths, preds):
            row = {"path": path, **pred}
            rows.append(row)
            print(
                f"{path}\tscore={pred['score']:.3f}\t"
                f"label={pred['label']}\tthreshold={pred['threshold']:.3f}"
            )

    if args.output:
        _write_predictions(args.output, rows)


if __name__ == "__main__":
    main()
