#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it with: brew install uv" >&2
  exit 1
fi

if ! PYTHON_BIN=$(uv python find --system --no-managed-python --no-python-downloads 2>/dev/null); then
  echo "No compatible system Python found. Install Python matching pyproject.toml and retry." >&2
  exit 1
fi

echo "Using $("$PYTHON_BIN" --version) at $PYTHON_BIN"
uv sync --python "$PYTHON_BIN" --no-dev
AICP_ROOT="$PROJECT_DIR" uv run --python "$PYTHON_BIN" aicp init-db

echo "Personal AI Control Plane v0.4 installed."
echo "Start API: ./run-api.sh"
echo "Admin UI: http://127.0.0.1:8765/admin"
echo "Agent OpenAPI: http://127.0.0.1:8765/openwebui/openapi.json"
