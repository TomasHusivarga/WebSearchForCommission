@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-app.ps1"
if errorlevel 1 (
    echo.
    echo The app could not be opened. Review the message above.
    pause
)
endlocal
