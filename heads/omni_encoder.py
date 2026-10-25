"""Frozen Qwen3-Omni audio encoder with read-only per-layer taps.

The encoder graph is not modified in any way: no prompts are injected, no weights are
adapted, and the `ln_post -> proj1 -> proj2` output that Qwen3-Omni's LLM consumes is
produced by the same arithmetic as the stock model. A diarization head can therefore
consume the tapped hidden states in the *same* forward pass that feeds the LLM.

Two attention modes are supported, because `transformers` is inconsistent here:

  ``blockdiag``  Attention is restricted to consecutive windows of ``window_aftercnn``
                 (104) frames, which is what ``cu_seqlens`` means and what the
                 flash-attention-2 varlen kernel actually does. Every official
                 Qwen3-Omni cookbook loads the model with
                 ``attn_implementation='flash_attention_2'``, so this is the
                 deployment behaviour. It also makes features window-local, which is
                 what lets us cache them per recording and crop chunks freely.

  ``global``     Full bidirectional attention over the whole sequence. This is what
                 `Qwen3OmniMoeAudioEncoder.forward` actually does under sdpa/eager,
                 because it builds `cu_seqlens` but never passes an attention mask to
                 the layers (`_prepare_attention_mask` is defined and never called).
                 Kept mainly so the reimplementation can be validated bit-for-bit
                 against the stock module.

``self_test`` asserts that ``global`` reproduces the stock forward, which is what makes
the layer reimplementation trustworthy.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from safetensors.torch import load_file
from transformers import WhisperFeatureExtractor
from transformers.models.qwen3_omni_moe.configuration_qwen3_omni_moe import (
    Qwen3OmniMoeAudioEncoderConfig,
)
from transformers.models.qwen3_omni_moe.modeling_qwen3_omni_moe import (
    Qwen3OmniMoeAudioEncoder,
    _get_feat_extract_output_lengths,
)

from omni_frontend import MEL_CHUNK, WhisperLogMel

DEFAULT_ENCODER_PATH = (
    "/home/aref.farhadipour/Encode-explore/USM_FTcode/encoder_omni/qwen3_omni_audio_encoder_fp32"
)


class FrozenOmniAudioEncoder(nn.Module):
    def __init__(
        self,
        encoder_path: str = DEFAULT_ENCODER_PATH,
        attn_mode: str = "blockdiag",
        dtype: torch.dtype = torch.float32,
    ):
        super().__init__()
        if attn_mode not in ("blockdiag", "global"):
            raise ValueError(f"attn_mode must be 'blockdiag' or 'global', got {attn_mode}")
        self.attn_mode = attn_mode

        encoder_path = os.path.abspath(os.path.expanduser(encoder_path))
        self.feature_extractor = WhisperFeatureExtractor.from_pretrained(encoder_path)
        self.mel = WhisperLogMel.from_hf_extractor(self.feature_extractor)

        config = Qwen3OmniMoeAudioEncoderConfig.from_pretrained(encoder_path)
        config._attn_implementation = "sdpa"
        self.encoder = Qwen3OmniMoeAudioEncoder(config)
        state = load_file(os.path.join(encoder_path, "model.safetensors"))
        missing, unexpected = self.encoder.load_state_dict(state, strict=False)
        if missing:
            raise RuntimeError(f"Missing encoder weights: {missing}")
        del state

        self.encoder.eval().to(dtype)
        for p in self.encoder.parameters():
            p.requires_grad = False

        self.config = config
        self.num_layers = config.encoder_layers
        self.d_model = config.d_model
        self.output_dim = config.output_dim
        self.num_heads = config.encoder_attention_heads
        self.head_dim = self.d_model // self.num_heads
        self.n_window = config.n_window
        self.window_aftercnn = 13 * (config.n_window_infer // (config.n_window * 2))

    def train(self, mode: bool = True):
        super().train(mode)
        self.encoder.eval()
        return self

    # -- attention ---------------------------------------------------------
    def _attention(self, attn: nn.Module, x: torch.Tensor, block_lens: Optional[List[int]]):
        """x: (S, D) -> (S, D). Mirrors Qwen3OmniMoeAudioAttention.forward."""
        S, _ = x.shape
        q = attn.q_proj(x).reshape(S, self.num_heads, self.head_dim)
        k = attn.k_proj(x).reshape(S, self.num_heads, self.head_dim)
        v = attn.v_proj(x).reshape(S, self.num_heads, self.head_dim)

        if block_lens is None:
            # (1, H, S, Dh)
            out = F.scaled_dot_product_attention(
                q.transpose(0, 1).unsqueeze(0),
                k.transpose(0, 1).unsqueeze(0),
                v.transpose(0, 1).unsqueeze(0),
                scale=attn.scaling,
            )
            out = out.transpose(1, 2).reshape(S, -1)
        else:
            nb = len(block_lens)
            maxlen = max(block_lens)
            if min(block_lens) == maxlen:
                qb = q.view(nb, maxlen, self.num_heads, self.head_dim)
                kb = k.view(nb, maxlen, self.num_heads, self.head_dim)
                vb = v.view(nb, maxlen, self.num_heads, self.head_dim)
                mask = None
            else:
                split = lambda t: nn.utils.rnn.pad_sequence(
                    list(torch.split(t, block_lens, dim=0)), batch_first=True
                )
                qb, kb, vb = split(q), split(k), split(v)
                valid = torch.arange(maxlen, device=x.device)[None, :] < torch.tensor(
                    block_lens, device=x.device
                )[:, None]
                mask = valid[:, None, None, :]  # (nb, 1, 1, maxlen)
            out = F.scaled_dot_product_attention(
                qb.transpose(1, 2), kb.transpose(1, 2), vb.transpose(1, 2),
                attn_mask=mask, scale=attn.scaling,
            )
            out = out.transpose(1, 2).reshape(nb, maxlen, -1)
            if mask is None:
                out = out.reshape(S, -1)
            else:
                out = out[valid]  # row-major gather == cat of each block's valid rows
        return attn.out_proj(out)

    def _layer(self, layer: nn.Module, x: torch.Tensor, block_lens: Optional[List[int]]):
        x = x + self._attention(layer.self_attn, layer.self_attn_layer_norm(x), block_lens)
        h = layer.final_layer_norm(x)
        return x + layer.fc2(layer.activation_fn(layer.fc1(h)))

    # -- forward -----------------------------------------------------------
    def _conv_frontend(self, mel: torch.Tensor, feature_lens: torch.Tensor):
        """Chunked conv frontend, following Qwen3OmniMoeAudioEncoder.forward exactly."""
        enc = self.encoder
        aftercnn_lens = _get_feat_extract_output_lengths(feature_lens)
        chunk_num = torch.ceil(feature_lens / MEL_CHUNK).long()

        chunk_lengths = torch.full(
            (int(chunk_num.sum()),), MEL_CHUNK, dtype=torch.long, device=feature_lens.device
        )
        tail = F.pad(chunk_num, (1, 0), value=-1).cumsum(0)[1:]
        chunk_lengths[tail] = feature_lens % MEL_CHUNK
        chunk_lengths[chunk_lengths == 0] = MEL_CHUNK

        chunks = mel.T.split(chunk_lengths.tolist(), dim=0)
        padded = nn.utils.rnn.pad_sequence(chunks, batch_first=True).transpose(1, 2).unsqueeze(1)
        lens_after_cnn = _get_feat_extract_output_lengths(chunk_lengths)
        mask_after_cnn = nn.utils.rnn.pad_sequence(
            [torch.ones(int(n), dtype=torch.bool, device=padded.device) for n in lens_after_cnn],
            batch_first=True,
        )

        embeds = []
        for chunk in padded.split(enc.conv_chunksize, dim=0):
            e = F.gelu(enc.conv2d1(chunk))
            e = F.gelu(enc.conv2d2(e))
            e = F.gelu(enc.conv2d3(e))
            embeds.append(e)
        e = torch.cat(embeds, dim=0)
        b, c, f, t = e.size()
        e = enc.conv_out(e.permute(0, 3, 1, 2).contiguous().view(b, t, c * f))
        e = e + enc.positional_embedding.positional_embedding[:t, :].unsqueeze(0).to(e.dtype)

        hidden = e[mask_after_cnn]  # (S, D), padding removed
        window = int(mask_after_cnn.shape[-1]) * (enc.n_window_infer // MEL_CHUNK)
        block_lens: List[int] = []
        for n in aftercnn_lens.tolist():
            block_lens += [window] * (n // window)
            if n % window:
                block_lens.append(n % window)
        return hidden, block_lens, aftercnn_lens

    @torch.no_grad()
    def forward(
        self,
        mel: torch.Tensor,
        feature_lens: torch.Tensor,
        tap_layers: Optional[Sequence[int]] = None,
        return_llm_features: bool = True,
    ) -> Dict[str, object]:
        """mel: (n_mels, S_total) with per-utterance lengths in ``feature_lens``.

        Returns ``taps`` (T, len(tap_layers), d_model) and, if requested,
        ``llm_features`` (T, output_dim) -- the tensor Qwen3-Omni scatters into its
        audio-token positions.
        """
        enc = self.encoder
        hidden, block_lens, aftercnn_lens = self._conv_frontend(mel, feature_lens)
        blocks = block_lens if self.attn_mode == "blockdiag" else None

        wanted = set(range(self.num_layers) if tap_layers is None else tap_layers)
        # Without llm_features nothing above the deepest tap is needed, so stop there: this is
        # what makes a head that reads only the first L layers cost L/num_layers of the tower.
        last = max(wanted) if wanted and not return_llm_features else self.num_layers - 1
        taps: List[torch.Tensor] = []
        for i, layer in enumerate(enc.layers):
            if i > last:
                break
            hidden = self._layer(layer, hidden, blocks)
            if i in wanted:
                taps.append(hidden)

        out: Dict[str, object] = {
            "taps": torch.stack(taps, dim=1) if taps else None,  # (S, n_taps, D)
            "aftercnn_lens": aftercnn_lens,
        }
        if return_llm_features:
            h = enc.ln_post(hidden)
            out["llm_features"] = enc.proj2(enc.act(enc.proj1(h)))
        return out

    # -- helpers -----------------------------------------------------------
    @torch.no_grad()
    def mel_from_waveform(self, waveform: torch.Tensor) -> torch.Tensor:
        """(T,) or (1, T) waveform -> (n_mels, S) log-mel on the same device."""
        return self.mel(waveform)[0]

    def encode_waveform_batch(self, waveforms: torch.Tensor, tap_layers: Sequence[int]) -> torch.Tensor:
        """(B, T) equal-length waveforms -> (B, S', len(tap_layers), d_model) tapped features.

        The batch is concatenated along time with one block per item, so block-diagonal
        attention keeps the items independent. Used by train_asv.py / train_spoof_audio.py;
        restored from the version those scripts were written against.
        """
        mel = self.mel(waveforms)
        B, n_mels, S = mel.shape
        flat = mel.permute(1, 0, 2).reshape(n_mels, B * S)
        lens = torch.full((B,), S, dtype=torch.long, device=mel.device)
        taps = self.forward(flat, lens, tap_layers, False)["taps"]
        return taps.view(B, -1, len(tap_layers), self.d_model)

    @torch.no_grad()
    def self_test(self, seconds: float = 6.0, tol: float = 2e-4) -> None:
        """Assert the reimplemented layer stack reproduces the stock HF forward."""
        device = next(self.encoder.parameters()).device
        n_mel = int(seconds * 100)
        mel = torch.randn(self.config.num_mel_bins, n_mel, device=device)
        lens = torch.tensor([n_mel], device=device)

        ref = self.encoder(mel, feature_lens=lens).last_hidden_state
        saved, self.attn_mode = self.attn_mode, "global"
        got = self.forward(mel, lens, tap_layers=[])["llm_features"]
        self.attn_mode = saved

        if ref.shape != got.shape:
            raise AssertionError(f"shape mismatch: stock {tuple(ref.shape)} vs ours {tuple(got.shape)}")
        err = (ref - got).abs().max().item()
        if err > tol:
            raise AssertionError(f"reimplementation diverges from stock forward: max abs err {err:.3e}")
        print(f"  encoder self-test OK (max abs err {err:.2e} vs stock forward, {ref.shape[0]} frames)")
