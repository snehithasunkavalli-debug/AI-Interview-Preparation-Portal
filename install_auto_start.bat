@echo off
echo ========================================================
echo Installing AI Interview Portal to Windows Startup...
echo ========================================================

set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set TARGET_VBS=C:\Users\SNEHITHA\.gemini\antigravity\scratch\ai-interview-portal\start_background.vbs

copy /Y "%TARGET_VBS%" "%STARTUP_DIR%\AI_Interview_Portal.vbs"

echo.
echo [SUCCESS] App added to Startup! 
echo The AI Interview Portal will now run automatically in the background
echo whenever your computer turns on or logs in.
echo.
echo Access URL: http://localhost:8080
echo.
pause
