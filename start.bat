@echo off
title AI Interview Preparation Portal
echo ======================================================
echo Launching AI Interview Preparation Portal...
echo ======================================================
cd /d "%~dp0"
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3.9+ from https://python.org
    pause
    exit /b
)
echo Installing / checking dependencies...
pip install -r requirements.txt
echo.
echo Starting Standalone Portal...
python run_standalone.py
pause
