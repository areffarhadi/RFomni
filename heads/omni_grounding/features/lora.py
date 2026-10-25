"""Tiny LoRA wrappers. No peft dependency; mix87 base weights stay frozen on disk."""

from __future__ import annotations

import math

import torch
import torch.nn as nn



class LoRALinear(nn.Module):
    """y = frozen_linear(x) + (alpha/r) B(A(x)). Base weights are never updated."""

    def __init__(self, base: nn.Linear, rank: int = 16, alpha: float = 32.0, dropout: float = 0.0):
        super().__init__()
        if not isinstance(base, nn.Linear):
            raise TypeError(f"LoRALinear expects nn.Linear, got {type(base)}")
        self.base = base
        for p in self.base.parameters():
            p.requires_grad = False
        self.rank = rank
        self.scale = alpha / rank
        self.lora_drop = nn.Dropout(dropout) if dropout else nn.Identity()
        self.lora_A = nn.Linear(base.in_features, rank, bias=False)
        self.lora_B = nn.Linear(rank, base.out_features, bias=False)
        nn.init.kaiming_uniform_(self.lora_A.weight, a=math.sqrt(5))
        nn.init.zeros_(self.lora_B.weight)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.base(x) + self.lora_B(self.lora_A(self.lora_drop(x))) * self.scale


def inject_vision_lora(encoder: nn.Module, rank: int = 16, alpha: float = 32.0) -> list[str]:
    """Attach LoRA to mix87 vision blocks: attn qkv/proj and MLP fc1/fc2."""
    replaced = []
    blocks = getattr(encoder, "blocks", None)
    if blocks is None:
        raise ValueError("encoder has no .blocks")
    for i, blk in enumerate(blocks):
        for path in ("attn.qkv", "attn.proj", "mlp.linear_fc1", "mlp.linear_fc2"):
            parent = blk
            *head, leaf = path.split(".")
            for h in head:
                parent = getattr(parent, h)
            mod = getattr(parent, leaf)
            if isinstance(mod, nn.Linear):
                setattr(parent, leaf, LoRALinear(mod, rank=rank, alpha=alpha))
                replaced.append(f"blocks.{i}.{path}")
    return replaced


def lora_parameters(encoder: nn.Module):
    for m in encoder.modules():
        if isinstance(m, LoRALinear):
            yield from m.lora_A.parameters()
            yield from m.lora_B.parameters()


def lora_state_dict(encoder: nn.Module) -> dict:
    out = {}
    for name, mod in encoder.named_modules():
        if isinstance(mod, LoRALinear):
            out[f"{name}.lora_A.weight"] = mod.lora_A.weight.detach().cpu()
            out[f"{name}.lora_B.weight"] = mod.lora_B.weight.detach().cpu()
    return out


def load_lora_state_dict(encoder: nn.Module, sd: dict) -> int:
    n = 0
    own = {k: v for k, v in encoder.named_modules() if isinstance(v, LoRALinear)}
    for key, tensor in sd.items():
        name = key.replace(".lora_A.weight", "").replace(".lora_B.weight", "")
        if name not in own:
            continue
        if key.endswith("lora_A.weight"):
            own[name].lora_A.weight.data.copy_(tensor)
        else:
            own[name].lora_B.weight.data.copy_(tensor)
        n += 1
    return n
