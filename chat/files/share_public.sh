#!/usr/bin/env bash
# Put the platform on a public HTTPS link, for people who are not on the LAN or VPN.
#
#   bash share_public.sh              # tunnel Open WebUI (:8080)
#   TARGET_PORT=7880 bash share_public.sh   # tunnel the standalone RFOMNI page instead
#   bash share_public.sh stop
#
# WHY A TUNNEL. This host has only private addresses (10.16.x.x); the public egress
# address is NAT'd with no inbound ports. cloudflared dials *outbound* to Cloudflare and
# traffic returns down that connection, so nothing has to be opened on the firewall.
#
# Two things come free: the URL has a real certificate, so there is no self-signed warning
# and the microphone works without a "Proceed anyway" click.
#
# VOICE MODE CAVEAT. Only signalling crosses the tunnel. WebRTC audio goes peer-to-peer,
# and reaches this host through its STUN-discovered address (srflx). That works through
# most NATs but not all — if a remote listener connects and hears nothing while text chat
# works, the media path is being blocked and a TURN server is the fix.
#
# SECURITY. This exposes the GPU to the public internet behind nothing but Open WebUI's
# login. The quick-tunnel hostname is random and dies with this process, so treat the URL
# as a password and stop the tunnel when the demo is over.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
TARGET_PORT="${TARGET_PORT:-8080}"
CFD="${CFD:-$(command -v cloudflared || echo "$HOME/bin/cloudflared")}"
LOG="${LOG:-/tmp/omni_share_${TARGET_PORT}.log}"
SESSION="omni-share-${TARGET_PORT}"

if [[ "${1:-}" == "stop" ]]; then
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  pkill -u "$(id -u)" -f "cloudflared.*127.0.0.1:${TARGET_PORT}" 2>/dev/null || true
  echo "[share] tunnel for :${TARGET_PORT} stopped"
  exit 0
fi

[[ -x "$CFD" ]] || { echo "ERROR: cloudflared not found at $CFD" >&2; exit 1; }
curl -sk -m 5 "https://127.0.0.1:${TARGET_PORT}/" >/dev/null 2>&1 \
  || echo "WARN: nothing answering on https://127.0.0.1:${TARGET_PORT}" >&2

tmux kill-session -t "$SESSION" 2>/dev/null || true
: > "$LOG"
# --no-tls-verify: the origin serves a self-signed cert; Cloudflare terminates the real
# TLS at its edge, so the browser still gets a valid certificate.
tmux new-session -d -s "$SESSION" \
  "$CFD tunnel --no-autoupdate --no-tls-verify --url https://127.0.0.1:${TARGET_PORT} 2>&1 | tee -a $LOG; exec bash"

echo "[share] waiting for the public hostname…"
URL=""
for _ in $(seq 1 60); do
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
  [[ -n "$URL" ]] && break
  sleep 1
done

if [[ -z "$URL" ]]; then
  echo "ERROR: no tunnel URL appeared — see $LOG" >&2
  exit 1
fi

echo "$URL" > "$ROOT/PUBLIC_LINK.txt"
cat <<EOF

============================================================
  Public link (works without the VPN):

    ${URL}

  Local port tunnelled : ${TARGET_PORT}
  Stop it with         : bash share_public.sh stop
  tmux session         : ${SESSION}
  Log                  : ${LOG}

  Anyone with this URL can reach the login page, so share it
  privately and stop the tunnel when you are done.
============================================================

EOF
