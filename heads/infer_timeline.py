"""Full-recording diarization: run the sidecar head over a whole recording and stitch
the per-chunk outputs into one global speaker timeline.

The head predicts a fixed number of speaker slots per chunk with arbitrary ordering, so
"speaker#1" in one chunk is unrelated to "speaker#1" in the next. Consecutive chunks
overlap by ``chunk_size - chunk_shift`` seconds, and we align each new chunk's slots to
the speakers accumulated so far by Hungarian matching on that overlap. Slots that match
nothing well enough become new speakers.

Outputs an RTTM and, with ``--score``, the full-recording DER. This is the number that
matters: the chunk-level DER reported during training never has to track a speaker
across a chunk boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
from scipy.optimize import linear_sum_assignment
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from dataset_cached import load_rttm, load_scp, load_uem  # noqa: E402
from model_sidecar import SpeakerSidecar  # noqa: E402
from omni_frontend import FRAMES_PER_SECOND  # noqa: E402


def soft_multilabel(scores: torch.Tensor, powerset) -> np.ndarray:
    """Powerset log-posteriors -> per-speaker marginal probabilities (T, num_speakers)."""
    return (scores.exp() @ powerset.mapping).clamp(0, 1).cpu().numpy()


class TimelineStitcher:
    """Accumulate overlapping chunk predictions into one globally-consistent timeline."""

    def __init__(self, total_frames: int, match_threshold: float = 0.35,
                 min_slot_activity: float = 0.5):
        self.total = total_frames
        self.match_threshold = match_threshold
        self.min_slot_activity = min_slot_activity
        self.tracks: List[np.ndarray] = []          # each (total_frames,) summed probability
        self.counts = np.zeros(total_frames, dtype=np.float32)

    def _new_track(self) -> int:
        self.tracks.append(np.zeros(self.total, dtype=np.float32))
        return len(self.tracks) - 1

    def add(self, chunk: np.ndarray, offset: int) -> None:
        """chunk: (L, num_slots) marginal probabilities starting at frame ``offset``."""
        L = min(chunk.shape[0], self.total - offset)
        if L <= 0:
            return
        chunk = chunk[:L]
        sl = slice(offset, offset + L)

        seen = self.counts[sl] > 0
        active = [s for s in range(chunk.shape[1]) if chunk[:, s].sum() >= self.min_slot_activity]

        if self.tracks and seen.any():
            norm = np.maximum(self.counts[sl], 1e-6)
            existing = np.stack([t[sl] / norm for t in self.tracks], axis=1)  # (L, G)
            # agreement between each active slot and each existing speaker over the overlap
            sim = np.einsum("ls,lg->sg", chunk[:, active] * seen[:, None], existing)
            denom = np.maximum(
                (chunk[:, active] * seen[:, None]).sum(0)[:, None]
                + (existing * seen[:, None]).sum(0)[None, :] - sim,
                1e-6,
            )
            iou = sim / denom  # soft Jaccard
            rows, cols = linear_sum_assignment(-iou)
            mapping = {}
            for r, c in zip(rows, cols):
                if iou[r, c] >= self.match_threshold:
                    mapping[active[r]] = c
        else:
            mapping = {}

        for s in active:
            g = mapping.get(s)
            if g is None:
                g = self._new_track()
            self.tracks[g][sl] += chunk[:, s]
        self.counts[sl] += 1

    def activity(self) -> np.ndarray:
        """(total_frames, num_speakers) averaged probabilities."""
        if not self.tracks:
            return np.zeros((self.total, 0), dtype=np.float32)
        norm = np.maximum(self.counts, 1e-6)[:, None]
        return np.stack(self.tracks, axis=1) / norm


def to_segments(activity: np.ndarray, frame_sec: float, onset: float = 0.5,
                min_duration_on: float = 0.20, min_duration_off: float = 0.20):
    """Binarise and merge into (start, end, speaker_index) segments."""
    segments = []
    for s in range(activity.shape[1]):
        active = activity[:, s] >= onset
        if not active.any():
            continue
        edges = np.diff(np.concatenate([[0], active.view(np.int8), [0]]))
        starts = np.flatnonzero(edges == 1)
        ends = np.flatnonzero(edges == -1)

        merged: List[List[float]] = []
        for a, b in zip(starts * frame_sec, ends * frame_sec):
            if merged and a - merged[-1][1] < min_duration_off:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        segments += [(a, b, s) for a, b in merged if b - a >= min_duration_on]
    return sorted(segments)


def write_rttm(path: str, per_recording: Dict[str, List[Tuple[float, float, int]]]) -> None:
    with open(path, "w") as f:
        for rec, segs in per_recording.items():
            for start, end, spk in segs:
                f.write(
                    f"SPEAKER {rec} 1 {start:.3f} {end - start:.3f} "
                    f"<NA> <NA> spk{spk:02d} <NA> <NA>\n"
                )


@torch.no_grad()
def diarize(model, feats: np.ndarray, chunk_size: int, chunk_shift: int,
            device, batch_size: int = 16) -> np.ndarray:
    """feats: (T, S, D) cached encoder features -> (T*upsample, num_speakers) activity."""
    frames = feats.shape[0]
    per_chunk = chunk_size * FRAMES_PER_SECOND
    step = chunk_shift * FRAMES_PER_SECOND
    up = model.upsample

    starts = list(range(0, max(1, frames - per_chunk + 1), step))
    if starts[-1] + per_chunk < frames:
        starts.append(max(0, frames - per_chunk))

    stitcher = TimelineStitcher(frames * up)
    for i in range(0, len(starts), batch_size):
        group = starts[i : i + batch_size]
        batch = np.zeros((len(group), per_chunk, feats.shape[1], feats.shape[2]), dtype=np.float32)
        for j, s in enumerate(group):
            piece = feats[s : s + per_chunk]
            batch[j, : piece.shape[0]] = piece
        scores = model(torch.from_numpy(batch).to(device))
        for j, s in enumerate(group):
            stitcher.add(soft_multilabel(scores[j], model.powerset), s * up)
    return stitcher.activity()


def score(hyp_rttm: str, ref: Dict, uem: Dict, collar: float) -> float:
    from pyannote.core import Annotation, Segment, Timeline
    from pyannote.metrics.diarization import DiarizationErrorRate

    metric = DiarizationErrorRate(collar=collar, skip_overlap=False)
    hyp_all: Dict[str, Annotation] = {}
    for line in open(hyp_rttm):
        p = line.split()
        rec, start, dur, spk = p[1], float(p[3]), float(p[4]), p[7]
        hyp_all.setdefault(rec, Annotation(uri=rec))[Segment(start, start + dur)] = spk

    for rec, segs in ref.items():
        if rec not in uem:
            continue
        reference = Annotation(uri=rec)
        for s, e, spk in segs:
            reference[Segment(s, e)] = spk
        hypothesis = hyp_all.get(rec, Annotation(uri=rec))
        scope = Timeline([Segment(*uem[rec])], uri=rec)
        metric(reference, hypothesis, uem=scope)
    return abs(metric)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--feat_dir", required=True)
    ap.add_argument("--scp", required=True)
    ap.add_argument("--uem", required=True)
    ap.add_argument("--out_rttm", required=True)
    ap.add_argument("--ref_rttm", default=None, help="if given, report full-recording DER")
    ap.add_argument("--chunk_shift", type=int, default=None,
                    help="defaults to half the training chunk size (more overlap = better stitching)")
    ap.add_argument("--onset", type=float, default=0.5)
    ap.add_argument("--min_duration_on", type=float, default=0.20)
    ap.add_argument("--min_duration_off", type=float, default=0.20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--gpu", type=int, default=0)
    args = ap.parse_args()

    device = torch.device(f"cuda:{args.gpu}" if torch.cuda.is_available() else "cpu")
    ckpt = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    cfg, meta = ckpt["args"], ckpt["feature_meta"]
    chunk_size = cfg["chunk_size"]
    chunk_shift = args.chunk_shift if args.chunk_shift else max(1, chunk_size // 2)

    model = SpeakerSidecar(
        num_streams=len(meta["layers"]), d_model=meta["d_model"],
        adapter_dim=cfg["adapter_dim"], attention_in=cfg["attention_in"],
        conformer_layers=cfg["conformer_layers"], refine_layers=cfg["refine_layers"],
        dropout=cfg["dropout"], upsample=cfg["upsample"],
        max_speakers_per_chunk=cfg["max_speakers_per_chunk"],
        max_speakers_per_frame=cfg["max_speakers_per_frame"],
    ).to(device)
    model.load_state_dict(ckpt["model"])
    model.eval()
    print(f"Loaded epoch {ckpt['epoch']} (chunk-level DER {ckpt['val']['der']:.4f})")
    print(f"  chunk {chunk_size}s / shift {chunk_shift}s, {chunk_size - chunk_shift}s overlap for stitching")

    scp, uem = load_scp(args.scp), load_uem(args.uem)
    recs = [r for r in scp if r in uem and os.path.exists(os.path.join(args.feat_dir, f"{r}.npy"))]
    if args.limit:
        recs = recs[: args.limit]

    frame_sec = 1.0 / (FRAMES_PER_SECOND * cfg["upsample"])
    results, speaker_counts = {}, []
    for rec in tqdm(recs, desc="diarizing"):
        feats = np.load(os.path.join(args.feat_dir, f"{rec}.npy"), mmap_mode="r")
        activity = diarize(model, np.asarray(feats, dtype=np.float32), chunk_size,
                           chunk_shift, device)
        segs = to_segments(activity, frame_sec, args.onset,
                           args.min_duration_on, args.min_duration_off)
        results[rec] = segs
        speaker_counts.append(len({s for _, _, s in segs}))

    os.makedirs(os.path.dirname(os.path.abspath(args.out_rttm)), exist_ok=True)
    write_rttm(args.out_rttm, results)
    print(f"\nWrote {sum(len(v) for v in results.values())} segments to {args.out_rttm}")
    print(f"  predicted speakers per recording: mean {np.mean(speaker_counts):.1f}, "
          f"max {max(speaker_counts)}")

    if args.ref_rttm:
        ref = load_rttm(args.ref_rttm)
        ref = {r: v for r, v in ref.items() if r in set(recs)}
        n_ref = np.mean([len({s for _, _, s in v}) for v in ref.values()])
        print(f"  reference speakers per recording: mean {n_ref:.1f}")
        for collar in (0.0, 0.25):
            der = score(args.out_rttm, ref, uem, collar)
            print(f"  full-recording DER (collar {collar:.2f}s): {der:.2%}")


if __name__ == "__main__":
    main()
