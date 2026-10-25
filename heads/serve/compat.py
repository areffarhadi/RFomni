"""Make the research code importable in the serving env, without changing the repo.

* sys.path: the repo root, repo/phoneme and serve/vendor.
* serve/vendor holds the two small modules the diarization head imports but that are not
  installed here: DiariZen's ConformerEncoder and pyannote's Powerset (MIT; see vendor/README).
* pycocotools is imported at module level by omni_grounding.data.* for dataset classes only;
  a stub keeps loc/pose/video-FAS importable.
* transformers 5.x removed the vision tower's fast_pos_embed_interpolate/rot_pos_emb, which
  FrozenOmniVisionEncoder._encode_pixels calls; the replacement reads the tapped blocks with
  forward hooks on one normal forward pass (same tensors, verified on the shipped heads).
"""
import sys
import types
from pathlib import Path

import numpy as np

SERVE = Path(__file__).resolve().parent
REPO = SERVE.parent
for p in (SERVE / "vendor", REPO / "phoneme", REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def stub_pycocotools() -> None:
    if "pycocotools" in sys.modules:
        return
    pc, pcc = types.ModuleType("pycocotools"), types.ModuleType("pycocotools.coco")

    class COCO:  # noqa: D401 - dataset-only dependency
        def __init__(self, *a, **k):
            raise RuntimeError("pycocotools is not installed (datasets are not used for inference)")

    pcc.COCO, pc.coco = COCO, pcc
    sys.modules["pycocotools"], sys.modules["pycocotools.coco"] = pc, pcc


def patch_vision_encoder() -> None:
    import torch
    import torch.nn.functional as F
    import omni_vision_encoder as ove

    if getattr(ove.FrozenOmniVisionEncoder, "_serve_patched", False):
        return

    def _encode_pixels(self, pixel_values, grid_thw, tap_layers, device, out_dtype=torch.float32):
        caps, hooks = {}, []
        for i in sorted(set(tap_layers)):
            hooks.append(self.encoder.blocks[i].register_forward_hook(
                lambda m, a, o, i=i: caps.__setitem__(i, o[0] if isinstance(o, tuple) else o)))
        try:
            dt = next(self.encoder.parameters()).dtype
            self.encoder(pixel_values.to(device=device, dtype=dt), grid_thw.to(device))
        finally:
            for h in hooks:
                h.remove()
        g = grid_thw.to(device)
        lengths = (g[:, 0] * g[:, 1] * g[:, 2]).tolist()
        cu = np.concatenate([[0], np.cumsum(lengths)])
        T = max(lengths)
        outs = []
        for t in tap_layers:
            x = caps[t]
            outs.append(torch.stack([F.pad(x[cu[b]:cu[b + 1]], (0, 0, 0, T - lengths[b])) for b in range(len(lengths))]))
        return torch.stack(outs, 2).to(out_dtype)

    ove.FrozenOmniVisionEncoder._encode_pixels = _encode_pixels
    ove.FrozenOmniVisionEncoder._serve_patched = True
