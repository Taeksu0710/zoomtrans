#!/bin/bash
# Optional development launcher; release apps contain their own Python runtime.
set -eu
PROJECT="$(cd -- "$(dirname -- "$0")/../../.." && pwd)"
cd -- "$PROJECT"
if [[ ! -f main.py ]]; then
    echo "ZoomTrans: main.py not found in $PROJECT" >&2
    exit 1
fi
PYTHON="$PROJECT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="/opt/homebrew/bin/python3.11"
fi
exec "$PYTHON" -u main.py "$@"
