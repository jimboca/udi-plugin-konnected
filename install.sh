#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Prefer FreeBSD package for zeroconf when available (pip often lacks FreeBSD wheels).
if command -v pkg >/dev/null 2>&1; then
  if ! python3 -c 'import zeroconf' >/dev/null 2>&1; then
    echo "NOTE: If pip install of zeroconf fails, run: pkg install py311-zeroconf" >&2
  fi
fi

pip3 install -r requirements.txt --upgrade --user --no-warn-script-location

echo "udi-plugin-konnected dependencies installed."
