@echo off
cd /d "%~dp0"

if not exist "frontend\node_modules" (
    echo frontend\node_modules not found.
    echo Run run.bat once first, or install frontend dependencies manually.
    pause
    exit /b 1
)

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

chcp 65001 >nul
set "FRONTEND_PORT="
for /f "tokens=1,2 delims==" %%A in ('%PYTHON_EXE% -X utf8 scripts\dev_server_guard.py frontend --print-env') do (
    if /i "%%A"=="FRONTEND_PORT" set "FRONTEND_PORT=%%B"
)
if not defined FRONTEND_PORT (
    echo Failed to select a frontend port.
    pause
    exit /b 1
)

if not defined BACKEND_PORT set "BACKEND_PORT=8000"
echo Starting React frontend on http://127.0.0.1:%FRONTEND_PORT%
start "lol-matchup-frontend" cmd /k "cd /d %cd%\frontend && npm.cmd run dev"
exit /b 0
