# Qwen3-Omni Deployment Platform

**One Open WebUI** for text, image, and voice. Voice mode uses the **Pipecat RFOMNI** engine (VAD, barge-in, interrupt, Omni talker) inside the chat page — not a separate Pipecat UI.

## Quick start

```bash
bash /home/aref.farhadipour/open-webui/deploy_omni_platform.sh
```

Open: **https://10.16.12.60:8080/**

In the chat input bar, click the black circular **Voice mode** button. Speak — Omni replies with audio; transcripts appear in the chat.

## Architecture

```text
Open WebUI (:8080)
  ├─ Text / image chat  →  omni_chat_proxy :8401  →  vLLM :8400  (modalities=text)
  └─ Voice mode button →  WebRTC (signaling via /api/v1/pipecat)
                              →  Pipecat RFOMNI :7860
                              →  vLLM :8400  (two-hop ASR + talker)
```

Pipecat handles turn-taking (Silero VAD, barge-in). Open WebUI is only the shell UI.

## Modes (in Voice overlay)

Assistant · Translate → EN · Annotator

## Env

```bash
PIPECAT_ENABLED=true
PIPECAT_BASE_URL=https://127.0.0.1:7860
PIPECAT_INSECURE_SSL=true
```

## Related

- Pipecat agent: `/home/aref.farhadipour/pipecat/examples/qwen3-omni/`
- Client helper: `src/lib/utils/pipecatClient.ts`
- Overlay: `src/lib/components/chat/MessageInput/PipecatCallOverlay.svelte`
