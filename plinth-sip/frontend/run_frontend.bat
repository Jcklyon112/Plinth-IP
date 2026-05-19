@echo off
:: Run from wherever this file lives (frontend folder)
cd /d "%~dp0"

echo Plinth Frontend starting on http://localhost:3000
echo Press Ctrl+C to stop.
echo.

npm run dev

echo.
echo Frontend stopped.
pause
