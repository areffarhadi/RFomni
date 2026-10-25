#!/usr/bin/env bash
# Start the sidecar heads service on the RTX 4090 (physical GPU 1), local only.
#   bash serve/start.sh            # foreground
#   HEADS_GPU=1 HEADS_PORT=8720 bash serve/start.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export CUDA_DEVICE_ORDER=PCI_BUS_ID                  # match nvidia-smi numbering
export CUDA_VISIBLE_DEVICES="${HEADS_GPU:-1}"        # GPU 0 runs vLLM; keep off it
export PYTHONDONTWRITEBYTECODE=1
PY="${HEADS_PY:-/home/rf/pablo_vllm026/bin/python3}" # has torch (CUDA), transformers, fastapi; nothing is installed into it
exec "$PY" -m uvicorn serve.app:app --host 127.0.0.1 --port "${HEADS_PORT:-8720}" --log-level warning
