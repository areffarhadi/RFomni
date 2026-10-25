#!/usr/bin/env bash
# Self-signed TLS for Pipecat LAN mic access (same rules as omni-annotate).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/certs"
mkdir -p "$DIR"
CRT="$DIR/cert.pem"
KEY="$DIR/key.pem"
[[ -f "$CRT" && -f "$KEY" && "${FORCE:-0}" != "1" ]] && exit 0
SANS="DNS:localhost,IP:127.0.0.1"
for ip in $(hostname -I 2>/dev/null); do
  case "$ip" in 172.1[6-9].*|172.2[0-9].*|172.3[0-1].*|169.254.*) continue ;; esac
  SANS="$SANS,IP:$ip"
done
openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
  -keyout "$KEY" -out "$CRT" \
  -subj "/CN=$(hostname -s) pipecat-omni" \
  -addext "subjectAltName=$SANS" \
  -addext "basicConstraints=CA:FALSE" \
  -addext "keyUsage=digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" 2>/dev/null
chmod 600 "$KEY"
echo "[cert] $CRT"
