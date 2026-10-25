"""Audio sidecar experts for Qwen3-Omni — spoof detection and speaker counting.

Mirrors the vision contract in ``omni_grounding/headpose.py``: each expert loads a frozen
head on the frozen mix87 audio encoder, emits a **calibrated scalar plus its decision
threshold**, and abstains rather than guessing when it cannot see enough.

    experts = AudioExperts(device="cuda:0")
    experts.spoof(wav_path)          -> {'score': 3.1, 'decision': 'spoof', ...}
    experts.speaker_count(wav_path)  -> {'n_speakers': 2, 'n_windows': 11, ...}

    experts.align(wav_path)          -> {'words': [{'word','start','end','score'}...], ...}
    experts.locate(wav_path, "hate") -> {'spans': [{'start','end','score'}], ...} | None

``align`` / ``locate`` are the emergent forced aligner of the THINKER (LLM L42H21 + L22H4,
§9g decoder, forced_aligner.py) and therefore need the caller's loaded thinker:
``AudioExperts(device, thinker=thinker, processor=processor, load_heads=False)``.

Measured on AIR-Bench (see docs/audio_expert_sweep.md):

    Synthesized_Voice_Detection   head-only 91.20%  vs 55.10% for the LLM alone
    Speaker_Number_Verification   head-only 96.90%  vs 68.00% (majority baseline 73.20%)
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F

from model_antispoof import AntispoofModel
from model_speaker import MHFASpeakerHead
from omni_encoder import DEFAULT_ENCODER_PATH, FrozenOmniAudioEncoder

REPO = Path(__file__).resolve().parent
TAPS = [3, 7, 11, 15, 19, 23, 27, 31]
SR = 16000

# The spoof classifier's own decision boundary. NOT tuned on any benchmark: score > 0 means
# spoof, which is where the 2-way classifier was trained to separate. Using it as-is is what
# keeps the reported 91.20% free of benchmark fitting.
SPOOF_THRESHOLD = 0.0

# Agglomerative clustering threshold on ASV cosine similarity. Selected by 5-fold CV on
# AIR-Bench Speaker_Number_Verification; folds agreed on 0.38-0.40. The curve is sharply
# peaked (97% at 0.38, 50% at 0.60) so this constant matters - re-validate on new domains.
CLUSTER_THRESHOLD = 0.38

# Below this many windows the clustering has too little evidence to count speakers.
MIN_WINDOWS = 3


def read_wav(path: str | os.PathLike, max_seconds: float = 0.0) -> np.ndarray:
    w, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if w.ndim > 1:
        w = w.mean(1)
    if sr != SR:
        import scipy.signal as ss
        w = ss.resample_poly(w, SR, sr).astype(np.float32)
    if max_seconds and len(w) > int(max_seconds * SR):
        w = w[: int(max_seconds * SR)]
    return np.ascontiguousarray(w)


def agglomerative(embs: np.ndarray, thr: float) -> int:
    """Average-linkage clustering on cosine similarity; returns the cluster count."""
    n = len(embs)
    if n <= 1:
        return 1
    clusters = [[i] for i in range(n)]
    sim = embs @ embs.T
    while len(clusters) > 1:
        best, bi, bj = -2.0, -1, -1
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                s = float(np.mean([sim[a, b] for a in clusters[i] for b in clusters[j]]))
                if s > best:
                    best, bi, bj = s, i, j
        if best < thr:
            break
        clusters[bi] = clusters[bi] + clusters[bj]
        clusters.pop(bj)
    return len(clusters)


class AudioExperts:
    """Frozen audio heads sharing one encoder pass budget."""

    def __init__(self, device: str | torch.device = "cuda:0",
                 encoder_path: str = DEFAULT_ENCODER_PATH,
                 spoof_ckpt: str = "checkpoints/audio_antispoof/best.pt",
                 asv_ckpt: str = "checkpoints/asv/best.pt",
                 encoder: FrozenOmniAudioEncoder | None = None,
                 thinker=None, processor=None, load_heads: bool = True,
                 align_cfg=None):
        self.device = torch.device(device)
        self.amp = torch.bfloat16
        self._aligner = None
        if thinker is not None:
            import forced_aligner as fa
            self._aligner = fa.ThinkerAligner(thinker, processor, self.device,
                                              cfg=align_cfg or fa.TIMIT_BEST)
        if not load_heads:
            return
        if encoder is not None:
            self.encoder = encoder
        else:
            self.encoder = FrozenOmniAudioEncoder(encoder_path, dtype=torch.bfloat16)
            self.encoder.eval().to(self.device)
            for p in self.encoder.parameters():
                p.requires_grad = False

        ck = torch.load(REPO / spoof_ckpt, map_location="cpu", weights_only=False)
        a = ck.get("args") or {}
        self.spoof_model = AntispoofModel(
            feature_dim=self.encoder.d_model, num_streams=len(TAPS),
            num_heads=int(a.get("num_heads", 8)),
            compression_dim=int(a.get("compression_dim", 128)),
            embedding_dim=int(a.get("embedding_dim", 256)), dropout=0.0).to(self.device)
        self.spoof_model.load_state_dict(ck.get("model") or ck.get("state_dict") or ck)
        self.spoof_model.eval()

        ck = torch.load(REPO / asv_ckpt, map_location="cpu", weights_only=False)
        a = ck.get("args") or {}
        self.asv_head = MHFASpeakerHead(
            feature_dim=self.encoder.d_model, num_streams=len(TAPS),
            num_heads=int(a.get("num_heads", 8)),
            compression_dim=int(a.get("compression_dim", 128)),
            embedding_dim=int(a.get("embedding_dim", 256)), dropout=0.0).to(self.device)
        self.asv_head.load_state_dict(ck.get("head") or ck.get("model") or ck["state_dict"])
        self.asv_head.eval()

    # -- shared encoder pass ---------------------------------------------------
    @torch.no_grad()
    def _taps(self, wave: np.ndarray) -> torch.Tensor | None:
        """(1, T, n_taps, D) tapped hidden states, or None if the clip is too short."""
        if len(wave) < SR // 2:
            return None
        mel = self.encoder.mel_from_waveform(torch.from_numpy(wave).to(self.device))
        lens = torch.tensor([mel.shape[1]], device=self.device)
        with torch.autocast("cuda", dtype=self.amp, enabled=self.device.type == "cuda"):
            t = self.encoder(mel, lens, tap_layers=TAPS, return_llm_features=False)["taps"]
        return t.unsqueeze(0) if t.shape[0] >= 2 else None

    # -- experts ---------------------------------------------------------------
    @torch.no_grad()
    def spoof(self, path: str | os.PathLike, max_seconds: float = 12.0) -> dict | None:
        """Synthetic/replayed speech detection. Higher score = more likely spoof."""
        feats = self._taps(read_wav(path, max_seconds))
        if feats is None:
            return None                                   # abstain: clip too short
        lens = torch.tensor([feats.shape[1]], device=self.device, dtype=torch.long)
        s = float(self.spoof_model.spoof_score(feats.float(), lengths=lens).float().cpu()[0])
        return {"score": round(s, 3), "threshold": SPOOF_THRESHOLD,
                "decision": "spoof" if s > SPOOF_THRESHOLD else "bonafide"}

    @torch.no_grad()
    def embed(self, wave: np.ndarray) -> np.ndarray | None:
        feats = self._taps(wave)
        if feats is None:
            return None
        e = F.normalize(self.asv_head(feats.float()), dim=1)[0]
        return e.float().cpu().numpy()

    @torch.no_grad()
    def speaker_count(self, path: str | os.PathLike, win: float = 2.0, hop: float = 1.0,
                      thr: float = CLUSTER_THRESHOLD, max_speakers: int = 4) -> dict | None:
        """How many distinct voices, by clustering windowed ASV embeddings."""
        w = read_wav(path)
        ws, hs = int(win * SR), int(hop * SR)
        embs = []
        for s in range(0, max(len(w) - ws + 1, 1), hs):
            e = self.embed(w[s : s + ws])
            if e is not None:
                embs.append(e)
        if len(embs) < MIN_WINDOWS:
            return None                                   # abstain: too little evidence
        E = np.array(embs, dtype=np.float32)
        n = min(agglomerative(E, thr), max_speakers)
        # mean off-diagonal similarity is a useful confidence proxy for the readout
        sim = E @ E.T
        off = sim[~np.eye(len(E), dtype=bool)]
        return {"n_speakers": int(n), "n_windows": len(embs),
                "duration": round(len(w) / SR, 1),
                "mean_window_similarity": round(float(off.mean()), 3),
                "cluster_threshold": thr}

    # -- forced aligner (thinker attention; see forced_aligner.py) ----------------
    def _need_aligner(self):
        if self._aligner is None:
            raise RuntimeError("align()/locate() need the thinker: "
                               "AudioExperts(device, thinker=..., processor=...)")
        return self._aligner

    def align(self, wav, transcript: str | None = None, language: str | None = None) -> dict | None:
        """Forced alignment (spec docs/FORCED_ALIGNER_HEAD_SPEC.md §6). `wav` is a path or a
        16 kHz float array; transcript=None uses the thinker's own ASR. None = abstain."""
        w = read_wav(wav) if isinstance(wav, (str, os.PathLike)) else np.asarray(wav, np.float32)
        out = self._need_aligner().align(w, transcript)
        if out is not None:
            out["language"] = language
        return out

    def locate(self, wav, query: str, transcript: str | None = None,
               language: str | None = None) -> dict | None:
        """Word localisation: spans where `query` is spoken. None = abstain (absent / too short)."""
        import forced_aligner as fa
        return fa.locate_in_alignment(self.align(wav, transcript, language), query)

