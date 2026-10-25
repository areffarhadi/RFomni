#!/usr/bin/env bash
# Start the RFOMNI V5.3 voice agent — V5.2 unchanged, with end-of-turn handed to
# Pipecat Local Smart Turn v3 (ONNX) instead of SpeechTimeoutUserTurnStopStrategy.
#
#   bash start_v5_3.sh
#
# Needs a vLLM-Omni voice backend (thinker + talker + code2wav) already serving. Start one with:
#   VOICE=1 GPU_ID=3 VLLM_PORT=8410 UI_PORT=8506 bash ~/omni-annotate/realtime/run.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PORT="${VOICE_PORT:-7912}"
OMNI_BASE_URL="${OMNI_BASE_URL:-http://127.0.0.1:8410/v1}"
OMNI_MODEL="${OMNI_MODEL:-voice}"
# launch_auto.sh passes PIPECAT_PY/ASV_DIR/ASV_PY in, so these defaults only matter when
# this script is run directly. They follow the same order it uses: the stack setup.sh
# builds, then a checkout that predates it.
STACK="${STACK:-$HOME/.rfomni}"
MODELS="${MODELS:-/data/d01/speech/tts/models/agigo-omni}"
pick() { local __v=$1 __first=$2 __c; shift
  for __c in "$@"; do [[ -e "$__c" ]] && { printf -v "$__v" %s "$__c"; return; }; done
  printf -v "$__v" %s "$__first"; }

[[ -n "${PIPECAT_PY:-}" ]] || pick PIPECAT_PY "$STACK/pipecat/.venv/bin/python3" \
  "$STACK/pipecat/.venv/bin/python3" "$HOME/pipecat/.venv/bin/python3"
# exported, not just set: prepare_rfomni_client.sh is a child process and has its own
# fallback to a path that only exists on the box this was developed on.
export PIPECAT_PY
PY="$PIPECAT_PY"
LOG="${AGENT_LOG:-/tmp/omni_pipecat_v5_3.log}"

# The voiceprint embedder: RFomni-ASV on the CPU (same as V5.1/V5.2).
GATE_PORT="${GATE_PORT:-8613}"
GATE_URL="http://127.0.0.1:${GATE_PORT}"
[[ -n "${ASV_DIR:-}" ]] || pick ASV_DIR "$DIR/../../../omni-annotate/platform/asv" \
  "$DIR/../../../omni-annotate/platform/asv" "$HOME/RFomni-ASV"
[[ -n "${ASV_PY:-}" ]] || pick ASV_PY "$STACK/venvs/asv/bin/python" \
  "$STACK/venvs/asv/bin/python" /home/aref.farhadipour/data-pipeline/data-env/bin/python
export RFOMNI_ASV_MODELS="${RFOMNI_ASV_MODELS:-$MODELS/asv}"
SPK_MODEL="${SPK_MODEL:-samresnet34}"
SPK_THREADS="${SPK_THREADS:-4}"
DIARIZE_PORT="${DIARIZE_PORT:-8611}"

log() { echo "[rfomni-v5.3] $*"; }

# --- backend checks ---------------------------------------------------------------
# VAD start still uses VADIterator (same as V5.2). Smart Turn v3 needs onnxruntime + soxr
# in the pipecat venv (already present on this box for pipecat 1.8.2.dev20).
VAD_DEPS_OK=1
"$PIPECAT_PY" -c "import silero_vad, torch" 2>/dev/null || VAD_DEPS_OK=0
if [[ "$VAD_DEPS_OK" != "1" ]]; then
  echo "ERROR: silero-vad / torch missing from $PIPECAT_PY — required by VADIterator start" >&2
  echo "       install once, CPU wheels only:" >&2
  echo "         $PIPECAT_PY -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio" >&2
  echo "         $PIPECAT_PY -m pip install --no-deps silero-vad" >&2
  echo "       (or run with OMNI_VAD_ENGINE=silero to use pipecat's analyzer, no torch needed)" >&2
  exit 1
fi
log "VADIterator deps ok (silero-vad + torch cpu)"

SMART_TURN_OK=1
"$PIPECAT_PY" -c "from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3; import onnxruntime, soxr" 2>/dev/null || SMART_TURN_OK=0
if [[ "$SMART_TURN_OK" != "1" ]]; then
  echo "ERROR: Local Smart Turn v3 deps missing from $PIPECAT_PY" >&2
  echo "       need: onnxruntime, soxr, and pipecat with local_smart_turn_v3" >&2
  exit 1
fi
log "Smart Turn v3 deps ok (LocalSmartTurnAnalyzerV3 + onnxruntime + soxr)"

