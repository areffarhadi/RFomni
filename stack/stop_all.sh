#!/usr/bin/env bash
# stop the whole Omni stack (see README.md)
exec python3 "$(dirname "$0")/stack.py" stop "$@"
