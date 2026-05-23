@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

echo Skipping Python requirements install.

if not exist "frontend\node_modules" (
    echo Installing frontend dependencies...
    cd frontend
    call npm.cmd install
    if errorlevel 1 (
        echo Failed to install frontend dependencies.
        pause
        exit /b 1
    )
    cd ..
)

chcp 65001 >nul
python -X utf8 scripts\dev_server_guard.py both
if errorlevel 1 (
    pause
    exit /b 1
)

echo Starting FastAPI backend on http://127.0.0.1:8000
start "lol-matchup-backend" cmd /k "cd /d %cd% && call .venv\Scripts\activate.bat && python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload"

echo Starting React frontend on http://127.0.0.1:5173
start "lol-matchup-frontend" cmd /k "cd /d %cd%\frontend && npm.cmd run dev"

echo Open http://127.0.0.1:5173 in your browser.
exit /b 0
