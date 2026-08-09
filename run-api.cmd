@echo off
setlocal
set AICP_ROOT=%CD%
.venv\Scripts\python.exe -m uvicorn personal_ai_control_plane.api:app --host 127.0.0.1 --port 8765
