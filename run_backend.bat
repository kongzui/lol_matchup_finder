@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo .venv\Scripts\python.exe not found.
    echo Run run.bat once first, or create the virtual environment manually.
    pause
    exit /b 1
)

start "lol-matchup-backend" cmd /k "cd /d %cd% && call .venv\Scripts\activate.bat && python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload"
exit /b 0
