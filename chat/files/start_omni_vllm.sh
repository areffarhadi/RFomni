#!/usr/bin/env bash
set -euo pipefail
MODEL=/data/d01/speech/tts/data/Qwen3Omni_Opt/checkpoints/unified_caps_ckpt30000-merged-MXFP4A16mse-fused
VENV=/data/d01/speech/tts/data/Qwen3Omni_Opt/pablo_vllm026
CFG=/home/aref.farhadipour/omni-annotate/configs/vllm_q3_mxfp4_realtime.yaml
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=3
export CUDA_HOME="$VENV/lib/python3.11/site-packages/nvidia/cu13"
export PATH="$CUDA_HOME/bin:$VENV/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$CUDA_HOME/lib"
export NVCC_APPEND_FLAGS="-DCCCL_DISABLE_CTK_COMPATIBILITY_CHECK"
export MODELSCOPE_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export ENABLE_AUDIO_OUTPUT=false
exec "$VENV/bin/vllm" serve "$MODEL" --omni --host 127.0.0.1 --port 8400 \
  --trust-remote-code --served-model-name qwen3-omni-annotate \
  --deploy-config "$CFG" \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --init-timeout 2400 --stage-init-timeout 2400 \
  --no-enable-flashinfer-autotune
