#!/usr/bin/env bash
# start the whole Omni stack (see README.md)
exec python3 "$(dirname "$0")/stack.py" start "$@"
