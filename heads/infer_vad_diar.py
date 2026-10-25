#!/usr/bin/env python3
"""VAD + speaker diarization from ONE frozen-encoder pass.

    python infer_vad_diar.py --audio meeting.wav --rttm out.rttm

Both heads read the same taps `[3,7,11,15,19,23,27,31]`, so the encoder runs once and each head is
a small amount of arithmetic on top:

    audio ─► frozen mix87 audio encoder (taps, 13 Hz)
               ├─ layer 31 ─► LinearVAD ────────► speech segments      (~0 cost)
               └─ all 8 taps ─► diar_sidecar ───► speaker activity, 52 Hz

`diar_sidecar` emits chunk-local speaker slots, and the stitcher's overlap matching alone
over-segments on hard audio (measured: 11 tracks for a 4-speaker VoxConverse file). Global speaker
identity therefore needs the `asv` head: one embedding per stitched track, average-linkage
clustering on cosine similarity, exactly as `audio_experts.agglomerative` does for speaker counting.
`--no-cluster` leaves the raw tracks if you want to see them.

`--vad-refine` intersects the diarization with the VAD, which removes speaker activity in regions
the gate calls silence. `--gate-diar` goes further and skips the diarization head entirely on files
with no detected speech.

Measured (docs/PAPER_FORCED_ALIGNMENT_FINDING.md §9b.4, 4 TED-LIUM talks, held out): the layer-31
probe reaches ROC-AUC 0.9532-0.9558 as a speech detector, against 0.9086 for reading
`diar_sidecar`'s own activity as a gate and 0.8905 for waveform RMS. Both numbers come from
single-speaker read speech and `diar_sidecar` is out of domain there, so this is a statement about
gating cost, not about diarization quality -- see §9b.4 before quoting it.
"""
from __future__ import annotations

import argparse, json, os, sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import soundfile as sf
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _p in ("/home/aref.farhadipour/aref-initial-diarizen-experiments-main",):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)          # model_sidecar needs `diarizen`

from audio_tower_export import export_audio_tower
from model_vad import FRAMES_PER_SECOND, load_vad, speech_ratio, to_segments

# audio_experts.CLUSTER_THRESHOLD is 0.38, tuned by 5-fold CV on AIR-Bench *speaker counting*, and
# its own docs warn the curve is knife-edged and must be re-validated per domain. It is wrong for
# diarization: on 5 VoxConverse files it merges far too aggressively. Swept here on those files --
#   0.38 -> DER 22.9%, speaker-count error 4.00
#   0.55 -> DER 19.1%, error 2.40
#   0.70 -> DER 14.9%, error 1.20      <- interior minimum in DER
#   0.80 -> DER 15.6%, error 0.80
# 0.70 minimises DER; raise it toward 0.80 if the speaker COUNT matters more than the boundaries.
# Five files is a thin basis: re-validate before trusting it on a new domain.
CLUSTER_THRESHOLD = 0.70

TAPS = [3, 7, 11, 15, 19, 23, 27, 31]
SR = 16000


def read_audio(path: str) -> np.ndarray:
    wav, sr = sf.read(path, dtype="float32", always_2d=False)
    if wav.ndim > 1:
        wav = wav.mean(1)
    if sr != SR:
        import scipy.signal as ss
        wav = ss.resample_poly(wav, SR, sr).astype(np.float32)
    return wav