curl -sf -m 5 "${OMNI_BASE_URL}/models" >/dev/null 2>&1 || {
  echo "ERROR: no vLLM-Omni at ${OMNI_BASE_URL}" >&2
  echo "       VOICE=1 GPU_ID=3 VLLM_PORT=8410 bash ~/omni-annotate/realtime/run.sh" >&2
  exit 1
}
log "omni backend ok — ${OMNI_BASE_URL} (model ${OMNI_MODEL})"

if curl -sf -m 3 "${GATE_URL}/health" >/dev/null 2>&1; then
  log "CPU embedder already up on ${GATE_PORT}"
else
  [[ -x "$ASV_PY" ]] || { echo "ERROR: python not found: $ASV_PY" >&2; exit 1; }
  [[ -d "$ASV_DIR" ]] || { echo "ERROR: RFomni-ASV not found: $ASV_DIR" >&2; exit 1; }
  log "starting ${SPK_MODEL} (onnx, CPU, ${SPK_THREADS} threads) on ${GATE_PORT}"
  ( cd "$ASV_DIR" && CUDA_VISIBLE_DEVICES="" SPK_MODEL="$SPK_MODEL" SPK_BACKEND=onnx \
      RFOMNI_ASV_MODELS="${RFOMNI_ASV_MODELS:-}" PYTHONPATH="$ASV_DIR${PYTHONPATH:+:$PYTHONPATH}" \
      SPK_THREADS="$SPK_THREADS" nohup "$ASV_PY" -m rfomni_asv.service \
      --host 127.0.0.1 --port "$GATE_PORT" >/tmp/rfomni_asv_${SPK_MODEL}.log 2>&1 & )
  for _ in $(seq 1 60); do
    curl -sf -m 2 "${GATE_URL}/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf -m 2 "${GATE_URL}/health" >/dev/null || {
    echo "WARN: CPU embedder did not come up — see /tmp/rfomni_asv_${SPK_MODEL}.log" >&2
    echo "      the agent still runs; the speaker controls stay hidden" >&2
  }
fi
curl -sf -m 3 "${GATE_URL}/health" 2>/dev/null \
  | python3 -c "import json,sys;h=json.load(sys.stdin);print('[rfomni-v5.3] embedder: %s dim=%s providers=%s ships threshold %s'%(h.get('model_name'),h.get('dim'),','.join(h.get('providers',[])),h.get('threshold')))" 2>/dev/null || true

if curl -sf -m 3 "http://127.0.0.1:${DIARIZE_PORT}/health" >/dev/null 2>&1; then
  log "diarizer present on ${DIARIZE_PORT} — speaker separation available"
else
  log "no diarizer on ${DIARIZE_PORT} — speaker separation will be unavailable"
fi

# --- client + cert ----------------------------------------------------------------
export OMNI_UI_TITLE="${OMNI_UI_TITLE:-RFomni Playground V5.3}"
bash "$DIR/prepare_rfomni_client.sh"
[[ -f "$DIR/certs/cert.pem" ]] || bash "$DIR/make_cert.sh"

fuser -k "${PORT}/tcp" 2>/dev/null || true
sleep 1

export SSL_CERTFILE="$DIR/certs/cert.pem"
export SSL_KEYFILE="$DIR/certs/key.pem"
export OMNI_BASE_URL OMNI_MODEL
export OMNI_GATE_URL="$GATE_URL"

log "starting agent on :${PORT} (Smart Turn v3 end-of-turn)"
nohup "$PY" qwen3-omni-voice-agent-webrtc.py \
  --host 0.0.0.0 --port "$PORT" -t webrtc \
  --ice-servers stun:stun.l.google.com:19302 >>"$LOG" 2>&1 &

for _ in $(seq 1 30); do
  curl -sk -m 2 "https://127.0.0.1:${PORT}/client/" >/dev/null 2>&1 && break
  sleep 1
done

LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' \
          | grep -vE '^(172\.(1[6-9]|2[0-9]|3[01])\.|169\.254\.|$)' | head -1)"
cat <<EOF

============================================================
  RFOMNI V5.3 — V5.2 + Local Smart Turn v3 end-of-turn

  Standalone UI:  https://${LAN_IP:-127.0.0.1}:${PORT}/client/

  In Open WebUI instead, point it at this agent:
    PIPECAT_BASE_URL=https://127.0.0.1:${PORT}

  Turn detection:
    start = Silero VADIterator (same as V5.2)
    stop  = LocalSmartTurnAnalyzerV3 (smart-turn-v3.2 ONNX)

  Keep V5.2 for silence-timeout reports:
    bash platform/run_password_v52.sh

  Log: ${LOG}
============================================================

EOF
