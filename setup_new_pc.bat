@echo off
rem ============================================================================
rem  One-time setup of the AAC system on a new PC. Run this ONCE after copying the
rem  "Combined System" folder, then use start_aac.bat as usual.
rem    setup_new_pc.bat          install what is missing (asks before big installs)
rem    setup_new_pc.bat check    only report what is missing; changes nothing
rem  Safe to run again at any time. Details: setup_new_pc.ps1, log in logs\setup_*.log
rem ============================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_new_pc.ps1" %1
echo.
pause
