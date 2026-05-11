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

if not exist ".venv\.installed" (
    echo Installing requirements...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install requirements.
        pause
        exit /b 1
    )
    type nul > ".venv\.installed"
)

python -m streamlit run app.py
pause
