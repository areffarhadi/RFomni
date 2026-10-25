# V5.3 — V5.2 with Local Smart Turn v3 end-of-turn

V5.2 is unchanged and remains the silence-timeout / VADIterator baseline for reports.
V5.3 is a fork that only changes **when a user turn ends**.

| | V5.2 (keep for reports) | **V5.3** |
|---|---|---|
| Speech start | Silero `VADIterator` (default) | same |
| Speech stop | `SpeechTimeoutUserTurnStopStrategy` | **`LocalSmartTurnAnalyzerV3`** |
| Identity / ASV / barge-in | V5.1 CPU gate | same |
| Agent port | 7911 | **7912** |

Pipecat ships Smart Turn v3 as its library default stop strategy; V5.2 overrode that with
a short speech timeout. V5.3 uses the bundled `smart-turn-v3.2-cpu.onnx` model through
`TurnAnalyzerUserTurnStopStrategy`.

## Run

```bash
bash platform/run_password_v53.sh              # agent v5.3 on :7912
# keep the old pipeline:
bash platform/run_password_v52.sh              # agent v5.2 on :7911
```

Or:

```bash
VERSION=v5.3 AGENT_PORT=7912 bash platform/launch_auto.sh
```

## Tunables

| env | default | |
|---|---|---|
| `OMNI_SMART_TURN_STOP_SECS` | `3.0` | max silence before the analyzer may end the turn |
| `OMNI_SMART_TURN_PRE_SPEECH_MS` | `500` | audio kept before speech onset |
| `OMNI_SMART_TURN_MAX_DURATION_SECS` | `8.0` | max segment length scored by the model |
| `OMNI_SMART_TURN_CPU_COUNT` | `1` | onnxruntime intra-op threads |
| `OMNI_SMART_TURN_MODEL_PATH` | *(bundled)* | override ONNX path |
| `OMNI_VAD_ENGINE` | `vaditerator` | start detector only (`silero` = V5.1 analyzer) |

## Needs

Same as V5.2 for VADIterator (`silero-vad` + CPU torch), plus `onnxruntime` and `soxr`
in the pipecat venv (already required by Pipecat 1.8.2’s Smart Turn v3).
`start_v5_3.sh` checks both.

## Studio mode (Omni Studio assistant)

A client can open a session in the `studio` mode (`{"body": {"mode": "studio"}}` in the `/start` body, as the
Omni Studio panel does). Each transcript then goes to Omni Studio's `/api/agent/turn` (`OMNI_STUDIO_URL`,
default `https://127.0.0.1:8710`) with `via=voice` and whether the speaker gate accepted the voice; the reply
(approval card, page actions) is sent to the client as an `rfomni-studio` server message and its `say` text is
spoken word for word. The greeting is skipped in this mode. The studio adds a signed pass (`studio_auth`) to the
start body; the agent sends it back as `X-Omni-Auth`, so spoken turns act for the person who opened the session.

