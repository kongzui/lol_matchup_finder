@echo off
cd /d "%~dp0"

if not exist "frontend\node_modules" (
    echo frontend\node_modules not found.
    echo Run run.bat once first, or install frontend dependencies manually.
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    chcp 65001 >nul
    ".venv\Scripts\python.exe" -X utf8 scripts\dev_server_guard.py frontend
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

start "lol-matchup-frontend" cmd /k "cd /d %cd%\frontend && npm.cmd run dev"
exit /b 0
