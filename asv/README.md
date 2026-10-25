# rfomni_asv — speaker verification for the voice agent

Vendored copy of RFomni-ASV. It answers two questions for the agent: *is this the owner
speaking?* and *may this voice interrupt?* V5.1 runs it on the CPU through onnxruntime,
which is why V5.1 needs no second GPU.

Its dependencies are in [../requirements/asv.txt](../requirements/asv.txt) rather than
beside the package, so every venv this repo builds is described in one directory.

This is the package only. **The checkpoints are not here** — they live in the shared store
and are selected with `RFOMNI_ASV_MODELS`, so a checkout costs 88 KB instead of 578 MB and
several checkouts on one box share one copy:

```bash
RFOMNI_ASV_MODELS=/data/d01/speech/tts/models/agigo-omni/asv \
PYTHONPATH=platform/asv \
  python -m rfomni_asv.service --host 127.0.0.1 --port 8613
```

`launch_auto.sh` passes both, so nothing has to be set by hand for a normal launch.
Without `RFOMNI_ASV_MODELS` the package falls back to a `models/` directory beside itself,
which is how the standalone RFomni-ASV checkout works.

## The checkpoints

Selected with `SPK_MODEL`. Each carries its own threshold, because their score
distributions differ — changing model without changing threshold silently changes who the
gate lets through. EER is on VoxCeleb1-O.

| `SPK_MODEL` | directory in the store | dim | threshold | EER | note |
|---|---|---|---|---|---|
| `samresnet34` | `voxblink2_samresnet34_ft` | 256 | 0.45 | 0.415 % | default — the accurate one |
| `ecapa1024` | `voxblink2_ecapa1024` | 192 | 0.45 | 0.675 % | 4.6× faster on CPU, close accuracy |
| `ecapa512` | `voxceleb_ecapa512_lm` | 192 | 0.50 | 0.883 % | fastest, loosest gate |

`voxceleb_resnet34_lm` is also in the store. It is an evaluated alternative rather than a
registry entry, and nothing selects it by name.

V5.1 was written around `ecapa512` for its 8 ms, then moved to `samresnet34`: on a live
WebRTC mic ecapa512 could not hold the owner apart from a stranger — owner turns spread
0.16–0.75 against a 0.206 impostor ceiling, measured 2026-09-17. On CPU `samresnet34` still
answers in ~100 ms, which is nothing beside a multi-second model reply.

## Staying in step with RFomni-ASV

This copy is vendored, not submoduled, so it can drift. It came from `~/RFomni-ASV` at
`3fed557`. The one local change is `MODELS_ROOT` in `backends.py`, which now reads
`RFOMNI_ASV_MODELS` before falling back to the in-repo `models/` directory — that change was
made in both copies, so a straight `diff -r` should come back clean:

```bash
diff -r --exclude=__pycache__ ~/RFomni-ASV/rfomni_asv platform/asv/rfomni_asv
```
