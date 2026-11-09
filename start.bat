@echo off
cd /d "%~dp0"

if not exist venv (
    echo [1/2] Creating virtual environment...
    py -m venv venv
    if errorlevel 1 (
        echo Python not found. Install Python from python.org and check "Add python.exe to PATH".
        pause
        exit /b
    )
    echo [2/2] Installing packages...
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

echo Starting bot... (press Ctrl+C to stop)
python bot.py
pause
