@echo off
echo.
echo ========================================
echo Discord Bot - Local Run
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python from https://www.python.org/
    pause
    exit /b 1
)

REM Check if venv exists, create if not
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate venv
call venv\Scripts\activate.bat

REM Install requirements
echo Installing dependencies...
pip install -q -r requirements.txt

REM Check for .env file
if not exist ".env" (
    echo.
    echo ERROR: .env file not found!
    echo Please create a .env file with your bot token:
    echo   BOT_TOKEN=your_discord_bot_token_here
    echo.
    pause
    exit /b 1
)

REM Run bot
echo.
echo Starting bot...
echo.
python main.py

pause
