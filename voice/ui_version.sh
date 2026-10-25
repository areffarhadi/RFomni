#!/usr/bin/env bash
# Save, list, and recall named RFOMNI / Pipecat UI + voice-turn presets.
#
#   ./ui_version.sh save [name] ["optional description"]
#   ./ui_version.sh list
#   ./ui_version.sh show <name>
#   ./ui_version.sh use <name> [--no-restart]
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSIONS_DIR="$DIR/ui_versions"
ACTIVE_FILE="$VERSIONS_DIR/active"
ENV_FILE="$DIR/.env"

# Env keys captured in a UI/voice preset (agent + branding).
UI_ENV_KEYS=(
  OMNI_UI_TITLE
  OMNI_MODE
  OMNI_MAX_TOKENS
  OMNI_CONTEXT_TURNS
  OMNI_MAX_CONCURRENT_TURNS
  OMNI_TURN_QUEUE_TIMEOUT
  OMNI_HTTP_MAX_CONNECTIONS
  OMNI_VAD_STOP_SECS
  OMNI_VAD_ENGINE
  OMNI_VAD_ITER_MIN_SILENCE_MS
  OMNI_SMART_TURN_STOP_SECS
  OMNI_SMART_TURN_PRE_SPEECH_MS
  OMNI_SMART_TURN_MAX_DURATION_SECS
  OMNI_SMART_TURN_CPU_COUNT
  OMNI_SMART_TURN_MODEL_PATH
  OMNI_PREROLL_SECS
  OMNI_VAD_CONFIDENCE
  OMNI_VAD_START_SECS
  OMNI_VAD_MIN_VOLUME
  OMNI_RNNOISE
)

_slug() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g'
}

_ensure_env() {
  [[ -f "$ENV_FILE" ]] || cp "$DIR/env.example" "$ENV_FILE"
}

_read_env_value() {
  local key="$1"
  if [[ -f "$ENV_FILE" ]] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    grep "^${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2-
  fi
}

_write_env_ui() {
  local src="$1"
  _ensure_env
  for key in "${UI_ENV_KEYS[@]}"; do
    if grep -q "^${key}=" "$src" 2>/dev/null; then
      val="$(grep "^${key}=" "$src" | tail -1 | cut -d= -f2-)"
      if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
      else
        echo "${key}=${val}" >>"$ENV_FILE"
      fi
    fi
  done
}

cmd_save() {
  local name="${1:-}"
  local desc="${2:-}"
  _ensure_env

  if [[ -z "$name" ]]; then
    local title
    title="$(_read_env_value OMNI_UI_TITLE)"
    title="${title:-RFomni Playground}"
    name="$(_slug "$title")"
    [[ -n "$name" ]] || name="ui-preset"
  else
    name="$(_slug "$name")"
  fi

  if [[ -z "$desc" ]]; then
    desc="Saved $(date -u +%Y-%m-%dT%H:%MZ) — $( _read_env_value OMNI_UI_TITLE || echo RFOMNI )"
  fi

  local vdir="$VERSIONS_DIR/$name"
  mkdir -p "$vdir"

  : >"$vdir/env.ui"
  for key in "${UI_ENV_KEYS[@]}"; do
    val="$(_read_env_value "$key")"
    [[ -n "${val:-}" ]] && echo "${key}=${val}" >>"$vdir/env.ui"
  done

  # Default title if missing from .env
  if ! grep -q '^OMNI_UI_TITLE=' "$vdir/env.ui" 2>/dev/null; then
    echo 'OMNI_UI_TITLE="RFomni Playground"' >>"$vdir/env.ui"
  fi

  if [[ -d "$DIR/client/dist" ]]; then
    tar -czf "$vdir/client-dist.tar.gz" -C "$DIR/client" dist
  else
    bash "$DIR/prepare_rfomni_client.sh"
    tar -czf "$vdir/client-dist.tar.gz" -C "$DIR/client" dist
  fi

  local title created
  title="$(grep '^OMNI_UI_TITLE=' "$vdir/env.ui" | cut -d= -f2-)"
  created="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  python3 - "$vdir/manifest.json" "$name" "$title" "$desc" "$created" <<'PY'
import json, sys
path, name, title, desc, created = sys.argv[1:6]
data = {
    "name": name,
    "title": title,
    "description": desc,
    "created": created,
    "keys": [
        "OMNI_UI_TITLE", "OMNI_MODE", "OMNI_MAX_TOKENS", "OMNI_CONTEXT_TURNS",
        "OMNI_MAX_CONCURRENT_TURNS", "OMNI_TURN_QUEUE_TIMEOUT", "OMNI_HTTP_MAX_CONNECTIONS",
        "OMNI_VAD_STOP_SECS", "OMNI_VAD_ENGINE", "OMNI_VAD_ITER_MIN_SILENCE_MS",
        "OMNI_SMART_TURN_STOP_SECS", "OMNI_SMART_TURN_PRE_SPEECH_MS",
        "OMNI_SMART_TURN_MAX_DURATION_SECS", "OMNI_SMART_TURN_CPU_COUNT",
        "OMNI_SMART_TURN_MODEL_PATH", "OMNI_PREROLL_SECS",
        "OMNI_VAD_CONFIDENCE", "OMNI_VAD_START_SECS", "OMNI_VAD_MIN_VOLUME",
        "OMNI_RNNOISE",
    ],
}
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY

  echo "$name" >"$ACTIVE_FILE"
  echo "[ui-version] saved '$name' → $vdir"
  echo "             title: $title"
  echo "             active preset updated"
}

