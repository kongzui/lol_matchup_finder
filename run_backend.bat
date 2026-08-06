@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo .venv\Scripts\python.exe not found.
    echo Run run.bat once first, or create the virtual environment manually.
    pause
    exit /b 1
)

chcp 65001 >nul
set "BACKEND_PORT="
for /f "tokens=1,2 delims==" %%A in ('.venv\Scripts\python.exe -X utf8 scripts\dev_server_guard.py backend --print-env') do (
    if /i "%%A"=="BACKEND_PORT" set "BACKEND_PORT=%%B"
)
if not defined BACKEND_PORT (
    echo Failed to select a backend port.
    pause
    exit /b 1
)

echo Starting FastAPI backend on http://127.0.0.1:%BACKEND_PORT%
start "lol-matchup-backend" cmd /k "cd /d %cd% && call .venv\Scripts\activate.bat && %cd%\.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload --no-access-log"
exit /b 0
