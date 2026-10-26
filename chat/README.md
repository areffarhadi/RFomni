# Open WebUI half of the platform

Open WebUI is a clone of an upstream repository, so its customisation is kept here in two
pieces rather than as a fork.

| | |
|---|---|
| `tracked.patch` | the diff against upstream for files upstream already has |
| `files/` | files upstream does not have, at their repository-relative paths |

```bash
cd ~/open-webui
git apply /path/to/tracked.patch
cp -a /path/to/files/. .
# .env is not shipped — platform/setup.sh writes one for the host it runs on
NODE_OPTIONS=--max-old-space-size=12288 npx vite build
# vite build empties build/; the site's own pages and top bar live in backend/open_webui/static
cp -p backend/open_webui/static/{omni-hub.html,omni-nav.js,omni-pages.css,omni-studio.html,custom.css} build/static/
bash start_remote.sh
```

The build needs the raised heap; the default 4 GB ceiling ends it with `Ineffective
mark-compacts near heap limit`. The frontend is served from `build/`, so a source edit does
nothing until `vite build` runs.

## What the patch changes

| File | Why |
|---|---|
| `backend/open_webui/main.py` | mounts the Pipecat router, advertises the feature in `/api/config`, serves `/hub` |
| `backend/open_webui/utils/middleware.py` | routes audio/video/image attachments to Omni as multimodal parts instead of RAG and Whisper |
| `src/lib/components/chat/Chat.svelte`, `ChatControls.svelte`, `MessageInput.svelte` | the Voice mode button and its overlay |

## What `files/` adds

| File | Role |
|---|---|
| `omni_chat_proxy.py` | text-chat normaliser in front of vLLM — see below |
| `backend/open_webui/routers/pipecat.py` | proxies WebRTC signalling so the browser trusts one origin; media still goes peer-to-peer |
| `backend/open_webui/utils/omni_media.py` | uploads → Omni multimodal parts (video capped at 90 s / 80 MB) |
| `src/lib/utils/pipecatClient.ts` | WebRTC + RTVI client: modes, speaker, language, and the speaker-gate controls |
| `src/lib/components/chat/MessageInput/PipecatCallOverlay.svelte` | the in-chat voice overlay |
| `deploy_omni_platform.sh` | brings up vLLM, proxy, Open WebUI and an agent together |
| `start_remote.sh` | Open WebUI bound for LAN access over HTTPS, which the microphone requires |
| `start_omni_voice_vllm.sh` | serves the voice checkpoint with the 3-stage config |
| `share_public.sh` | cloudflared quick tunnel for people off the LAN |
| `static/omni-hub.html` | landing page linking chat and voice |

## The chat proxy earns its place

Three separate problems make a direct Open WebUI → vLLM connection unusable for text:

1. Open WebUI does not send `modalities=["text"]`, so the talker runs on every chat message
   and streams audio codes into the text box, filling the 4096-token context and breaking the
   next turn.
2. It sends `tool_choice: "auto"` whenever plugins are enabled, and the voice backend is
   served without `--enable-auto-tool-choice`/`--tool-call-parser`, so vLLM rejects the whole
   request with a 400. The proxy strips the tool fields; without a parser the model could not
   emit tool calls anyway.
3. A long history can arrive oversized, so the proxy keeps recent turns and caps
   `max_tokens`.

Consequence of (2): Open WebUI features that need function calling — web search, code
interpreter — do not work against this model. They would have failed silently before; the
proxy makes that explicit. Restart vLLM with the two tool flags if they are ever wanted.

## Selecting a version

```bash
PIPECAT_BASE_URL=https://127.0.0.1:7860   # v1
PIPECAT_BASE_URL=https://127.0.0.1:7870   # v2
PIPECAT_BASE_URL=https://127.0.0.1:7880   # v3
```

`start_remote.sh` sources `.env` with `set -a` after applying its own defaults, so `.env`
wins over anything exported by `deploy_omni_platform.sh`.

The speaker controls are capability-gated on the agent's `rfomni-gate` message, so pointing at
an older port gives exactly that version's behaviour from the same build.
