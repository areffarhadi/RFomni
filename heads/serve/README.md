# Heads service

One process that loads the frozen mix87 audio tower and vision tower once and keeps every
deployable head resident on them. Speech regions (`vad`) come from Silero VAD (vendored,
CPU), not from the mix87 VAD probe. Omni Studio's labeling engine calls it
(`omni-studio/backend/engine/heads.py`); anything else on the machine can too.

```bash
bash serve/start.sh        # 127.0.0.1:8720, physical GPU 1 (RTX 4090); log to stdout
curl -s 127.0.0.1:8720/health
```

- Python: `/home/rf/pablo_vllm026/bin/python3` (the vLLM env: torch with CUDA, transformers,
  FastAPI). Nothing is installed into it. Override with `HEADS_PY`.
- Encoders: `OMNI_AUDIO_TOWER` (default: the exported tower in
  `omni-annotate/omni-mix87-heads-bundle/encoder/mix87_audio_tower`) and
  `OMNI_VISION_ENCODER` (default: the fused MXFP4 checkpoint next to it). Head weights come
  from this repo's `checkpoints/`; the local heads bundle holds the same files.
- GPU: about 4 GB with everything loaded. `HEADS_GPU` picks the card (GPU 0 runs vLLM).
- `HEADS_LOAD=audio` or `vision` loads only one side.

## Endpoints

| | input | heads |
|---|---|---|
| `POST /audio` | `pcm16`: base64 int16 LE, 16 kHz mono, or `path` to a local audio file (any rate); optional `segments` [[s, e], ...] | `vad`, `diarization`, `speaker_count`, `gender`, `spoof`, `phonemes`, `end_of_turn` |
| `POST /image` | `path` or `image` (base64 file bytes) | `boxes` (loc v2, 11 classes, `options.classes`), `pose`, `face_spoof`, `head_pose` |
| `POST /video` | `path` | `video_spoof` |

Times are seconds from the start of the audio sent; boxes are normalised `[x1, y1, x2, y2]`;
keypoints are COCO-17 `[x, y, conf]`, normalised. Per-segment audio heads run on `segments`
or, without them, on the VAD segments.

Measured on the 4090: all seven audio heads on a 25 s file in about 0.5 s; one image
(boxes + pose) about 0.2 s; video spoof on a 22 s clip at 1 fps about 0.5 s.

## What differs from the research scripts

- `compat.py` makes the repo importable in this env without changing it: `vendor/` supplies
  DiariZen's `ConformerEncoder` and pyannote's `Powerset` (the only pieces of either package
  inference uses), `pycocotools` is stubbed (datasets only), and the vision tower's
  `_encode_pixels` reads its tapped blocks with forward hooks, because transformers 5.x
  removed `fast_pos_embed_interpolate` / `rot_pos_emb`.
- Diarization: tracks too short to cluster are attached to their nearest speaker instead of
  being dropped, and VAD speech no speaker claims goes to the most active speaker. Without
  this, half of a test call had no speaker. The clustering threshold (0.70, tuned on
  VoxConverse) is `options.diar_threshold`.
- Face spoof v2 crops faces with loc v2 instead of the ultralytics YOLO face model (not
  shipped), following the reference recipe (score >= 0.5, at least a quarter of the largest
  face, max over faces); without a face the caller decides what to do.

## Not served

| head | why |
|---|---|
| forced aligner (thinker L42H21) | reads attention of the unquantised 30B thinker (~66 GB); vLLM does not expose attention |
| active speaker detection | only a feature-cache batch path; the raw-video pipeline lives in `video_sweep/extract_socialomni_asd.py` and needs porting |
| atomic action (60 AVA classes) | only feature-cache batches; trained on ground-truth person boxes |
| face ASV | no programmatic API in the repo (recipe known: 112 px face, MHFA head, cosine, threshold 0.368) |
| ASV / smart turn standalone | used inside diarization, speaker count, gender and end-of-turn |
| VAD probe (layer 31) | replaced by Silero VAD by choice; the checkpoint stays in `checkpoints/vad_probe` |
