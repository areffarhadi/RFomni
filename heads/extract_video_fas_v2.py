#!/usr/bin/env python3
"""One pass over a v2 manifest: decode, crop the face, run the frozen tower once.

For every frame named in <data_dir>/<split>.jsonl this writes

    frames_<split>.npy   (N, size, size, 3) uint8   the face crop the encoder saw
    feats_<split>.npy    (N, 27, 1152)      float16 token mean of every block output

so heads train on cached features in minutes instead of re-running the
encoder every epoch. Crops:

    crop "bb"      official RetinaFace box (_BB.txt; CelebA-Spoof), square, x margin
    crop "detect"  loc v2 "face" query on each frame, square, x margin
    crop "none"    frame resized as is

v2 manifests crop FF++ with "detect" and leave the 224x224 VoxCeleb2 clips
(LAV-DF, AV-1M) at "none"; v3 manifests (prepare_video_fas_v3.py) crop those
with "detect" too, the recipe video_fas_sidecar.py uses on any input. A 1.5x
margin on a face box shows the face at about the VoxCeleb2 scale.

    .venv/bin/python extract_video_fas_v2.py --split train --gpus 0,1,2,3,4,5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import cv2
import numpy as np
import torch
import torch.multiprocessing as mp
from torch.utils.data import DataLoader, Dataset

ENCODER_PATH = "/projects/capire/arfarh/mix87_ckpt54000-merged-MXFP4A16mse-fused"
DEPTH, WIDTH = 27, 1152


def square_crop(img: np.ndarray, box, margin: float, size: int) -> np.ndarray:
    """Square window of side max(w, h) * margin around the box centre, kept inside the image when it fits."""
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    side = max(x2 - x1, y2 - y1) * margin
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side = max(8.0, side)
    left, top = cx - side / 2, cy - side / 2
    if side <= w:
        left = min(max(left, 0.0), w - side)
    if side <= h:
        top = min(max(top, 0.0), h - side)
    l, t, s = int(round(left)), int(round(top)), int(round(side))
    pad = max(0, -l, -t, l + s - w, t + s - h)
    if pad:
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        l, t = l + pad, t + pad
    crop = img[t:t + s, l:l + s]
    interp = cv2.INTER_AREA if s > size else cv2.INTER_LINEAR
    return cv2.resize(crop, (size, size), interpolation=interp)


def read_video_frames(path: str, wanted: list[int]) -> dict[int, np.ndarray]:
    """Frame-accurate: decode sequentially and keep the wanted indexes (RGB)."""
    want = set(wanted)
    last = max(wanted)
    cap = cv2.VideoCapture(path)
    out, index = {}, 0
    while index <= last:
        if not cap.grab():
            break
        if index in want:
            ok, bgr = cap.retrieve()
            if ok:
                out[index] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        index += 1
    cap.release()
    return out


def celeba_box(path: str, img: np.ndarray):
    """The _BB.txt box is given on a 224x224 rescale of the image."""
    stem, _ = os.path.splitext(path)
    try:
        x, y, bw, bh = [float(v) for v in open(stem + "_BB.txt").read().split()[:4]]
    except (OSError, ValueError):
        return None
    if bw <= 1 or bh <= 1:
        return None
    h, w = img.shape[:2]
    return [x * w / 224, y * h / 224, (x + bw) * w / 224, (y + bh) * h / 224]


class ItemDataset(Dataset):
    def __init__(self, items, size, margin):
        self.items, self.size, self.margin = items, size, margin

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        it = self.items[i]
        frames = it["frames"]
        try:
            if it["media"] == "image":
                bgr = cv2.imread(it["path"])
                if bgr is None:
                    raise RuntimeError("unreadable image")
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                box = celeba_box(it["path"], rgb) if it["crop"] == "bb" else None
                if box is None:
                    h, w = rgb.shape[:2]
                    box = [0, 0, w, h]
                    crop = square_crop(rgb, box, 1.0, self.size)
                else:
                    crop = square_crop(rgb, box, self.margin, self.size)
                return {"item": it["item"], "crops": np.stack([crop]), "full": None, "ok": [True]}
            got = read_video_frames(it["path"], frames)
        except Exception as exc:  # noqa: BLE001 - keep going, the frame is marked bad
            print(f"[warn] {it['path']}: {exc}", file=sys.stderr, flush=True)
            got = {}
        ok = [f in got for f in frames]
        if not any(ok):
            blank = np.zeros((self.size, self.size, 3), np.uint8)
            return {"item": it["item"], "crops": np.stack([blank] * len(frames)), "full": None, "ok": ok}
        fill = next(got[f] for f in frames if f in got)
        seq = [got.get(f, fill) for f in frames]
        if it["crop"] == "detect":
            return {"item": it["item"], "crops": None, "full": seq, "ok": ok}
        crops = [cv2.resize(fr, (self.size, self.size), interpolation=cv2.INTER_LINEAR if fr.shape[0] < self.size
                            else cv2.INTER_AREA) for fr in seq]
        return {"item": it["item"], "crops": np.stack(crops), "full": None, "ok": ok}


def patchify_gpu(x: torch.Tensor, patch: int = 16, merge: int = 2, temporal: int = 2):
    """Batched ``qwen_patchify`` on the GPU. x: (B, H, W, 3) uint8 -> (B*gh*gw, 3*temporal*patch^2), grid (B, 3)."""
    b, h, w, _ = x.shape
    gh, gw = h // patch, w // patch
    x = x.permute(0, 3, 1, 2).float().div_(255.0).sub_(0.5).div_(0.5)
    x = x.unsqueeze(1).expand(b, temporal, 3, h, w)
    x = x.reshape(b, temporal, 3, gh // merge, merge, patch, gw // merge, merge, patch)
    x = x.permute(0, 3, 6, 4, 7, 2, 1, 5, 8).reshape(b * gh * gw, 3 * temporal * patch * patch)
    grid = torch.tensor([1, gh, gw], device=x.device).expand(b, 3).contiguous()
    return x, grid


class NpyRows:
    """Positioned row writes into an existing .npy file.

    Mapping a 100 GB array in each GPU process breaks the 161 GB per-process
    address-space limit (ulimit -v) on this machine, so rows are written with pwrite.
    """

    def __init__(self, path: str):
        with open(path, "rb") as f:
            version = np.lib.format.read_magic(f)
            reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
            shape, _fortran, dtype = reader(f)
            self.offset = f.tell()
        self.shape, self.dtype = shape, dtype
        self.row_bytes = int(np.prod(shape[1:], dtype=np.int64)) * dtype.itemsize
        self.fd = os.open(path, os.O_RDWR)

    def write(self, start: int, rows: np.ndarray) -> None:
        data = np.ascontiguousarray(rows, dtype=self.dtype).tobytes()
        os.pwrite(self.fd, data, self.offset + start * self.row_bytes)

    def close(self) -> None:
        os.fsync(self.fd)
        os.close(self.fd)


def _as_is(sample):
    """batch_size=None would otherwise turn every numpy frame into a torch tensor."""
    return sample


def _worker_init(_):
    cv2.setNumThreads(1)
    torch.set_num_threads(1)


def run_rank(rank: int, gpu: int, world: int, args):
    from fas_face_detect import LocV2FaceDetector, encode_taps
    from omni_vision_encoder import FrozenOmniVisionEncoder

    # The default intra-op pool is half of 384 cores per process and starves the decoders.
    torch.set_num_threads(4)
    cv2.setNumThreads(2)
    torch.cuda.set_device(gpu)
    device = torch.device(f"cuda:{gpu}")
    items = [json.loads(line) for line in open(os.path.join(args.data_dir, f"{args.split}.jsonl"))]
    by_index = {it["item"]: it for it in items}
    done_flags = np.load(os.path.join(args.data_dir, f"done_{args.split}.npy"))
    mine = [it for it in items if it["item"] % world == rank and not done_flags[it["item"]]]
    frames_out = NpyRows(os.path.join(args.data_dir, f"frames_{args.split}.npy"))
    feats_out = NpyRows(os.path.join(args.data_dir, f"feats_{args.split}.npy"))
    done_out = NpyRows(os.path.join(args.data_dir, f"done_{args.split}.npy"))
    bad_out = NpyRows(os.path.join(args.data_dir, f"bad_{args.split}.npy"))
    if not mine:
        return
    encoder = FrozenOmniVisionEncoder(args.encoder_path, dtype=torch.bfloat16).to(device).eval()
    detector = None
    if any(it["crop"] == "detect" for it in mine):
        detector = LocV2FaceDetector(device, size="auto", encoder=encoder)
    loader = DataLoader(ItemDataset(mine, args.size, args.margin), batch_size=None, shuffle=False,
                        num_workers=args.workers, worker_init_fn=_worker_init, prefetch_factor=4, collate_fn=_as_is,
                        persistent_workers=False)
    taps = list(range(DEPTH))
    pending_crops, pending_items = [], []  # pending_items: (item, first row, frame count)
    t0, n_frames, n_items = time.time(), 0, 0

    def flush():
        nonlocal pending_crops, pending_items
        if not pending_crops:
            return
        crops = np.concatenate(pending_crops)
        parts = []
        # A long dense video can push one flush to ~400 frames; cap each encoder call.
        for i in range(0, len(crops), args.batch):
            pix, grid = patchify_gpu(torch.from_numpy(crops[i:i + args.batch]).to(device, non_blocking=True))
            with torch.autocast("cuda", dtype=torch.bfloat16):
                feats = encode_taps(encoder, pix.to(torch.bfloat16), grid, taps)  # (B, tokens, 27, D)
            parts.append(feats.mean(dim=1, dtype=torch.float32).half().cpu().numpy())
            del feats
        means = np.concatenate(parts)
        k = 0
        for item, start, count in pending_items:
            feats_out.write(start, means[k:k + count])
            done_out.write(item, np.ones(1, np.uint8))
            k += count
        pending_crops, pending_items = [], []

    det_queue = []  # (item, sample) waiting for one batched face-detection call

    def emit(it, sample, crops):
        nonlocal n_frames, n_items
        start = it["offset"]
        frames_out.write(start, crops)
        if not all(sample["ok"]):
            bad_out.write(start, ~np.asarray(sample["ok"]))
        pending_crops.append(np.ascontiguousarray(crops))
        pending_items.append((it["item"], start, len(crops)))
        n_frames += len(crops)
        n_items += 1
        if sum(c for _i, _s, c in pending_items) >= args.batch:
            flush()
        if n_items % 2000 == 0:
            rate = n_frames / (time.time() - t0)
            print(f"[gpu{gpu}] {n_items}/{len(mine)} items {n_frames} frames {rate:.0f} fr/s", flush=True)

    def run_detection():
        # A call per item ran 1-3 frames at a time and bound the whole pass; batch across items instead.
        frames = [f for _it, smp in det_queue for f in smp["full"]]
        boxes = detect_boxes(detector, frames)
        k = 0
        for it, smp in det_queue:
            n = len(smp["full"])
            emit(it, smp, crops_from_boxes(smp["full"], boxes[k:k + n], args.size, args.margin))
            k += n
        det_queue.clear()

    for sample in loader:
        it = by_index[sample["item"]]
        if sample["crops"] is None:
            det_queue.append((it, sample))
            if sum(len(smp["full"]) for _i, smp in det_queue) >= 64:
                run_detection()
        else:
            emit(it, sample, sample["crops"])
    if det_queue:
        run_detection()
    flush()
    for out in (frames_out, feats_out, done_out, bad_out):
        out.close()
    print(f"[gpu{gpu}] finished {n_items} items {n_frames} frames in {(time.time() - t0) / 60:.1f} min", flush=True)


def detect_boxes(detector, frames: list[np.ndarray]) -> list:
    """Best face box per frame, batched. Frames are grouped by detection canvas so each keeps its size."""
    groups: dict[int, list[int]] = {}
    for i, frame in enumerate(frames):
        groups.setdefault(detector.canvas(frame) if detector.size == "auto" else int(detector.size), []).append(i)
    boxes: list = [None] * len(frames)
    for canvas, idx in groups.items():
        chunk = max(8, 16384 // (canvas // 16) ** 2)  # about 16k tokens per call: 64 frames at 256, 16 at 512
        for i in range(0, len(idx), chunk):
            part = idx[i:i + chunk]
            for j, box in zip(part, detector.detect([frames[j] for j in part])):
                boxes[j] = box
    return boxes


def crops_from_boxes(full: list[np.ndarray], boxes: list, size: int, margin: float) -> np.ndarray:
    """A frame without a face takes the nearest frame's box; no face at all gives a centre square."""
    have = [i for i, b in enumerate(boxes) if b is not None]
    crops = []
    for i, frame in enumerate(full):
        if have:
            j = min(have, key=lambda k: abs(k - i))
            crops.append(square_crop(frame, boxes[j][:4], margin, size))
        else:
            h, w = frame.shape[:2]
            s = min(h, w)
            crops.append(square_crop(frame, [(w - s) / 2, (h - s) / 2, (w + s) / 2, (h + s) / 2], 1.0, size))
    return np.stack(crops)


