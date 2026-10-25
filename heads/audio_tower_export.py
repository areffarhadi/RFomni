"""Export the Qwen3-Omni audio tower to the single-file layout FrozenOmniAudioEncoder expects.

The README says to point `ENCODER_PATH` at `mix87_ckpt54000-merged-MXFP4A16mse-fused`, but
`FrozenOmniAudioEncoder` loads one `model.safetensors` of bare encoder keys while that checkpoint is
15 shards with `thinker.audio_tower.` prefixes -- so pointing at it directly raises FileNotFoundError.
This extracts the tower once into a directory that does load.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_FUSED = os.environ.get(
    "OMNI_CHECKPOINT",
    "/data/d01/speech/tts/data/Qwen3Omni_Opt/checkpoints/mix87_ckpt54000-merged-MXFP4A16mse-fused",
)
DEFAULT_EXPORT = Path(
    os.environ.get("OMNI_AUDIO_TOWER",
                   str(Path(__file__).resolve().parent / "checkpoints/mix87_audio_tower"))
)
PREFIX = "thinker.audio_tower."


def export_audio_tower(checkpoint: str = DEFAULT_FUSED,
                       out_dir: Path | str = DEFAULT_EXPORT,
                       force: bool = False) -> Path:
    """Return a directory holding `model.safetensors` + `config.json` for the audio tower."""
    import torch
    from safetensors.torch import load_file, save_file

    out_dir = Path(out_dir)
    if (out_dir / "model.safetensors").exists() and not force:
        return out_dir
    ckpt = Path(checkpoint)
    out_dir.mkdir(parents=True, exist_ok=True)

    index = ckpt / "model.safetensors.index.json"
    if index.exists():
        wmap = json.loads(index.read_text())["weight_map"]
        shards = sorted({v for k, v in wmap.items() if k.startswith(PREFIX)})
    else:
        shards = ["model.safetensors"]

    tensors = {}
    for shard in shards:
        st = load_file(str(ckpt / shard))
        for k, v in st.items():
            if k.startswith(PREFIX):
                tensors[k[len(PREFIX):]] = v.to(torch.float32).contiguous()
        del st
    if not tensors:
        raise RuntimeError(f"no '{PREFIX}*' tensors found in {ckpt}")

    save_file(tensors, str(out_dir / "model.safetensors"))
    cfg = json.loads((ckpt / "config.json").read_text())
    audio_cfg = cfg.get("thinker_config", cfg).get("audio_config", cfg)
    (out_dir / "config.json").write_text(json.dumps(audio_cfg, indent=1))
    src = ckpt / "preprocessor_config.json"
    if src.exists():
        (out_dir / "preprocessor_config.json").write_text(src.read_text())
    print(f"exported {len(tensors)} audio-tower tensors -> {out_dir}")
    return out_dir


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", default=DEFAULT_FUSED)
    ap.add_argument("--out", default=str(DEFAULT_EXPORT))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    export_audio_tower(a.checkpoint, a.out, a.force)
