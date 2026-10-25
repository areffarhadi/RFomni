"""Phoneme CTC sidecar on the frozen mix87 audio tower.

Reads all 32 tower layers (13 frames/s, d=1280), upsamples, greedy-CTC decodes
the ZIPA 127-token vocabulary. Word-boundary token 3 (▁) is printed as ``|``.

    from phoneme_sidecar import PhonemeSidecar
    ph = PhonemeSidecar(device="cuda:0")          # checkpoints/phoneme/tf4_lstm1_k3_wb.pt
    out = ph.predict(wave_16k)                    # {"phones": "h ɛ l oʊ | w ɝ l d", "ipa": "..."}
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence, Union

import numpy as np
import torch
import torch.nn as nn

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "phoneme"))
from omni_phoneme_encoder import OmniStreams  # noqa: E402
from phoneme_heads import build_head, greedy  # noqa: E402

CKPT = REPO / "checkpoints" / "phoneme" / "tf4_lstm1_k3_wb.pt"
TOKENS_PATH = REPO / "checkpoints" / "phoneme" / "tokens.txt"
SR = 16000
WB_PRINT = "|"


def _load_tokens(path: Path) -> list[str]:
    return [line.split()[0] for line in open(path, encoding="utf-8")]


class PhonemeSidecar(nn.Module):
    def __init__(self, ckpt: Union[str, Path] = CKPT, encoder_path: str = "",
                 tokens: Union[str, Path] = TOKENS_PATH,
                 device: Union[str, torch.device] = "cuda:0"):
        super().__init__()
        self.device = torch.device(device)
        st = torch.load(ckpt, map_location="cpu", weights_only=False)
        self.head = build_head(st["config"]).to(self.device).eval()
        self.head.load_state_dict(st["state_dict"])
        self.tokens = _load_tokens(Path(tokens))
        tower = encoder_path or os.environ.get("OMNI_AUDIO_TOWER", "")
        self.encoder = (OmniStreams(tower) if tower else OmniStreams()).to(self.device)
        for p in self.parameters():
            p.requires_grad = False

    def _decode_ids(self, ids: Sequence[int]) -> dict:
        raw = [self.tokens[t] if 0 <= t < len(self.tokens) else "<unk>" for t in ids]
        phones = [WB_PRINT if t == "▁" else t for t in raw]
        words, cur = [], []
        for t in phones:
            if t == WB_PRINT:
                if cur:
                    words.append("".join(cur))
                    cur = []
            else:
                cur.append(t)
        if cur:
            words.append("".join(cur))
        return {"phones": " ".join(phones), "ipa": " ".join(words), "ids": list(ids)}

    @torch.no_grad()
    def predict(self, wave: Union[np.ndarray, torch.Tensor], sr: int = SR) -> dict:
        """One 16 kHz mono clip (float in [-1, 1]) -> phones / ipa / ids."""
        w = torch.as_tensor(np.asarray(wave, dtype=np.float32).reshape(-1))
        if sr != SR:
            raise ValueError(f"expected {SR} Hz, got {sr}")
        x = w.unsqueeze(0).to(self.device)
        lens = torch.tensor([w.numel()], device=self.device)
        feats, valid = self.encoder(x, lens)
        ids = greedy(*self.head(feats, valid))[0]
        return self._decode_ids(ids)

    @torch.no_grad()
    def predict_batch(self, waves: Sequence[Union[np.ndarray, torch.Tensor]]) -> list:
        xs, lens = [], []
        for w in waves:
            t = torch.as_tensor(np.asarray(w, dtype=np.float32).reshape(-1))
            xs.append(t)
            lens.append(t.numel())
        n = max(lens)
        x = torch.zeros(len(xs), n)
        for i, t in enumerate(xs):
            x[i, : t.numel()] = t
        x = x.to(self.device)
        lens_t = torch.tensor(lens, device=self.device)
        feats, valid = self.encoder(x, lens_t)
        hyps = greedy(*self.head(feats, valid))
        return [self._decode_ids(h) for h in hyps]


def main():
    import argparse
    import soundfile as sf

    ap = argparse.ArgumentParser(description="Phoneme CTC sidecar")
    ap.add_argument("audio")
    ap.add_argument("--ckpt", default=str(CKPT))
    ap.add_argument("--device", default="cuda:0")
    a = ap.parse_args()
    w, sr = sf.read(a.audio, dtype="float32", always_2d=True)
    w = w.mean(1)
    if sr != SR:
        import torchaudio
        w = torchaudio.functional.resample(torch.from_numpy(w), sr, SR).numpy()
    ph = PhonemeSidecar(ckpt=a.ckpt, device=a.device)
    out = ph.predict(w)
    print(out["phones"])
    print(out["ipa"])


if __name__ == "__main__":
    main()
