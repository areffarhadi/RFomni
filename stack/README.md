# Omni stack: start and stop everything

This folder runs the RFomni machine (`~/omni-stack` is a link to it). `services/` is not in the repository: it holds each service's exact command and environment, including
API keys, and `stack.py snapshot` writes it on each machine from the running services.

- `configs/vllm_mix87_voice.yaml`, `configs/vllm_mix87_chat.yaml`: the vLLM-omni deploy configs for RFomni
  (served id `mix87`; the voice config has the thinker context at 16384).
- `patches/vllm_omni_stage_names.patch`: a five-line patch to vllm-omni so the stage processes show as
  `mix87::thinker`, `mix87::talker`, `mix87::code2wav` in nvidia-smi (with `OMNI_STAGE_NAMES` and
  `VLLM_PROCESS_NAME_PREFIX=mix87` in the vllm service's environment). Apply with
  `patch -p1 -d <site-packages> < patches/vllm_omni_stage_names.patch`.

One command brings back the whole working setup: the model server, the heads, the speaker
check, the voice agent, the chat page and Omni Studio with its assistant.

```bash
~/omni-stack/start_all.sh     # start what is not running, wait until each service answers
~/omni-stack/status_all.sh    # one line per service
~/omni-stack/stop_all.sh      # stop everything (before shutting the machine down)
```

Any of them takes service names to act on only some, e.g. `~/omni-stack/stack.py restart studio`.

| name   | port | what | GPU |
|--------|------|------|-----|
| asv    | 8613 | speaker check used by the voice agent's gate | |
| vllm   | 8410 | vLLM-omni mix87: thinker, talker, code2wav (models `mix87`, `voice`) | 0 |
| heads  | 8720 | sidecar heads + Silero VAD (`heads/serve`) | 1 |
| proxy  | 8401 | chat proxy in front of vLLM for Open WebUI; adds the `omni-studio` model | |
| voice  | 7912 | Pipecat voice agent v5.3 (https), has the `studio` mode | |
| webui  | 8080 | Open WebUI chat page, https://192.168.8.174:8080/ | |
| studio | 8710 | Omni Studio + assistant panel, https://192.168.8.174:8710/ (sign-in required) | |

`vllm` is the slow one: a cold start loads three stages and can take several minutes. The
script waits up to 20 minutes for it. Everything else is ready within seconds to a minute.

## Sign-in

The chat page (Open WebUI) owns the accounts. The studio's sign-in page posts to it and keeps the
session in the same `token` cookie, so open both apps on the same address (192.168.8.174) and one
sign-in covers both. The chat sends the user to the `omni-studio` model as a signed header
(`FORWARD_USER_INFO_HEADER_JWT_SECRET` in webui = `OMNI_FORWARD_JWT_SECRET` in studio), and a voice
session carries a pass the studio signs, so the assistant's plan follows the person everywhere.
`OMNI_AUTH=off` in the studio's environment turns sign-in off again.

vLLM's stage processes are named `mix87::thinker`, `mix87::talker`, `mix87::code2wav` in nvidia-smi
(`OMNI_STAGE_NAMES` + a 5-line local patch in `vllm_omni/engine/stage_engine_core_proc.py`; a
reinstall of vllm-omni drops the patch and the old names come back, nothing breaks).

## How it works

`services/<name>.json` holds each service's exact command, folder, environment and log file,
copied from the running processes on 2026-10-25 with `stack.py snapshot`. `start` replays them
in the order above. These files are private (mode 600) because the environment holds API keys.

If you change how a service is started (new flags, a new model, another port), start it by hand
once, then run `~/omni-stack/stack.py snapshot` to record the new way.

Logs: `/tmp/omni_mix87_voice.log` (vllm), `/tmp/omni_heads.log`, `/tmp/rfomni_asv_samresnet34.log`,
`/tmp/omni_chat_proxy.log`, `/tmp/omni_pipecat_v5_3.log`, `/tmp/open-webui.log`, `/tmp/omni_studio.log`.

## Where each service's code is

All in this repository: `studio/`, `voice/` (Pipecat voice agent V5.3), `asv/` (speaker check), `heads/serve/`
(heads service), `chat/` (Open WebUI customisation and the chat proxy) and `stack/configs/` (vLLM-omni). The
upstream clones (Open WebUI, Pipecat), the Python environments and the model weights live outside it; see the
repository's README.
