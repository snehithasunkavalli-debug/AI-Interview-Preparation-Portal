@echo off
title AI Interview Preparation Portal
echo ======================================================
echo   Launching AI Interview Preparation Portal...
echo ======================================================
cd /d "%~dp0"

:: 1. Check Python installation
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3.9+ from https://python.org or check your PATH.
    echo.
    echo In the meantime, you can open index.html directly in any browser!
    start "" index.html
    pause
    exit /b
)

:: 2. Free up port 8080 if an old stuck process is running
echo [*] Checking port 8080...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8080 ^| findstr LISTENING 2^>nul') do (
    echo [*] Clearing old process (PID %%a) from port 8080...
    taskkill /F /PID %%a >nul 2>&1
)

:: 3. Fast dependency check (avoid slow pip install on every launch)
python -c "import flask, waitress" >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] First-time setup: installing dependencies...
    pip install -r requirements.txt
)

:: 4. Start standalone server
echo.
echo [*] Starting Standalone Portal...
python run_standalone.py
pause
