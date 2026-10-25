#!/usr/bin/env bash
# Start Omni Studio locally:  ./start.sh   (then open http://127.0.0.1:8710)
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Creating Python environment (.venv)…"
  python3 -m venv .venv
fi
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "Installing dependencies…"
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  touch .venv/.installed
fi

# landing page font (downloaded per install; its license does not allow committing it)
[ -f frontend/landing/fonts/satoshi/Satoshi-Regular.woff2 ] || .venv/bin/python scripts/get_fonts.py --quiet || true

PORT="${OMNI_STUDIO_PORT:-8710}"
export OMNI_STUDIO_PORT="$PORT"
if [ "${OMNI_NO_BROWSER:-0}" != "1" ]; then
  (sleep 1.5 && .venv/bin/python -c "import webbrowser; webbrowser.open('http://127.0.0.1:$PORT')") &
fi
exec .venv/bin/python -m backend
