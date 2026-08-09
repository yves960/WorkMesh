#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export AICP_ROOT="$PROJECT_DIR"

exec "$PROJECT_DIR/.venv/bin/python" -m uvicorn \
  personal_ai_control_plane.api:app \
  --host 127.0.0.1 \
  --port 8765
