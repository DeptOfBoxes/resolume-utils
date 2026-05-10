@echo off
:: REST Health Monitor — Windows uninstaller

schtasks /delete /tn "REST Health Monitor" /f >nul 2>&1

if errorlevel 1 (
    echo Task not found — nothing to remove.
) else (
    echo REST Health Monitor startup task removed.
)

:: Kill any running instance
taskkill /FI "WINDOWTITLE eq REST Health Monitor*" /F >nul 2>&1

echo Done.
pause
