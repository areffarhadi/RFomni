#!/usr/bin/env bash
# Open WebUI behind the name-only gate.
#
# Differs from start_remote.sh in exactly two ways, and both matter:
#   1. It binds to 127.0.0.1, not 0.0.0.0. The gate is the only thing on the LAN.
#   2. It trusts X-Auth-Email for identity. That header is a free pass, so it is
#      only safe while nothing but the gate can reach this port.
# TLS is terminated by the gate, so this half speaks plain HTTP over loopback.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"

set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a

export PATH="$ROOT/.venv/bin:$PATH"
export HOST="127.0.0.1"
export PORT="${PORT:-8080}"
export FRONTEND_BUILD_DIR="${FRONTEND_BUILD_DIR:-$ROOT/build}"
export DATA_DIR="${DATA_DIR:-$ROOT/backend/data}"
export ENABLE_OLLAMA_API="${ENABLE_OLLAMA_API:-false}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"

# The gate's public address, not this one — it is what the UI puts in links.
export WEBUI_URL="${WEBUI_URL:-https://10.16.12.60:8443}"

# Identity comes from the gate. On first sight of an address Open WebUI creates
# the account and signs it in; the role header overrides default_user_role,
# which is "pending" here and would otherwise park everyone in a waiting room.
export WEBUI_AUTH_TRUSTED_EMAIL_HEADER="X-Auth-Email"
export WEBUI_AUTH_TRUSTED_NAME_HEADER="X-Auth-Name"
export WEBUI_AUTH_TRUSTED_ROLE_HEADER="X-Auth-Role"

# Everyone signing in through the gate is role "user", and Open WebUI hides any
# base model that has no per-model access grant from that role -- which is every
# model served straight off a connection. With a single shared model on a closed
# LAN there is nothing to partition, so lift the filter rather than minting a
# grant per person.
export BYPASS_MODEL_ACCESS_CONTROL="${BYPASS_MODEL_ACCESS_CONTROL:-true}"

# Pipecat RFOMNI — Voice mode in this UI (signaling proxy)
export PIPECAT_ENABLED="${PIPECAT_ENABLED:-true}"
export PIPECAT_BASE_URL="${PIPECAT_BASE_URL:-https://127.0.0.1:7890}"
export PIPECAT_INSECURE_SSL="${PIPECAT_INSECURE_SSL:-true}"

mkdir -p "$DATA_DIR"

echo "[open-webui] HOST=$HOST PORT=$PORT (loopback only — reach it through the gate)"
echo "[open-webui] trusted identity header: $WEBUI_AUTH_TRUSTED_EMAIL_HEADER"
echo "[open-webui] public URL: $WEBUI_URL"

exec bash "$ROOT/backend/start.sh"
