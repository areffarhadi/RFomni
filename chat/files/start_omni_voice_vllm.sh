#!/usr/bin/env bash
# Serve Qwen3-Omni with talker (speech out) for Open WebUI.
# Same path as: VOICE=1 bash omni-annotate/realtime/run.sh
set -euo pipefail
MODEL=/data/d01/speech/tts/data/Qwen3Omni_Opt/checkpoints/unified_caps_ckpt30000-MXFP4A16mse-VOICE
VENV=/data/d01/speech/tts/data/Qwen3Omni_Opt/pablo_vllm026
CFG=/home/aref.farhadipour/omni-annotate/configs/vllm_q3_mxfp4_voice.yaml
PORT="${VLLM_PORT:-8410}"
GPU_ID="${GPU_ID:-3}"

[[ -d "$MODEL" ]] || { echo "ERROR: missing $MODEL" >&2; exit 1; }
[[ -f "$CFG" ]] || { echo "ERROR: missing $CFG" >&2; exit 1; }

export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES="$GPU_ID"
export CUDA_HOME="$VENV/lib/python3.11/site-packages/nvidia/cu13"
export PATH="$CUDA_HOME/bin:$VENV/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$CUDA_HOME/lib"
export NVCC_APPEND_FLAGS="-DCCCL_DISABLE_CTK_COMPATIBILITY_CHECK"
export MODELSCOPE_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# Voice checkpoint has enable_audio_output: true — do NOT set ENABLE_AUDIO_OUTPUT=false

echo "[omni-voice] model=$(basename "$MODEL") gpu=$GPU_ID port=$PORT"
exec "$VENV/bin/vllm" serve "$MODEL" --omni --host 127.0.0.1 --port "$PORT" \
  --trust-remote-code --served-model-name qwen3-omni-voice \
  --stage-configs-path "$CFG" \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --init-timeout 2400 --stage-init-timeout 2400 \
  --no-enable-flashinfer-autotune
