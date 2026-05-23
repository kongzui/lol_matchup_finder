@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo .venv\Scripts\python.exe not found.
    echo Run run.bat once first, or create the virtual environment manually.
    pause
    exit /b 1
)

chcp 65001 >nul
".venv\Scripts\python.exe" -X utf8 scripts\dev_server_guard.py backend
if errorlevel 1 (
    pause
    exit /b 1
)

start "lol-matchup-backend" cmd /k "cd /d %cd% && call .venv\Scripts\activate.bat && python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload"
exit /b 0
