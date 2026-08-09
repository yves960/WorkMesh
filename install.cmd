@echo off
setlocal

where uv >nul 2>nul
if errorlevel 1 (
  echo uv is required. Install it from https://docs.astral.sh/uv/ 1>&2
  exit /b 1
)

set "PYTHON_BIN="
for /f "delims=" %%I in ('uv python find --system --no-managed-python --no-python-downloads 2^>nul') do set "PYTHON_BIN=%%I"
if not defined PYTHON_BIN (
  echo No compatible system Python found. Install Python matching pyproject.toml and retry. 1>&2
  exit /b 1
)

echo Using Python at %PYTHON_BIN%
uv sync --python "%PYTHON_BIN%" --no-dev
if errorlevel 1 exit /b 1
set "AICP_ROOT=%CD%"
uv run --python "%PYTHON_BIN%" aicp init-db
if errorlevel 1 exit /b 1

echo.
echo Personal AI Control Plane v0.4 installed.
echo Start API: run-api.cmd
echo Admin UI: http://127.0.0.1:8765/admin
echo CLI: .venv\Scripts\aicp.exe context list
