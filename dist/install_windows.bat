@echo off
:: REST Health Monitor — Windows auto-launch installer
:: Creates a Task Scheduler task that starts the floating panel at each login.

set SCRIPT_DIR=%~dp0
set SCRIPT=%SCRIPT_DIR%rest_health_monitor_ui.py

echo REST Health Monitor — Windows installer
echo.

:: Verify Python is available
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Install from https://python.org  ^(check "Add to PATH" during setup^)
    pause
    exit /b 1
)

:: Verify tkinter is available (not included in Microsoft Store Python)
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo ERROR: tkinter not available.
    echo Install Python from https://python.org  ^(not the Microsoft Store version^).
    pause
    exit /b 1
)

:: Create the scheduled task (runs at logon for current user, no elevation needed)
schtasks /create ^
  /tn "REST Health Monitor" ^
  /tr "python \"%SCRIPT%\"" ^
  /sc onlogon ^
  /rl limited ^
  /f >nul

if errorlevel 1 (
    echo ERROR: Failed to create scheduled task.
    echo Try running this script as Administrator.
    pause
    exit /b 1
)

echo Installed successfully.
echo.
echo   Start now:  schtasks /run /tn "REST Health Monitor"
echo   Remove:     uninstall_windows.bat
echo.
pause
