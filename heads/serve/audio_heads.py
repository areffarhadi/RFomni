"""Audio heads on one shared, frozen mix87 audio tower.

Speech regions come from Silero VAD (vendored, CPU), not the mix87 VAD probe. One bf16
tower pass over the recording feeds diarization (taps 3,7,...,31); the per-segment heads (voice gender, spoof, phonemes, end of turn) encode their own segment,
because they were trained on clips encoded on their own (the tower attends in 8 s blocks
counted from the clip start).

Input everywhere: float32 mono 16 kHz. All times in the output are seconds from the start
of the audio that was sent.
"""
import json

import numpy as np
import torch
import torch.nn.functional as F

from . import compat  # noqa: F401  (sys.path)

SR = 16000
TAPS8 = [3, 7, 11, 15, 19, 23, 27, 31]
ALL32 = list(range(32))
CHUNK_S = 296           # long audio is encoded in pieces of 37 x 8 s blocks (~2.5 GB peak each)
PHONE_MAX_S = 20.0      # the phoneme head was trained on 1-20 s clips
TURN_S = 8.0            # the end-of-turn head reads the last 8 s of a turn


def _r(x):
    return round(float(x), 3)


class AudioHeads:
    def __init__(self, tower: str, device: torch.device):
        from omni_encoder import FrozenOmniAudioEncoder
        from model_sidecar import SpeakerSidecar
        from silero_vad import load_silero_vad
        from audio_experts import AudioExperts
        from phoneme_heads import build_head
        from phoneme_sidecar import _load_tokens
        from smart_turn_sidecar import SmartTurnSidecar

        repo = compat.REPO
        self.dev = device
        self.enc = FrozenOmniAudioEncoder(tower, attn_mode="blockdiag", dtype=torch.float32).eval().to(device)

        self.silero = load_silero_vad()  # TorchScript, runs on the CPU

        ck = torch.load(repo / "checkpoints/diar_sidecar/best.pt", map_location="cpu", weights_only=False)
        a = self.diar_args = ck["args"]
        self.diar = SpeakerSidecar(
            num_streams=8, d_model=1280, adapter_dim=a["adapter_dim"], attention_in=a["attention_in"],
            conformer_layers=a["conformer_layers"], refine_layers=a["refine_layers"], upsample=a["upsample"],
            max_speakers_per_chunk=a["max_speakers_per_chunk"], max_speakers_per_frame=a["max_speakers_per_frame"],
            dropout=0.0).to(device).eval()
        self.diar.load_state_dict(ck["model"])

        # ASV + audio spoof heads, sharing the tower (ASV also clusters diarization tracks)
        self.ex = AudioExperts(device=device, encoder=self.enc)
        g = json.loads((repo / "checkpoints/voice_gender/probe.json").read_text())
        self.g_classes = g["classes"]
        self.g_mu, self.g_sd, self.g_w = (np.array(g[k], np.float32) for k in ("mu", "sd", "w"))

        st = torch.load(repo / "checkpoints/phoneme/tf4_lstm1_k3_wb.pt", map_location="cpu", weights_only=False)
        self.phone = build_head(st["config"]).to(device).eval()
        self.phone.load_state_dict(st["state_dict"])
        self.tokens = _load_tokens(str(repo / "checkpoints/phoneme/tokens.txt"))

        self.turn = SmartTurnSidecar(encoder_path=tower, device=device, standalone=False)

    # ------------------------------------------------------------------ encoder
    @torch.inference_mode()
    def encode(self, wav: np.ndarray, taps=ALL32) -> tuple:
        """(T, len(taps), 1280) bf16 taps at 13 Hz and the after-CNN lengths."""
        mel = self.enc.mel_from_waveform(torch.from_numpy(np.ascontiguousarray(wav)).to(self.dev))
        with torch.autocast("cuda", dtype=torch.bfloat16):
            out = self.enc(mel, torch.tensor([mel.shape[-1]], device=self.dev), tap_layers=taps,
                           return_llm_features=False)
        return out["taps"], out["aftercnn_lens"]

    def encode_long(self, wav: np.ndarray) -> torch.Tensor:
        """Whole recording in 296 s pieces (block-aligned, so the pieces join cleanly)."""
        step = CHUNK_S * SR
        parts = [self.encode(wav[s:s + step], TAPS8)[0] for s in range(0, len(wav), step)
                 if len(wav[s:s + step]) >= SR // 2]
        return torch.cat(parts, 0) if parts else torch.zeros(0, len(TAPS8), 1280, device=self.dev)

    # ------------------------------------------------------------------ heads
    @torch.inference_mode()
    def vad_segments(self, wav: np.ndarray, threshold: float = 0.5, min_speech: float = 0.25,
                     min_silence: float = 0.2, pad: float = 0.03) -> list:
        """Silero VAD speech regions in seconds, each with its mean speech probability.
        All 32 ms windows are scored in one audio_forward call, then Silero's own
        get_speech_timestamps_from_probs turns them into regions."""
        from silero_vad import get_speech_timestamps_from_probs
        if len(wav) < 512:
            return []
        self.silero.reset_states()
        probs = self.silero.audio_forward(torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32)), SR)[0]
        probs = probs.float().cpu().numpy()
        ts = get_speech_timestamps_from_probs(probs.tolist(), sampling_rate=SR, threshold=threshold,
                                              min_speech_duration_ms=int(min_speech * 1000),
                                              min_silence_duration_ms=int(min_silence * 1000),
                                              speech_pad_ms=int(pad * 1000), audio_length_samples=len(wav))
        win = 512
        return [{"start": _r(t["start"] / SR), "end": _r(t["end"] / SR),
                 "score": _r(probs[t["start"] // win: max(t["start"] // win + 1, t["end"] // win)].mean())}
                for t in ts]

    @torch.inference_mode()
    def _embed_frames(self, taps8: torch.Tensor, frames: np.ndarray) -> np.ndarray:
        e = self.ex.asv_head(taps8[frames].unsqueeze(0).float())
        e = F.normalize(e, dim=1)[0]
        return e.float().cpu().numpy()

    def diarize(self, taps8: torch.Tensor, threshold: float = 0.70, min_frames: int = 26,
                assign_frames: int = 6, speech=None, num_speakers: int | None = None) -> list:
        """Speaker turns. The sidecar gives chunk-local tracks; the ASV head groups them into
        global speakers (average linkage on cosine). Unlike the repo's merge, tracks too short
        to cluster reliably are attached to their nearest speaker instead of being dropped, and
        frames the VAD calls speech (`speech`: [{start, end}]) where no speaker reaches 0.5 go
        to the most active speaker, so speech is not left without a speaker. With num_speakers
        the clustering merges down to exactly that many speakers instead of stopping at the
        threshold."""
        from infer_timeline import diarize
        from infer_vad_diar import activity_to_rttm

        a, up = self.diar_args, self.diar.upsample
        with torch.inference_mode():
            act = diarize(self.diar, taps8.float().cpu().numpy(), a["chunk_size"], a["chunk_shift"], self.dev)
        if act.size == 0 or act.shape[1] == 0:
            return []
        dom, live = act.argmax(1), act.max(1) > 0.5
        embs, size = {}, {}
        for t in range(act.shape[1]):
            fr = np.unique(np.flatnonzero((dom == t) & live) // up)
            fr = fr[fr < taps8.shape[0]]
            if len(fr) >= assign_frames:
                embs[t], size[t] = self._embed_frames(taps8, fr), len(fr)
        if not embs:
            return []
        reliable = [t for t in embs if size[t] >= min_frames] or [max(size, key=size.get)]
        clusters = [[t] for t in reliable]
        while len(clusters) > 1:
            best, bi, bj = -2.0, -1, -1
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    v = float(np.mean([embs[x] @ embs[y] for x in clusters[i] for y in clusters[j]]))
                    if v > best:
                        best, bi, bj = v, i, j
            if (best < threshold and not num_speakers) or (num_speakers and len(clusters) <= num_speakers):
                break
            clusters[bi] += clusters.pop(bj)
        cent = [F.normalize(torch.tensor(np.mean([embs[t] for t in c], 0)), dim=0).numpy() for c in clusters]
        for t in embs:
            if t not in reliable:
                clusters[int(np.argmax([c @ embs[t] for c in cent]))].append(t)
        merged = np.stack([act[:, c].max(1) for c in clusters], 1)
        fps = 13.0 * up
        for seg in speech or []:
            a, b = int(seg["start"] * fps), min(int(seg["end"] * fps), len(merged))
            if b <= a:
                continue
            block = merged[a:b]
            idle = block.max(1) < 0.5
            block[idle, block[idle].argmax(1)] = 0.5  # activity_to_rttm's onset
        turns = activity_to_rttm(merged, fps, "x")
        order = {}  # SPEAKER_0 is whoever talks first
        for tt in turns:
            order.setdefault(tt["speaker"], f"SPEAKER_{len(order)}")
        return [{"start": _r(t["start"]), "end": _r(t["end"]), "speaker": order[t["speaker"]]} for t in turns]

    def gender(self, wav: np.ndarray):
        e = self.ex.embed(wav[: 12 * SR])
        if e is None:
            return None
        z = np.r_[(e - self.g_mu) / self.g_sd, 1.0] @ self.g_w
        p = np.exp(z - z.max())
        p /= p.sum()
        return {"label": self.g_classes[int(p.argmax())], "confidence": _r(p.max())}

    @torch.inference_mode()
    def spoof(self, wav: np.ndarray):
        from audio_experts import SPOOF_THRESHOLD
        feats = self.ex._taps(wav[: 12 * SR])
        if feats is None:
            return None
        lens = torch.tensor([feats.shape[1]], device=self.dev, dtype=torch.long)
        s = float(self.ex.spoof_model.spoof_score(feats.float(), lengths=lens).float().cpu()[0])
        return {"label": "spoof" if s > SPOOF_THRESHOLD else "bonafide", "score": _r(s),
                "confidence": _r(1 / (1 + np.exp(-abs(s - SPOOF_THRESHOLD))))}

    def speaker_count(self, wav: np.ndarray, max_speakers: int = 6):
        from audio_experts import CLUSTER_THRESHOLD, MIN_WINDOWS, agglomerative
        win = 2 * SR
        hop = max(SR, (len(wav) - win) // 400)  # at most ~400 windows on long files
        embs = [e for s in range(0, max(len(wav) - win + 1, 1), hop)
                if (e := self.ex.embed(wav[s:s + win])) is not None]
        if len(embs) < MIN_WINDOWS:
            return None
        n = min(agglomerative(np.array(embs, np.float32), CLUSTER_THRESHOLD), max_speakers)
        return {"n_speakers": int(n), "n_windows": len(embs)}

    @torch.inference_mode()
    def phonemes(self, wav: np.ndarray, t0: float = 0.0) -> list:
        """Phones with times from the CTC emissions (39 Hz). '|' word boundaries are skipped."""
        from omni_frontend import upsampled_frame_centres
        out = []
        step = int(PHONE_MAX_S * SR)
        for s in range(0, len(wav), step):
            piece = wav[s:s + step]
            if len(piece) < SR // 2:
                continue
            taps, _ = self.encode(piece, ALL32)
            feats = taps.to(torch.bfloat16).permute(1, 0, 2).unsqueeze(1).contiguous()
            valid = torch.ones(1, feats.shape[2], dtype=torch.bool, device=self.dev)
            logits, ov = self.phone(feats, valid)
            am = logits[0].argmax(-1).cpu().numpy()
            n = int(ov[0].sum()) if ov is not None else len(am)
            tc = upsampled_frame_centres(feats.shape[2], self.phone.k)
            off = t0 + s / SR
            emits, prev = [], 0
            for i in range(min(n, len(am))):
                if am[i] != prev and am[i] != 0:
                    emits.append((float(tc[i]) + off, int(am[i])))
                prev = am[i]
            for j, (t, tok) in enumerate(emits):
                if self.tokens[tok] == "▁":
                    continue
                end = emits[j + 1][0] if j + 1 < len(emits) else t + 0.12
                out.append({"start": _r(t), "end": _r(min(end, t + 0.3)), "value": self.tokens[tok]})
        return out

    @torch.inference_mode()
    def end_of_turn(self, wav: np.ndarray):
        w = wav[-int(TURN_S * SR):]
        if len(w) < SR // 2:
            return None
        taps, lens = self.encode(w, self.turn.taps)
        p = float(self.turn.from_taps(taps, lens).float().cpu()[0])
        return {"label": "complete" if p > 0.5 else "incomplete", "confidence": _r(max(p, 1 - p)), "p_complete": _r(p)}

    # ------------------------------------------------------------------ request
    def analyze(self, wav: np.ndarray, heads: list, segments=None, options=None) -> dict:
        """heads: vad, diarization, gender, spoof, speaker_count, phonemes, end_of_turn.
        Per-segment heads run on `segments` ([[start, end], ...] in seconds) or, if none are
        given, on the VAD segments."""
        o = options or {}
        out, heads = {"duration": _r(len(wav) / SR)}, set(heads)
        if heads & {"vad", "diarization"} or (segments is None and heads & {"gender", "spoof", "phonemes", "end_of_turn"}):
            out["vad"] = self.vad_segments(wav, float(o.get("vad_threshold", 0.5)),
                                           min_silence=float(o.get("vad_min_silence", 0.2)))
        if "diarization" in heads:
            # diar_fill_vad / diar_assign_frames exist for ablations: False / 26 reproduce the repo's merge
            out["diarization"] = self.diarize(self.encode_long(wav), float(o.get("diar_threshold", 0.70)),
                                              assign_frames=int(o.get("diar_assign_frames", 6)),
                                              num_speakers=int(o["num_speakers"]) if o.get("num_speakers") else None,
                                              speech=out["vad"] if o.get("diar_fill_vad", True) else None)
        if "speaker_count" in heads:
            out["speaker_count"] = self.speaker_count(wav)
        spans = segments if segments is not None else [(s["start"], s["end"]) for s in out.get("vad", [])]
        for name, fn in (("gender", self.gender), ("spoof", self.spoof), ("end_of_turn", self.end_of_turn)):
            if name in heads:
                res = []
                for a, b in spans:
                    r = fn(wav[int(a * SR):int(b * SR)])
                    if r:
                        res.append({"start": _r(a), "end": _r(b), **r})
                out[name] = res
        if "phonemes" in heads:
            out["phonemes"] = [p for a, b in spans for p in self.phonemes(wav[int(a * SR):int(b * SR)], a)]
        if "vad" not in heads:
            out.pop("vad", None)
        return out