def crop_with_detector(detector, full: list[np.ndarray], size: int, margin: float) -> np.ndarray:
    return crops_from_boxes(full, detect_boxes(detector, full), size, margin)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data_dir", default="/local/scratch/arfarh/video_fas_v2")
    ap.add_argument("--split", required=True, choices=("train", "val", "test", "train_hn", "val_hn"))
    ap.add_argument("--encoder_path", default=ENCODER_PATH)
    ap.add_argument("--gpus", default="0")
    ap.add_argument("--workers", type=int, default=24, help="decode workers per GPU")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--margin", type=float, default=1.5)
    ap.add_argument("--batch", type=int, default=192, help="frames per encoder call")
    ap.add_argument("--shard", default=None, help="R/W: only items with item %% W == R, on the first --gpus entry")
    args = ap.parse_args()

    items = [json.loads(line) for line in open(os.path.join(args.data_dir, f"{args.split}.jsonl"))]
    total = items[-1]["offset"] + len(items[-1]["frames"])
    paths = {
        "frames": (os.path.join(args.data_dir, f"frames_{args.split}.npy"), np.uint8, (total, args.size, args.size, 3)),
        "feats": (os.path.join(args.data_dir, f"feats_{args.split}.npy"), np.float16, (total, DEPTH, WIDTH)),
        "done": (os.path.join(args.data_dir, f"done_{args.split}.npy"), np.uint8, (len(items),)),
        "bad": (os.path.join(args.data_dir, f"bad_{args.split}.npy"), np.uint8, (total,)),
    }
    for path, dtype, shape in paths.values():
        if not os.path.exists(path):
            np.lib.format.open_memmap(path, mode="w+", dtype=dtype, shape=shape).flush()
    print(f"{args.split}: {len(items)} items, {total} frames", flush=True)
    gpus = [int(g) for g in args.gpus.split(",")]
    mp.set_start_method("spawn", force=True)
    if args.shard:
        # Finish one rank of an earlier world-size run without redoing the other ranks.
        rank, world = (int(v) for v in args.shard.split("/"))
        procs = [mp.Process(target=run_rank, args=(rank, gpus[0], world, args))]
    else:
        procs = [mp.Process(target=run_rank, args=(r, g, len(gpus), args)) for r, g in enumerate(gpus)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    done = np.load(paths["done"][0], mmap_mode="r")
    bad = np.load(paths["bad"][0], mmap_mode="r")
    print(f"{args.split}: done {int(done.sum())}/{len(items)} items, bad frames {int(bad.sum())}", flush=True)
    if any(p.exitcode != 0 for p in procs):
        raise SystemExit("a GPU worker failed")


if __name__ == "__main__":
    main()
