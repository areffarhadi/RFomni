# RFomni

Everything the RFomni website runs, in one place: the annotation studio, the public pages (Task sharing, Subjective
tests, Data collection), the voice agent, the chat page's customisation and proxy, the trained heads service, the
speaker check, and the scripts that start and stop it all.

| Folder | What | Port |
|---|---|---|
| `studio/` | Omni Studio: the annotation studio, its assistant, the public pages `/tasks/`, `/listen/`, `/collect/` and the landing and sign-in pages. Version history and tags (v0.1.0 to v0.6.5) are in this folder's history. | 8710 |
| `voice/` | The voice agent (Pipecat, version 5.3): speaker check gate, end-of-turn, the studio's voice mode. | 7912 |
| `chat/` | The chat page: the customisation of an upstream Open WebUI clone (`tracked.patch` for its files, `files/` for new ones) and `files/omni_chat_proxy.py`, the proxy in front of the model that adds the studio assistant and shows the model as RFomni. | 8080, 8401 |
| `heads/serve/` | The heads service: trained heads on the model's frozen audio and vision towers (speakers, overlap, spoofing, pose, …) and Silero VAD. | 8720 |
| `asv/` | The speaker check (`rfomni_asv`) the voice agent uses to know who is speaking. | 8613 |
| `stack/` | `start_all.sh` / `stop_all.sh` / `status_all.sh` (`stack.py`), the vLLM-omni configs for the model, and a patch for readable process names. | 8410 (vLLM) |
| `requirements/` | Python requirements of the voice agent and the speaker check. | |

## Run

```bash
stack/start_all.sh     # starts whatever is not running and waits until each service answers
stack/status_all.sh
stack/stop_all.sh
```

`stack/services/<name>.json` holds each service's exact command, folder and environment on this machine (API keys
included, so it is not in the repository); `stack/stack.py snapshot` writes it from running services. See
`stack/README.md`.

Addresses on the network: studio and public pages `https://<host>:8710/` (the landing page is `/landing/`), chat
`https://<host>:8080/`. The microphone (voice, recording pages) needs https.

## Outside the repository

- **Model weights**: the fine-tuned model served by vLLM-omni (`mix87`, shown as RFomni) and the heads bundle
  (`RFOMNI_HEADS_BUNDLE`, default `~/omni-annotate/omni-mix87-heads-bundle/encoder`); the trained heads, 771 MB
  (`heads/checkpoints`, a local link to `~/omni-mix87-sidecar-heads/checkpoints`); the speaker check models
  (`RFOMNI_ASV_MODELS`).
- **Upstream clones and environments** in `~/.rfomni/`: `open-webui/` (apply `chat/tracked.patch` and copy `chat/files/`
  in, then build the frontend), `pipecat/` (editable install in its `.venv`), `venvs/` (speaker check, Open WebUI);
  vLLM-omni in its own environment (`stack/patches/` has the process-name patch).
- **Certificates** for https in `certs/` (self-signed for the LAN; not in the repository).
- **Runtime data**: the studio's `studio/workspace/` (projects, shared tasks, recordings, sessions).
