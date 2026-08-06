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
set "BACKEND_PORT="
set "FRONTEND_PORT="
for /f "tokens=1,2 delims==" %%A in ('python -X utf8 scripts\dev_server_guard.py both --print-env') do (
    if /i "%%A"=="BACKEND_PORT" set "BACKEND_PORT=%%B"
    if /i "%%A"=="FRONTEND_PORT" set "FRONTEND_PORT=%%B"
)
if not defined BACKEND_PORT (
    echo Failed to select a backend port.
    pause
    exit /b 1
)
if not defined FRONTEND_PORT (
    echo Failed to select a frontend port.
    pause
    exit /b 1
)

echo Starting FastAPI backend on http://127.0.0.1:%BACKEND_PORT%
start "lol-matchup-backend" cmd /k "cd /d %cd% && call .venv\Scripts\activate.bat && %cd%\.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload"

echo Starting React frontend on http://127.0.0.1:%FRONTEND_PORT%
start "lol-matchup-frontend" cmd /k "cd /d %cd%\frontend && npm.cmd run dev"

echo Open http://127.0.0.1:%FRONTEND_PORT% in your browser.
exit /b 0