def merge_tracks(taps: torch.Tensor, act: np.ndarray, up: int, asv_ckpt: str,
                 device, threshold: float, d_model: int, min_frames: int = 26):
    """Map over-segmented stitcher tracks onto global speakers with the trained ASV head.

    One embedding per track, pooled over the 13 Hz tap frames where that track is the dominant
    speaker, then average-linkage clustering on cosine similarity. Tracks too short to embed
    reliably are dropped rather than guessed at.
    """
    from model_speaker import MHFASpeakerHead

    ck = torch.load(asv_ckpt, map_location="cpu", weights_only=False)
    b = ck.get("args") or {}
    asv = MHFASpeakerHead(feature_dim=d_model, num_streams=taps.shape[1],
                          num_heads=int(b.get("num_heads", 8)),
                          compression_dim=int(b.get("compression_dim", 128)),
                          embedding_dim=int(b.get("embedding_dim", 256)),
                          dropout=0.0).to(device).eval()
    asv.load_state_dict(ck.get("head") or ck.get("model") or ck["state_dict"])

    dom = act.argmax(1)
    live = act.max(1) > 0.5
    embs, keep = [], []
    for t in range(act.shape[1]):
        fr = np.unique(np.flatnonzero((dom == t) & live) // up)     # 52 Hz -> 13 Hz
        fr = fr[fr < taps.shape[0]]
        if len(fr) < min_frames:
            continue
        with torch.inference_mode():
            e = asv(taps[fr].unsqueeze(0)).squeeze(0).float().cpu().numpy()
        embs.append(e / max(np.linalg.norm(e), 1e-9))
        keep.append(t)
    if not embs:
        return act, list(range(act.shape[1]))

    E = np.stack(embs)
    clusters = [[i] for i in range(len(E))]
    sim = E @ E.T
    while len(clusters) > 1:
        best, bi, bj = -2.0, -1, -1
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                v = float(np.mean([sim[x, y] for x in clusters[i] for y in clusters[j]]))
                if v > best:
                    best, bi, bj = v, i, j
        if best < threshold:
            break
        clusters[bi] += clusters[bj]
        clusters.pop(bj)

    merged = np.zeros((act.shape[0], len(clusters)), np.float32)
    for gi, grp in enumerate(clusters):
        cols = [keep[i] for i in grp]
        merged[:, gi] = act[:, cols].max(1)
    return merged, [[keep[i] for i in g] for g in clusters]


def activity_to_rttm(activity: np.ndarray, fps: float, uri: str,
                     threshold: float = 0.5, min_dur: float = 0.20) -> List[Dict]:
    """(T, n_speakers) activity -> per-speaker turns."""
    turns = []
    for spk in range(activity.shape[1]):
        on = activity[:, spk] >= threshold
        if not on.any():
            continue
        idx = np.flatnonzero(np.diff(np.r_[False, on, False]))
        for s, e in zip(idx[::2], idx[1::2]):
            if (e - s) / fps < min_dur:
                continue
            turns.append({"uri": uri, "speaker": f"spk{spk}",
                          "start": round(s / fps, 3), "end": round(e / fps, 3)})
    return sorted(turns, key=lambda t: t["start"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--encoder", default="", help="audio-tower dir; exported automatically if empty")
    ap.add_argument("--vad-ckpt", default=str(HERE / "checkpoints/vad_probe/best.pt"))
    ap.add_argument("--diar-ckpt", default=str(HERE / "checkpoints/diar_sidecar/best.pt"))
    ap.add_argument("--attn-mode", default="blockdiag", choices=["blockdiag", "global"])
    ap.add_argument("--threshold", type=float, default=None, help="override the stored VAD threshold")
    ap.add_argument("--min-speech", type=float, default=0.20)
    ap.add_argument("--min-silence", type=float, default=0.20)
    ap.add_argument("--no-diar", action="store_true", help="VAD only; skips the diarization head")
    ap.add_argument("--gate-diar", action="store_true", help="skip diarization if VAD finds no speech")
    ap.add_argument("--vad-refine", action="store_true", help="zero speaker activity outside speech")
    ap.add_argument("--no-cluster", action="store_true",
                    help="skip ASV track merging and emit raw stitcher tracks")
    ap.add_argument("--asv-ckpt", default=str(HERE / "checkpoints/asv/best.pt"))
    ap.add_argument("--cluster-threshold", type=float, default=CLUSTER_THRESHOLD)
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--rttm", default="")
    ap.add_argument("--json", dest="json_out", default="")
    a = ap.parse_args()

    from omni_encoder import FrozenOmniAudioEncoder

    device = torch.device(f"cuda:{a.gpu}" if torch.cuda.is_available() else "cpu")
    enc_path = a.encoder or str(export_audio_tower())
    enc = FrozenOmniAudioEncoder(enc_path, attn_mode=a.attn_mode,
                                 dtype=torch.float32).eval().to(device)

    wav = read_audio(a.audio)
    duration = len(wav) / SR
    mel = enc.mel_from_waveform(torch.from_numpy(wav).to(device))
    with torch.inference_mode():
        out = enc(mel, torch.tensor([mel.shape[-1]], device=device),
                  tap_layers=TAPS, return_llm_features=False)
    taps = out["taps"].float()                        # (T, 8, 1280) -- ONE pass, both heads
    n = taps.shape[0]

    # ---- VAD: one linear layer on the layer-31 tap
    vad, vck = load_vad(a.vad_ckpt, device)
    thr = a.threshold if a.threshold is not None else float(vck["args"].get("threshold", 0.5))
    prob = vad.probs(taps[:, TAPS.index(vad.layer)]).cpu().numpy()
    segments = to_segments(prob, thr, a.min_speech, a.min_silence)
    result = {
        "audio": a.audio, "duration": round(duration, 3), "frame_rate": FRAMES_PER_SECOND,
        "vad": {"layer": vad.layer, "threshold": thr, "n_segments": len(segments),
                "speech_ratio": speech_ratio(segments, duration), "segments": segments},
    }
    print(f"VAD  layer {vad.layer}, threshold {thr:.3f}: {len(segments)} segments, "
          f"{result['vad']['speech_ratio']:.1%} speech of {duration:.1f}s", flush=True)

    # ---- diarization: the trained sidecar on all 8 taps
    if not a.no_diar and not (a.gate_diar and not segments):
        from infer_timeline import diarize
        from model_sidecar import SpeakerSidecar
        ck = torch.load(a.diar_ckpt, map_location="cpu", weights_only=False)
        ar = ck.get("args") or {}
        diar = SpeakerSidecar(
            num_streams=len(TAPS), d_model=enc.d_model,
            adapter_dim=int(ar.get("adapter_dim", 128)),
            attention_in=int(ar.get("attention_in", 256)),
            conformer_layers=int(ar.get("conformer_layers", 4)),
            refine_layers=int(ar.get("refine_layers", 2)),
            upsample=int(ar.get("upsample", 4)),
            max_speakers_per_chunk=int(ar.get("max_speakers_per_chunk", 4)),
            max_speakers_per_frame=int(ar.get("max_speakers_per_frame", 2)),
            dropout=0.0).to(device).eval()
        diar.load_state_dict(ck.get("model") or ck.get("state_dict") or ck)
        up = diar.upsample
        with torch.inference_mode():
            act = diarize(diar, taps.cpu().numpy(), int(ar.get("chunk_size", 12)),
                          int(ar.get("chunk_shift", 6)), device)
        fps = FRAMES_PER_SECOND * up

        n_raw = act.shape[1]
        groups = None
        if not a.no_cluster and Path(a.asv_ckpt).exists():
            act, groups = merge_tracks(taps, act, up, a.asv_ckpt, device,
                                       a.cluster_threshold, enc.d_model)
            print(f"     stitcher tracks {n_raw} -> {act.shape[1]} speakers "
                  f"(ASV clustering, threshold {a.cluster_threshold})", flush=True)

        if a.vad_refine:
            keep = np.zeros(act.shape[0], bool)
            for s in segments:
                keep[int(s["start"] * fps): int(np.ceil(s["end"] * fps))] = True
            act = act * keep[:, None]

        turns = activity_to_rttm(act, fps, Path(a.audio).stem)
        spk = sorted({t["speaker"] for t in turns})
        result["diarization"] = {
            "frame_rate": fps, "n_speakers": len(spk), "vad_refined": bool(a.vad_refine),
            "raw_tracks": int(n_raw), "clustered": groups is not None,
            "cluster_threshold": None if groups is None else a.cluster_threshold,
            "val_der_of_head": ck.get("val", {}).get("der"), "turns": turns,
        }
        print(f"DIAR {len(spk)} speakers, {len(turns)} turns"
              f"{' (VAD-refined)' if a.vad_refine else ''}", flush=True)

        if a.rttm:
            with open(a.rttm, "w") as f:
                for t in turns:
                    f.write(f"SPEAKER {t['uri']} 1 {t['start']:.3f} "
                            f"{t['end']-t['start']:.3f} <NA> <NA> {t['speaker']} <NA> <NA>\n")
            print(f"wrote {a.rttm}", flush=True)
    elif a.gate_diar and not segments:
        print("DIAR skipped: VAD found no speech", flush=True)

    if a.json_out:
        Path(a.json_out).write_text(json.dumps(result, indent=2))
        print(f"wrote {a.json_out}", flush=True)
    elif not a.rttm:
        print(json.dumps({k: v for k, v in result.items() if k != "diarization"}, indent=2)[:900])


if __name__ == "__main__":
    main()
