#!/usr/bin/env bash
# Copy Pipecat prebuilt UI, rebrand RFOMNI, enable user camera, inject chrome overlay.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PIPECAT_PY:-/home/aref.farhadipour/pipecat/.venv/bin/python3}"
if [[ -z "${PIPECAT_PREBUILT_SRC:-}" ]]; then
  SRC="$("$PY" -c "import os, pipecat_ai_prebuilt; print(os.path.join(os.path.dirname(pipecat_ai_prebuilt.__file__), 'client', 'dist'))")"
else
  SRC="$PIPECAT_PREBUILT_SRC"
fi
DEST="$DIR/client/dist"
OVERLAY="$DIR/client_overlay"
TITLE="${OMNI_UI_TITLE:-RFomni Playground}"

if [[ ! -d "$SRC" ]]; then
  echo "ERROR: prebuilt client not found at $SRC" >&2
  exit 1
fi

rm -rf "$DEST"
mkdir -p "$DEST"
cp -a "$SRC/." "$DEST/"
cp -a "$OVERLAY/." "$DEST/"

JS="$(ls "$DEST"/assets/index-*.js | head -1)"
[[ -f "$JS" ]] || { echo "ERROR: bundled JS missing in $DEST" >&2; exit 1; }

python3 - "$JS" "$TITLE" <<'PY'
import pathlib, sys
js_path, title = sys.argv[1], sys.argv[2]
text = pathlib.Path(js_path).read_text()
# Enable user camera + hide logo + set title; expose RTVI client to RFOMNI chrome.
old = "noUserVideo:!0,logoComponent:(0,V.jsx)(JY,{value:e,onValueChange:t}),onClient:e=>{e.on(R.Connected,async()=>{await o(e)})}"
new = (
    f"noUserVideo:!1,noLogo:!0,titleText:`{title}`,"
    "onClient:e=>{try{window.__RFOMNI_CLIENT__=e;window.__RFOMNI_WIRE__&&window.__RFOMNI_WIRE__(e)}catch(_AG){}"
    "e.on(R.Connected,async()=>{await o(e)})}"
)
if old not in text:
    old2 = "noUserVideo:!0,logoComponent:(0,V.jsx)(JY,{value:e,onValueChange:t}),onClient:"
    new2 = f"noUserVideo:!1,noLogo:!0,titleText:`{title}`,onClient:"
    if old2 not in text:
        print("[rfomni-client] WARN: prebuilt UI patch anchor not found; leaving the bundled client as shipped", file=sys.stderr)
    else:
        text = text.replace(old2, new2, 1)
else:
    text = text.replace(old, new, 1)
# Safari/WebRTC: do not tap the mic MediaStream for the inline button meter.
old3 = "!d&&(0,V.jsx)(yK,{participantType:`local`"
new3 = "!1&&(0,V.jsx)(yK,{participantType:`local`"
if old3 in text:
    text = text.replace(old3, new3, 1)
    print("[rfomni-client] disabled inline mic visualizer (RFOMNI chrome replaces it)")
else:
    print("[rfomni-client] WARN: mic visualizer anchor missing", file=sys.stderr)
pathlib.Path(js_path).write_text(text)
PY

python3 - "$DEST/index.html" "$TITLE" <<'PY'
import pathlib, sys
path, title = pathlib.Path(sys.argv[1]), sys.argv[2]
html = path.read_text()
html = html.replace("<title>Pipecat UI</title>", f"<title>{title}</title>")
html = html.replace("<title>RFomni Playground</title>", f"<title>{title}</title>")
# Version the chrome URLs: the bundle is rebuilt on every start, and without this a
# browser keeps serving the copy it cached from an earlier run.
import time
v = int(time.time())
inject = (
    f'  <link rel="stylesheet" href="./rfomni-chrome.css?v={v}">\n'
    f'  <script src="./rfomni-chrome.js?v={v}" defer></script>\n'
)
if "rfomni-chrome.js" not in html:
    html = html.replace("</head>", inject + "</head>")
path.write_text(html)
PY

echo "[rfomni-client] ready at $DEST (title: ${TITLE}; user video ON; chrome overlay)"
