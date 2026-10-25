# RFomni Playground — feature notes (base UI + upgrades)

## Base UI (saved preset: `rfomni-playground`)

- Title **RFomni Playground**, Pipecat logo removed
- Fast turn-end (~0.5s silence)
- Multi-user pool (`OMNI_MAX_CONCURRENT_TURNS=8`)
- Conversation transcript (built-in Voice UI Kit panel)

## Upgrades (this iteration)

| Feature | How |
|---------|-----|
| **Mode switcher** | Bottom chrome: Assistant / Translate→EN / Annotator → RTVI `set-mode` |
| **Memory indicator** | `Memory N/12` badge via RTVI `server-message` |
| **User camera → Omni vision** | `video_in` + `CameraTap`; vision hop when you ask about reactions/face |
| **Talking avatar (Bot Video)** | **Off by default** (`OMNI_AVATAR=none` → empty panel). Optional: `facemesh`, `wav2lip`, `simli`, `local` |
| **Annotator mode** | Full ASR/NER/EMO/GEN/LID/EVENT/QUALITY + EN translation if LID≠en, then spoken summary |

## Env

```bash
OMNI_VISION=1
# SIMLI_API_KEY=...          # natural lip-sync
# SIMLI_FACE_ID=cace3ef7-a4c4-425d-a8cf-a5358eb0c427
```

## Recall / save UI

```bash
./ui_version.sh list
./ui_version.sh save rfomni-v2 "modes + memory + vision + avatar"
./ui_version.sh use rfomni-playground   # restore base branding/settings
```

## Pipeline

```
WebRTC in → CameraTap → UserAggregator(VAD) → Qwen3OmniVoiceService
  → Avatar(FaceMesh|Wav2Lip|Simli|local) → WebRTC out
```