cmd_list() {
  echo "Saved UI versions (in $VERSIONS_DIR):"
  local active=""
  [[ -f "$ACTIVE_FILE" ]] && active="$(cat "$ACTIVE_FILE")"
  shopt -s nullglob
  for m in "$VERSIONS_DIR"/*/manifest.json; do
    local n t d c mark=""
    n="$(basename "$(dirname "$m")")"
    t="$(python3 -c 'import json; print(json.load(open("'"$m"'")).get("title",""))' 2>/dev/null || echo "?")"
    d="$(python3 -c 'import json; print(json.load(open("'"$m"'")).get("description",""))' 2>/dev/null || echo "")"
    c="$(python3 -c 'import json; print(json.load(open("'"$m"'")).get("created",""))' 2>/dev/null || echo "")"
    [[ "$n" == "$active" ]] && mark=" *"
    echo "  - ${n}${mark}"
    echo "      title: ${t}"
    [[ -n "$d" ]] && echo "      note:  ${d}"
    [[ -n "$c" ]] && echo "      saved: ${c}"
  done
  [[ -z "${active:-}" ]] || echo ""
  [[ -z "${active:-}" ]] || echo "Active: $active"
}

cmd_show() {
  local name="$1"
  name="$(_slug "$name")"
  local vdir="$VERSIONS_DIR/$name"
  [[ -d "$vdir" ]] || { echo "ERROR: unknown version '$name'" >&2; exit 1; }
  echo "=== $name ==="
  [[ -f "$vdir/manifest.json" ]] && cat "$vdir/manifest.json"
  echo "--- env.ui ---"
  cat "$vdir/env.ui"
}

cmd_use() {
  local name="$1"
  local no_restart="${2:-}"
  name="$(_slug "$name")"
  local vdir="$VERSIONS_DIR/$name"
  [[ -d "$vdir" ]] || { echo "ERROR: unknown version '$name'" >&2; exit 1; }

  _write_env_ui "$vdir/env.ui"

  if [[ -f "$vdir/client-dist.tar.gz" ]]; then
    rm -rf "$DIR/client/dist"
    mkdir -p "$DIR/client"
    tar -xzf "$vdir/client-dist.tar.gz" -C "$DIR/client"
    echo "[ui-version] restored frozen client from $name"
  else
    set -a
    # shellcheck disable=SC1091
    . "$vdir/env.ui"
    set +a
    bash "$DIR/prepare_rfomni_client.sh"
    echo "[ui-version] rebuilt client for $name"
  fi

  echo "$name" >"$ACTIVE_FILE"
  local title
  title="$(grep '^OMNI_UI_TITLE=' "$vdir/env.ui" | cut -d= -f2-)"
  echo "[ui-version] active preset: $name ($title)"
  echo "[ui-version] merged settings into .env"

  if [[ "$no_restart" == "--no-restart" ]]; then
    echo "[ui-version] restart the agent to apply voice-turn settings"
    return
  fi

  echo "[ui-version] restarting WebRTC agent on :${UI_PORT:-7860}..."
  fuser -k "${UI_PORT:-7860}/tcp" 2>/dev/null || true
  sleep 1
  PY="${PIPECAT_PY:-/home/aref.farhadipour/pipecat/.venv/bin/python3}"
  set -a
  # shellcheck disable=SC1091
  . "$ENV_FILE"
  set +a
  export SSL_CERTFILE="${SSL_CERTFILE:-$DIR/certs/cert.pem}"
  export SSL_KEYFILE="${SSL_KEYFILE:-$DIR/certs/key.pem}"
  export OMNI_BASE_URL="http://127.0.0.1:${VLLM_PORT:-8410}/v1"
  nohup "$PY" "$DIR/qwen3-omni-voice-agent-webrtc.py" \
    --host "${UI_HOST:-0.0.0.0}" --port "${UI_PORT:-7860}" -t webrtc \
    --ice-servers stun:stun.l.google.com:19302 >>"${AGENT_LOG:-/tmp/omni_pipecat_agent.log}" 2>&1 &
  for _ in $(seq 1 20); do
    curl -sk -m 2 "https://127.0.0.1:${UI_PORT:-7860}/client/" >/dev/null 2>&1 && break
    sleep 1
  done
  echo "[ui-version] agent ready — hard refresh the browser"
}

load_active_version_env() {
  [[ -f "$ACTIVE_FILE" ]] || return 0
  local name vdir
  name="$(cat "$ACTIVE_FILE")"
  vdir="$VERSIONS_DIR/$name"
  [[ -f "$vdir/env.ui" ]] || return 0
  set -a
  # shellcheck disable=SC1091
  . "$vdir/env.ui"
  set +a
}

usage() {
  cat <<EOF
Usage:
  $0 save [name] ["description"]   Save current .env + built UI as a named preset
  $0 list                          List saved presets (* = active)
  $0 show <name>                   Show preset manifest and env
  $0 use <name> [--no-restart]     Apply preset and restart agent

Presets live in: $VERSIONS_DIR
EOF
}

mkdir -p "$VERSIONS_DIR"
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    save) shift; cmd_save "$@" ;;
    list|ls) cmd_list ;;
    show) shift; [[ -n "${1:-}" ]] || { usage; exit 1; }; cmd_show "$1" ;;
    use) shift; [[ -n "${1:-}" ]] || { usage; exit 1; }; cmd_use "$1" "${2:-}" ;;
    -h|--help|help|"") usage ;;
    *) echo "Unknown command: $1" >&2; usage; exit 1 ;;
  esac
fi
