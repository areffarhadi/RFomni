# Vendored modules

Imported by `model_sidecar.py` (diarization head) at inference time but not installed in the
serving environment. Copied unchanged from their upstream projects:

| path | upstream | license |
|---|---|---|
| `diarizen/models/module/conformer.py` | DiariZen (BUT-FIT/DiariZen), `diarizen/models/module/conformer.py` | MIT |
| `pyannote/audio/utils/powerset.py` | pyannote-audio, `pyannote/audio/utils/powerset.py` | MIT |
| `silero_vad/` | silero-vad 6.2.3 (snakers4/silero-vad): the package code and `data/silero_vad.jit` only | MIT (`silero_vad/LICENSE`) |

Only `ConformerEncoder` and `Powerset` are used from the first two; the diarization
checkpoint loads with all keys matched. Silero VAD gives the speech regions (the service's
`vad` head), chosen over the mix87 VAD probe.
