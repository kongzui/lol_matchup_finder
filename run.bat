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

echo Installing Python requirements...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install Python requirements.
    pause
    exit /b 1
)

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

echo Starting FastAPI backend on http://127.0.0.1:8000
start "lol-matchup-backend" cmd /k "cd /d %cd% && call .venv\Scripts\activate.bat && python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload"

echo Starting React frontend on http://127.0.0.1:5173
start "lol-matchup-frontend" cmd /k "cd /d %cd%\frontend && npm.cmd run dev"

echo Open http://127.0.0.1:5173 in your browser.
pause
