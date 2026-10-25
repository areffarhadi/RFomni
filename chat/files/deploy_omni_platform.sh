#!/usr/bin/env bash
# Unified Qwen3-Omni deployment:
#   - vLLM-Omni VOICE (shared backend, :8410 — 8400 is taken by another tenant)
#   - Open WebUI (text / image / file chat)
#   - Pipecat RFOMNI V3 WebRTC (voice conversation + goal-speaker recognition)
#
# Usage:
#   bash deploy_omni_platform.sh          # start all (reuse running vLLM if healthy)
#   bash deploy_omni_platform.sh stop     # stop platform services (not training jobs)
#   FORCE_VLLM=1 bash deploy_omni_platform.sh   # restart vLLM on GPU
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PIPECAT_DIR="/home/aref.farhadipour/pipecat/examples/qwen3-omni-v3"
CERT_DIR="${CERT_DIR:-/home/aref.farhadipour/omni-annotate/realtime/certs}"

VLLM_PORT="${VLLM_PORT:-8410}"
PROXY_PORT="${PROXY_PORT:-8401}"
OWUI_PORT="${OWUI_PORT:-8080}"
VOICE_PORT="${VOICE_PORT:-7880}"
GPU_ID="${GPU_ID:-3}"
SERVED_MODEL="${SERVED_MODEL:-voice}"
OMNI_MODEL="${OMNI_MODEL:-$SERVED_MODEL}"

# Pipecat RFOMNI voice (Open WebUI Voice mode uses this — not the /client UI)
export PIPECAT_ENABLED=true
export PIPECAT_BASE_URL="${PIPECAT_BASE_URL:-https://127.0.0.1:${VOICE_PORT}}"
export PIPECAT_INSECURE_SSL=true

LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -vE '^(172\.(1[6-9]|2[0-9]|3[01])\.|169\.254\.|$)' | head -1)"
LAN_IP="${LAN_IP:-127.0.0.1}"

log() { echo "[omni-platform] $*"; }

vllm_ok() {
  curl -sf -m 3 "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1
}

stop_platform() {
  log "stopping Open WebUI, proxy, Pipecat agent (vLLM left running unless FORCE_VLLM=1)"
  fuser -k "${OWUI_PORT}/tcp" 2>/dev/null || true
  fuser -k "${PROXY_PORT}/tcp" 2>/dev/null || true
  fuser -k "${VOICE_PORT}/tcp" 2>/dev/null || true
  if [[ "${FORCE_VLLM:-0}" == "1" ]]; then
    fuser -k "${VLLM_PORT}/tcp" 2>/dev/null || true
  fi
}

install_hub() {
  mkdir -p "$ROOT/build/static" "$ROOT/backend/open_webui/static"
  cp -f "$ROOT/static/omni-hub.html" "$ROOT/build/static/omni-hub.html"
  cp -f "$ROOT/static/omni-hub.html" "$ROOT/backend/open_webui/static/omni-hub.html"
}

start_vllm() {
  if [[ "${FORCE_VLLM:-0}" != "1" ]] && vllm_ok; then
    log "reusing vLLM on :${VLLM_PORT}"
    return
  fi
  fuser -k "${VLLM_PORT}/tcp" 2>/dev/null || true
  sleep 1
  log "starting vLLM voice on GPU ${GPU_ID} port ${VLLM_PORT}"
  tmux kill-session -t omni-voice-platform 2>/dev/null || true
  tmux new-session -d -s omni-voice-platform \
    "GPU_ID=${GPU_ID} VLLM_PORT=${VLLM_PORT} bash ${ROOT}/start_omni_voice_vllm.sh; exec bash"
  printf "[omni-platform] waiting for vLLM"
  for _ in $(seq 1 120); do
    vllm_ok && { echo " ready"; return; }
    printf "."
    sleep 5
  done
  echo
  echo "ERROR: vLLM not ready on :${VLLM_PORT}" >&2
  exit 1
}

