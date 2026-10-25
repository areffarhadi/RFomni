#!/usr/bin/env bash
# Start Open WebUI bound for remote LAN access (HTTPS required for mic/dictate).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"

set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a

export PATH="$ROOT/.venv/bin:$PATH"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8080}"
export FRONTEND_BUILD_DIR="${FRONTEND_BUILD_DIR:-$ROOT/build}"
export DATA_DIR="${DATA_DIR:-$ROOT/backend/data}"
export ENABLE_OLLAMA_API="${ENABLE_OLLAMA_API:-false}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
export WEBUI_URL="${WEBUI_URL:-https://10.16.12.60:${PORT}}"

# Pipecat RFOMNI — Voice mode in this UI (signaling proxy)
export PIPECAT_ENABLED="${PIPECAT_ENABLED:-true}"
export PIPECAT_BASE_URL="${PIPECAT_BASE_URL:-https://127.0.0.1:7860}"
export PIPECAT_INSECURE_SSL="${PIPECAT_INSECURE_SSL:-true}"

CERT_DIR="${CERT_DIR:-/home/aref.farhadipour/omni-annotate/realtime/certs}"
SSL_CERT="${SSL_CERTFILE:-$CERT_DIR/cert.pem}"
SSL_KEY="${SSL_KEYFILE:-$CERT_DIR/key.pem}"

mkdir -p "$DATA_DIR"

echo "[open-webui] HOST=$HOST PORT=$PORT"
echo "[open-webui] OPENAI_API_BASE_URL=${OPENAI_API_BASE_URL:-}"
echo "[open-webui] FRONTEND_BUILD_DIR=$FRONTEND_BUILD_DIR"
echo "[open-webui] Remote URL: https://10.16.12.60:${PORT}"
echo "[open-webui] TLS: $SSL_CERT"

# Pass SSL to uvicorn via start.sh extra args
exec bash "$ROOT/backend/start.sh" \
  --ssl-certfile "$SSL_CERT" \
  --ssl-keyfile "$SSL_KEY"
