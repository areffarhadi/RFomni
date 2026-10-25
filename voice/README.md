# Qwen3-Omni + Pipecat conversational agent

Local voice agent using your **quantized Qwen3-Omni** checkpoint (vLLM-Omni) inside a
Pipecat pipeline with:

- **Silero VAD** — speech vs silence
- **Smart Turn v3** — end-of-utterance detection (Pipecat default)
- **Barge-in / interrupt** — user speech cancels in-flight Omni TTS generation
- **RNNoise** (optional) — input noise suppression
- **Two-hop Omni** — ASR+LID on audio, then spoken reply via talker+code2wav

## Architecture

```text
LocalAudioTransport (mic/speaker)
  → user_aggregator (Silero VAD + Smart Turn + InterruptionFrame on barge-in)
  → Qwen3OmniVoiceService
       hop 1: POST /v1/chat/completions  modalities=["text"]     (ASR)
       hop 2: POST /v1/chat/completions  modalities=["text","audio"] (speak)
  → transport.output()
  → assistant_aggregator (context)
```

Omni is **utterance-level**, not frame streaming — Pipecat handles turn boundaries;
the full utterance is sent per turn (same as `omni-annotate/realtime/voice.html`).

## 1. Start vLLM-Omni (voice checkpoint)

From omni-annotate (separate terminal):

```bash
VOICE=1 GPU_ID=3 VLLM_PORT=8410 UI_PORT=8505 \
  bash /home/aref.farhadipour/omni-annotate/realtime/run.sh
```

Wait until `voice_health` reports talker up:

```bash
curl -s http://127.0.0.1:8505/voice_health
```

## 2. Run the Pipecat agent

**Headless GPU server (recommended — browser mic):**

```bash
bash /home/aref.farhadipour/pipecat/examples/qwen3-omni/start_pipecat_omni.sh
```

Open the printed link (e.g. `http://10.16.12.60:7860/client/`) on your laptop, click **Connect**, allow mic.

**Local machine with speakers/mic:**

```bash
cd /home/aref.farhadipour/pipecat/examples/qwen3-omni
source /home/aref.farhadipour/pipecat/.venv/bin/activate
pip install -e "../../.[local,silero,rnnoise,runner,webrtc]"
cp env.example .env
python qwen3-omni-voice-agent-local.py   # needs ALSA/PyAudio
python qwen3-omni-voice-agent-webrtc.py --host 0.0.0.0 --port 7860 -t webrtc
```

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `OMNI_BASE_URL` | `http://127.0.0.1:8410/v1` | vLLM-Omni OpenAI API |
| `OMNI_MODEL` | `voice` | Model name in vLLM config |
| `OMNI_MODE` | `assistant` | `assistant`, `translate_en`, `translate_de`, `say` |
| `OMNI_MAX_TOKENS` | `200` | Talker max tokens per reply |
| `OMNI_VAD_STOP_SECS` | `0.5` | Silence after speech before turn ends |
| `OMNI_RNNOISE` | `1` | Set `0` to disable RNNoise filter |

## Files

| File | Role |
|------|------|
| `omni_client.py` | vLLM HTTP client (ASR + streaming speak) |
| `omni_voice_service.py` | Pipecat `FrameProcessor` |
| `qwen3-omni-voice-agent-local.py` | Runnable local agent |

## Limitations

- **Latency**: ~1–3 s per turn (ASR hop + speak hop), not sub-200 ms like cloud realtime APIs.
- **Echo**: use headphones on first tests; RNNoise helps but full-duplex echo cancellation is not included.
- **No native Omni turn frames**: turn detection is Pipecat-side (Silero + Smart Turn), not model-side.
- **Context**: last N turns are injected into the speak prompt; not full fine-tuned chat template.

## Next steps

- WebRTC transport (`SmallWebRTCTransport`) for browser clients
- Speaker gate from `omni-annotate/realtime/server.py`
- Single-hop if a future checkpoint supports instruction-following on raw audio
