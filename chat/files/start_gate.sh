#!/usr/bin/env bash
# The LAN-facing half: terminates TLS, asks each visitor for a name once, and
# forwards to Open WebUI on loopback with the identity header attached.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

CERT_DIR="${CERT_DIR:-/home/aref.farhadipour/omni-annotate/realtime/certs}"
export GATE_CERTFILE="${GATE_CERTFILE:-$CERT_DIR/cert.pem}"
export GATE_KEYFILE="${GATE_KEYFILE:-$CERT_DIR/key.pem}"
export GATE_PORT="${GATE_PORT:-8443}"
export GATE_HOST="${GATE_HOST:-0.0.0.0}"
export GATE_UPSTREAM="${GATE_UPSTREAM:-http://127.0.0.1:8080}"
export GATE_TITLE="${GATE_TITLE:-RFomni Playground}"

# Names that resolve to an account that already exists, so its owner keeps their
# admin rights and their history instead of being handed a blank identity.
export GATE_RESERVED="${GATE_RESERVED:-aref=areffarhadi@gmail.com:admin}"

exec "$ROOT/.venv/bin/python" "$ROOT/owui_gate.py"