start_proxy() {
  fuser -k "${PROXY_PORT}/tcp" 2>/dev/null || true
  sleep 1
  log "starting chat proxy :${PROXY_PORT} -> vLLM :${VLLM_PORT}"
  tmux kill-session -t omni-chat-proxy 2>/dev/null || true
  tmux new-session -d -s omni-chat-proxy \
    "OMNI_UPSTREAM=http://127.0.0.1:${VLLM_PORT} PROXY_PORT=${PROXY_PORT} ${ROOT}/.venv/bin/python ${ROOT}/omni_chat_proxy.py; exec bash"
  for _ in $(seq 1 20); do
    curl -sf -m 2 "http://127.0.0.1:${PROXY_PORT}/health" >/dev/null 2>&1 && return
    sleep 1
  done
  echo "WARN: chat proxy health check failed" >&2
}

start_openwebui() {
  fuser -k "${OWUI_PORT}/tcp" 2>/dev/null || true
  sleep 1
  log "starting Open WebUI HTTPS on :${OWUI_PORT}"
  tmux kill-session -t open-webui 2>/dev/null || true
  tmux new-session -d -s open-webui "bash ${ROOT}/start_remote.sh; exec bash"
  for _ in $(seq 1 40); do
    curl -sk -m 2 "https://127.0.0.1:${OWUI_PORT}/health" >/dev/null 2>&1 && return
    sleep 2
  done
  echo "WARN: Open WebUI health check failed" >&2
}

start_pipecat() {
  cd "$PIPECAT_DIR"
  [[ -f .env ]] || { echo "no .env — run 'bash platform/setup.sh owui' to write one" >&2; exit 1; }
  bash "$PIPECAT_DIR/prepare_rfomni_client.sh"
  bash "$PIPECAT_DIR/make_cert.sh"

  fuser -k "${VOICE_PORT}/tcp" 2>/dev/null || true
  sleep 1

  export SSL_CERTFILE="$PIPECAT_DIR/certs/cert.pem"
  export SSL_KEYFILE="$PIPECAT_DIR/certs/key.pem"
  export OMNI_BASE_URL="http://127.0.0.1:${VLLM_PORT}/v1"
  export OMNI_MODEL="$OMNI_MODEL"
  export OMNI_RNNOISE="${OMNI_RNNOISE:-0}"
  export OMNI_AVATAR="${OMNI_AVATAR:-none}"

  PY="${PIPECAT_PY:-/home/aref.farhadipour/pipecat/.venv/bin/python3}"
  AGENT_LOG="${AGENT_LOG:-/tmp/omni_pipecat_agent.log}"

  log "starting Pipecat RFOMNI on :${VOICE_PORT} (model=${OMNI_MODEL})"
  nohup "$PY" qwen3-omni-voice-agent-webrtc.py \
    --host 0.0.0.0 --port "$VOICE_PORT" -t webrtc \
    --ice-servers stun:stun.l.google.com:19302 >>"$AGENT_LOG" 2>&1 &

  for _ in $(seq 1 30); do
    curl -sk -m 2 "https://127.0.0.1:${VOICE_PORT}/client/" >/dev/null 2>&1 && return
    sleep 1
  done
  echo "WARN: Pipecat client not ready — see $AGENT_LOG" >&2
}

print_links() {
  local hub="https://${LAN_IP}:${OWUI_PORT}/hub"
  local chat="https://${LAN_IP}:${OWUI_PORT}/"
  local voice="https://${LAN_IP}:${VOICE_PORT}/client/"
  cat >"$ROOT/OMNI_PLATFORM_LINKS.txt" <<EOF
Hub:    ${hub}
Chat:   ${chat}
Voice:  ${voice}
vLLM:   http://127.0.0.1:${VLLM_PORT}/v1  (model: ${SERVED_MODEL})
Proxy:  http://127.0.0.1:${PROXY_PORT}/v1
EOF
  cat <<EOF

============================================================
  Qwen3-Omni Platform (shared vLLM backend)

  Hub (start here):
    ${hub}

  Text / image chat (Open WebUI):
    ${chat}

  Voice conversation (Pipecat RFOMNI):
    ${voice}

  Accept self-signed HTTPS certs on :${OWUI_PORT} and :${VOICE_PORT} for mic access.

  tmux sessions: omni-voice-platform, omni-chat-proxy, open-webui
  Pipecat log: /tmp/omni_pipecat_agent.log
============================================================

EOF
}

if [[ "${1:-}" == "stop" ]]; then
  stop_platform
  exit 0
fi

install_hub
start_vllm
start_proxy
start_openwebui
start_pipecat
print_links
