"""Frozen Qwen3-Omni vision encoder with per-block taps (MHFA-compatible)."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import List, Sequence

import torch
import torch.nn as nn
import torch.nn.functional as F
from safetensors import safe_open
from transformers import AutoProcessor
from transformers.models.qwen3_omni_moe.configuration_qwen3_omni_moe import (
    Qwen3OmniMoeVisionEncoderConfig,
)
from transformers.models.qwen3_omni_moe.modeling_qwen3_omni_moe import Qwen3OmniMoeVisionEncoder

DEFAULT_MIX87_ENCODER_PATH = (
    "/data/d01/speech/tts/data/Qwen3Omni_Opt/checkpoints/mix87_ckpt54000-merged-MXFP4A16mse-fused"
)
DEFAULT_VISION_TAPS = [2, 5, 8, 11, 14, 17, 20, 23]  # 8 streams, depth 27


def _load_vision_state(path: str) -> dict:
    index_path = os.path.join(path, "model.safetensors.index.json")
    if not os.path.isfile(index_path):
        raise FileNotFoundError(f"No vision weights index at {path}")
    weight_map = json.load(open(index_path))["weight_map"]
    keys = [k for k in weight_map if k.startswith("thinker.visual.")]
    by_shard: dict = defaultdict(list)
    for k in keys:
        by_shard[weight_map[k]].append(k)
    state = {}
    for shard, ks in by_shard.items():
        with safe_open(os.path.join(path, shard), framework="pt") as f:
            for k in ks:
                state[k.split("thinker.visual.", 1)[-1]] = f.get_tensor(k)
    return state


class FrozenOmniVisionEncoder(nn.Module):
    def __init__(
        self,
        encoder_path: str = DEFAULT_MIX87_ENCODER_PATH,
        dtype: torch.dtype = torch.float32,
        lora_rank: int = 0,
        lora_alpha: float = 32.0,
    ):
        super().__init__()
        encoder_path = os.path.abspath(os.path.expanduser(encoder_path))
        self.encoder_path = encoder_path
        self.lora_rank = int(lora_rank)

        cfg = json.load(open(os.path.join(encoder_path, "config.json")))
        vcfg = Qwen3OmniMoeVisionEncoderConfig(**cfg["thinker_config"]["vision_config"])
        self.encoder = Qwen3OmniMoeVisionEncoder(vcfg)
        self.encoder.load_state_dict(_load_vision_state(encoder_path), strict=False)
        self.encoder.eval().to(dtype)
        for p in self.encoder.parameters():
            p.requires_grad = False

        self.lora_names: list[str] = []
        if self.lora_rank > 0:
            from omni_grounding.features.lora import inject_vision_lora, lora_parameters

            self.lora_names = inject_vision_lora(self.encoder, rank=self.lora_rank, alpha=lora_alpha)
            for p in lora_parameters(self.encoder):
                p.requires_grad = True
            self.encoder.gradient_checkpointing = True

        self.processor = AutoProcessor.from_pretrained(encoder_path, trust_remote_code=True)
        self.hidden_size = vcfg.hidden_size
        self.depth = vcfg.depth
        self.d_model = vcfg.hidden_size
        self.patch_size = int(getattr(vcfg, "patch_size", 16))

    def set_image_pixel_budget(self, min_pixels: int = 256 * 256, max_pixels: int = 512 * 512) -> None:
        """Cap Omni dynamic-resolution so localization batches stay trainable."""
        ip = getattr(self.processor, "image_processor", None)
        if ip is None:
            return
        if hasattr(ip, "min_pixels"):
            ip.min_pixels = min_pixels
        if hasattr(ip, "max_pixels"):
            ip.max_pixels = max_pixels

    def train(self, mode: bool = True):
        super().train(mode)
        # Base mix87 stays eval (frozen BN/dropout). LoRA adapters follow `mode`.
        self.encoder.eval()
        if self.lora_rank > 0:
            from omni_grounding.features.lora import LoRALinear

            for m in self.encoder.modules():
                if isinstance(m, LoRALinear):
                    m.train(mode)
        return self

    def preprocess_images(self, images: Sequence) -> tuple[torch.Tensor, torch.Tensor]:
        out = self.processor(
            text=["<|vision_start|><|image_pad|><|vision_end|>"] * len(images),
            images=list(images),
            return_tensors="pt",
            padding=True,
        )
        return out["pixel_values"], out["image_grid_thw"]

    def encode_pixels(
        self,
        pixel_values: torch.Tensor,
        grid_thw: torch.Tensor,
        tap_layers: Sequence[int],
        device: torch.device,
        out_dtype: torch.dtype = torch.float32,
    ) -> torch.Tensor:
        """Return (B, T, S, D) tapped block outputs before the final merger."""
        if self.lora_rank > 0 and self.training:
            return self._encode_pixels(pixel_values, grid_thw, tap_layers, device, out_dtype)
        with torch.no_grad():
            return self._encode_pixels(pixel_values, grid_thw, tap_layers, device, out_dtype)

    def _encode_pixels(
        self,
        pixel_values: torch.Tensor,
        grid_thw: torch.Tensor,
        tap_layers: Sequence[int],
        device: torch.device,
        out_dtype: torch.dtype = torch.float32,
    ) -> torch.Tensor:
        tap_set = set(tap_layers)
        hidden_states = pixel_values.to(device=device, dtype=next(self.encoder.parameters()).dtype)
        grid_thw = grid_thw.to(device)

        enc = self.encoder
        hidden_states = enc.patch_embed(hidden_states)
        pos_embeds = enc.fast_pos_embed_interpolate(grid_thw)
        hidden_states = hidden_states + pos_embeds

        rotary_pos_emb = enc.rot_pos_emb(grid_thw)
        seq_len, _ = hidden_states.size()
        hidden_states = hidden_states.reshape(seq_len, -1)
        rotary_pos_emb = rotary_pos_emb.reshape(seq_len, -1)
        emb = torch.cat((rotary_pos_emb, rotary_pos_emb), dim=-1)
        position_embeddings = (emb.cos(), emb.sin())

        cu_seqlens = torch.repeat_interleave(grid_thw[:, 1] * grid_thw[:, 2], grid_thw[:, 0]).cumsum(
            dim=0, dtype=torch.int32
        )
        cu_seqlens = F.pad(cu_seqlens, (1, 0), value=0)

        taps: List[torch.Tensor] = []
        use_ckpt = self.lora_rank > 0 and self.training
        cos, sin = position_embeddings
        for layer_num, blk in enumerate(enc.blocks):
            if use_ckpt:

                def _run(h, cu, c, s, block=blk):
                    return block(h, cu_seqlens=cu, position_embeddings=(c, s))

                hidden_states = torch.utils.checkpoint.checkpoint(
                    _run, hidden_states, cu_seqlens, cos, sin, use_reentrant=False
                )
            else:
                hidden_states = blk(
                    hidden_states,
                    cu_seqlens=cu_seqlens,
                    position_embeddings=position_embeddings,
                )
            if layer_num in tap_set:
                taps.append(hidden_states)

        lengths = cu_seqlens[1:] - cu_seqlens[:-1]
        if bool((lengths == lengths[0]).all()):
            # Equal-size images (fixed canvas): one reshape, no per-image padding copies.
            bsz, n = lengths.numel(), int(lengths[0])
            return torch.stack([t.view(bsz, n, -1) for t in taps], dim=2).to(out_dtype)
        max_t = int(lengths.max().item())
        stacked_layers = []
        for t in taps:
            pieces = []
            for i, ln in enumerate(lengths.tolist()):
                sl = t[int(cu_seqlens[i]) : int(cu_seqlens[i + 1])]
                pad = max_t - int(ln)
                if pad > 0:
                    sl = F.pad(sl, (0, 0, 0, pad))
                pieces.append(sl)
            stacked_layers.append(torch.stack(pieces, dim=0))
        return torch.stack(stacked_layers, dim=2).to(out_dtype)

    def encode_images(
        self,
        images: Sequence,
        tap_layers: Sequence[int],
        device: torch.device,
    ) -> torch.Tensor:
        pixel_values, grid_thw = self.preprocess_images(images)
        return self.encode_pixels(pixel_values, grid_thw, tap_layers, device)

    def encode_images_spatial(
        self,
        images: Sequence,
        tap_layers: Sequence[int],
        device: torch.device,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Tapped features (B, T, S, D) plus image_grid_thw (B, 3)."""
        pixel_values, grid_thw = self.preprocess_images(images)
        feats = self.encode_pixels(pixel_values, grid_thw, tap_layers, device)
        return feats, grid_thw.to(device)

    @torch.no_grad()
    def encode_merged(self, images: Sequence, device: torch.device):
        """Post-merger Omni visual tokens (2048-d), the same space the LLM reads.

        Returns per-image token tensors plus a mean-pooled region vector.
        """
        pixel_values, grid_thw = self.preprocess_images(images)
        dtype = next(self.encoder.parameters()).dtype
        merged, _deep = self.encoder(
            pixel_values.to(device=device, dtype=dtype),
            grid_thw.to(device),
        )
        merge = int(self.encoder.spatial_merge_size)
        grid_thw = grid_thw.to(device)
        lengths = (grid_thw[:, 0] * (grid_thw[:, 1] // merge) * (grid_thw[:, 2] // merge)).long()
        tokens, pooled = [], []
        offset = 0
        for n in lengths.tolist():
            sl = merged[offset : offset + n].float()
            offset += n
            tokens.append(sl)
            pooled.append(sl.mean(0) if sl.numel() else merged.new_zeros(merged.shape[-1]).float())
        return tokens, torch.stack(pooled, 0), grid_thw


def qwen_patchify(image, image_mean, image_std, patch_size: int = 16, temporal_patch_size: int = 2,
                  merge_size: int = 2) -> tuple[torch.Tensor, torch.Tensor]:
    """CPU-cheap Qwen2/3-VL image preprocessing for an RGB image whose sides are already
    multiples of patch_size*merge_size (no resize). Matches the HF processor's pixel_values
    and image_grid_thw for such inputs; use it in DataLoader workers."""
    import numpy as np

    arr = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    h, w = arr.shape[:2]
    unit = patch_size * merge_size
    if h % unit or w % unit:
        raise ValueError(f"image {w}x{h} not a multiple of {unit}")
    arr = (arr - np.asarray(image_mean, dtype=np.float32)) / np.asarray(image_std, dtype=np.float32)
    x = arr.transpose(2, 0, 1)[None].repeat(temporal_patch_size, axis=0)  # (T, C, H, W)
    gh, gw = h // patch_size, w // patch_size
    x = x.reshape(1, temporal_patch_size, 3, gh // merge_size, merge_size, patch_size,
                  gw // merge_size, merge_size, patch_size)
    x = x.transpose(0, 3, 6, 4, 7, 2, 1, 5, 8).reshape(gh * gw, 3 * temporal_patch_size * patch_size ** 2)
    return torch.from_numpy(np.ascontiguousarray(x)), torch.tensor([[1, gh, gw]])


def enable_batched_vision_sdpa(vision_encoder: nn.Module) -> int:
    """Patch the tower's attention so equal-length images run as one batched SDPA call.

    transformers' non-flash path loops over images (one eager attention per image per
    block); with a fixed 256x256 canvas every image has the same token count, so the
    loop collapses to a reshape. Unequal lengths fall back to the original forward.
    Returns the number of patched modules.
    """
    from transformers.models.qwen3_omni_moe.modeling_qwen3_omni_moe import (
        Qwen3OmniMoeVisionAttention,
        apply_rotary_pos_emb_vision,
    )

    def fast_forward(self, hidden_states, cu_seqlens, rotary_pos_emb=None, position_embeddings=None, **kw):
        lengths = cu_seqlens[1:] - cu_seqlens[:-1]
        if not bool((lengths == lengths[0]).all()):
            return self._orig_forward(hidden_states, cu_seqlens, rotary_pos_emb, position_embeddings, **kw)
        n, ln = hidden_states.shape[0], int(lengths[0])
        b = n // ln
        q, k, v = self.qkv(hidden_states).reshape(n, 3, self.num_heads, -1).permute(1, 0, 2, 3).unbind(0)
        cos, sin = position_embeddings
        q, k = apply_rotary_pos_emb_vision(q, k, cos, sin)
        q, k, v = (t.reshape(b, ln, self.num_heads, -1).transpose(1, 2) for t in (q, k, v))
        o = F.scaled_dot_product_attention(q, k, v, scale=self.scaling)
        return self.proj(o.transpose(1, 2).reshape(n, -1))

    count = 0
    for mod in vision_encoder.modules():
        if isinstance(mod, Qwen3OmniMoeVisionAttention) and not hasattr(mod, "_orig_forward"):
            mod._orig_forward = mod.forward
            mod.forward = fast_forward.__get__(mod)
            count += 1
    return count


def qwen_patchify_batch(images_u8: torch.Tensor, image_mean, image_std, patch_size: int = 16,
                        temporal_patch_size: int = 2, merge_size: int = 2) -> tuple[torch.Tensor, torch.Tensor]:
    """qwen_patchify for a (B, H, W, 3) uint8 batch of equal-size images, on any device.

    Lets DataLoader workers ship 8x smaller uint8 images and do the float work on the GPU.
    """
    b, h, w, _ = images_u8.shape
    x = images_u8.float().div_(255.0)
    mean = torch.as_tensor(image_mean, device=x.device, dtype=x.dtype)
    std = torch.as_tensor(image_std, device=x.device, dtype=x.dtype)
    x = ((x - mean) / std).permute(0, 3, 1, 2)  # (B, C, H, W)
    x = x[:, None].expand(b, temporal_patch_size, 3, h, w)
    gh, gw = h // patch_size, w // patch_size
    x = x.reshape(b, 1, temporal_patch_size, 3, gh // merge_size, merge_size, patch_size,
                  gw // merge_size, merge_size, patch_size)
    x = x.permute(0, 1, 4, 7, 5, 8, 3, 2, 6, 9).reshape(b * gh * gw, 3 * temporal_patch_size * patch_size ** 2)
    grid = torch.tensor([[1, gh, gw]] * b, device=x.device)
    return x.contiguous(), grid
