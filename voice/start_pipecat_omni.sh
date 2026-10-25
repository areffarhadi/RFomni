#!/usr/bin/env bash
# Start MXFP4 Qwen3-Omni vLLM + Pipecat WebRTC agent over HTTPS (LAN mic).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO=/home/aref.farhadipour/omni-annotate
CK=/data/d01/speech/tts/data/Qwen3Omni_Opt/checkpoints
VENV=/data/d01/speech/tts/data/Qwen3Omni_Opt/pablo_vllm026
PY="${PIPECAT_PY:-/home/aref.farhadipour/pipecat/.venv/bin/python3}"

GPU_ID="${GPU_ID:-0}"
VLLM_PORT="${VLLM_PORT:-8410}"
UI_PORT="${UI_PORT:-7860}"
UI_HOST="${UI_HOST:-0.0.0.0}"
LOG="${LOG:-/tmp/omni_pipecat_vllm_gpu${GPU_ID}.log}"
PIDFILE="/tmp/omni_pipecat_vllm.pid"
AGENT_LOG="${AGENT_LOG:-/tmp/omni_pipecat_agent.log}"

MODEL="${MODEL:-$CK/unified_caps_ckpt30000-MXFP4A16mse-VOICE}"
CFG="${CFG:-$DIR/configs/vllm_voice_multiuser.yaml}"

cd "$DIR"
[[ -f .env ]] || cp env.example .env

# Recall named UI preset (ui_versions/active) before building the client.
# shellcheck disable=SC1091
source "$DIR/ui_version.sh"
load_active_version_env

bash "$DIR/prepare_rfomni_client.sh"
bash "$DIR/make_cert.sh"
export SSL_CERTFILE="$DIR/certs/cert.pem"
export SSL_KEYFILE="$DIR/certs/key.pem"

# Prefer an already-running voice stack (e.g. VOICE=1 from omni-annotate).
# Set FORCE_VLLM=1 to kill and relaunch on GPU_ID.
if [[ "${FORCE_VLLM:-0}" != "1" ]] && curl -sf -m 2 "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
  echo "[pipecat-omni] reusing voice vLLM on :${VLLM_PORT}"
else
  echo "[pipecat-omni] starting voice vLLM on GPU ${GPU_ID} (log: ${LOG})"
  fuser -k "${VLLM_PORT}/tcp" 2>/dev/null || true
  sleep 1
  (
    export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES="$GPU_ID"
    export CUDA_HOME="$VENV/lib/python3.11/site-packages/nvidia/cu13"
    export PATH="$CUDA_HOME/bin:$VENV/bin:/usr/bin:/bin"
    export LD_LIBRARY_PATH="$CUDA_HOME/lib"
    export NVCC_APPEND_FLAGS="-DCCCL_DISABLE_CTK_COMPATIBILITY_CHECK"
    export MODELSCOPE_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
    nohup "$VENV/bin/vllm" serve "$MODEL" --omni --host 127.0.0.1 --port "$VLLM_PORT" \
      --trust-remote-code --served-model-name voice \
      --stage-configs-path "$CFG" \
      --init-timeout 2400 --stage-init-timeout 2400 \
      --no-enable-flashinfer-autotune >>"$LOG" 2>&1 &
    echo $! >"$PIDFILE"
  )
  printf "[pipecat-omni] waiting for vLLM"
  for _ in $(seq 1 240); do
    curl -sf -m 3 "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1 && { echo " ready"; break; }
    printf "."
    sleep 5
  done
  curl -sf -m 3 "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null \
    || { echo; echo "ERROR: vLLM not ready — see $LOG" >&2; exit 1; }
fi

fuser -k "${UI_PORT}/tcp" 2>/dev/null || true
sleep 1

# Load .env so OMNI_RNNOISE=0 / OMNI_AVATAR etc. apply; override base URL to live voice port.
set -a
# shellcheck disable=SC1091
[[ -f .env ]] && . ./.env
set +a
export OMNI_BASE_URL="http://127.0.0.1:${VLLM_PORT}/v1"
export OMNI_RNNOISE="${OMNI_RNNOISE:-0}"
export OMNI_AVATAR="${OMNI_AVATAR:-none}"
export WAV2LIP_URL="${WAV2LIP_URL:-http://127.0.0.1:8099}"

# Optional Wav2Lip sidecar only when explicitly requested.
if [[ "${OMNI_AVATAR}" == "wav2lip" || "${OMNI_AVATAR}" == "livetalking" ]]; then
  bash /data/d01/speech/tts/data/rfomni-avatar/start_wav2lip_sidecar.sh || \
    echo "[pipecat-omni] WARN: Wav2Lip sidecar failed — agent will fall back"
fi

echo "[pipecat-omni] starting HTTPS WebRTC agent on ${UI_HOST}:${UI_PORT} (RNNoise=${OMNI_RNNOISE}, multi-user turns=${OMNI_MAX_CONCURRENT_TURNS:-8}, avatar=${OMNI_AVATAR})"
nohup "$PY" qwen3-omni-voice-agent-webrtc.py \
  --host "$UI_HOST" --port "$UI_PORT" -t webrtc \
  --ice-servers stun:stun.l.google.com:19302 >>"$AGENT_LOG" 2>&1 &
APID=$!

for _ in $(seq 1 30); do
  curl -sk -m 2 "https://127.0.0.1:${UI_PORT}/client/" >/dev/null 2>&1 && break
  sleep 1
done

LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -vE '^(172\.(1[6-9]|2[0-9]|3[01])\.|169\.254\.|$)' | head -1)"
LINK="https://${LAN_IP:-127.0.0.1}:${UI_PORT}/client/"
echo "$LINK" > "$DIR/PIPECAT_OMNI_LINK.txt"

cat <<EOF

============================================================
  Qwen3-Omni + Pipecat (HTTPS — required for LAN microphone)
  Open in browser, accept the self-signed cert warning once:
    ${LINK}
  Then click Connect and allow the microphone.

  SSH tunnel alternative (no cert warning):
    ssh -N -L ${UI_PORT}:127.0.0.1:${UI_PORT} \$USER@$(hostname)
    open http://127.0.0.1:${UI_PORT}/client/

  vLLM: http://127.0.0.1:${VLLM_PORT}/v1
  Logs: ${AGENT_LOG}
============================================================

EOF
