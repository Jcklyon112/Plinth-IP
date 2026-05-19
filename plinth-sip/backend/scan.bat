@echo off
REM Plinth SIP — Auto-Scanner (Phase 5)
REM Usage: scan.bat "Burlington, VT"
REM        scan.bat "05401"
REM        scan.bat "14 Main St, Concord MA"

setlocal

set CONFIGS_DIR=%~dp0..\configs

echo.
echo ================================================================
echo   Plinth SIP — Auto-Scanner
echo ================================================================
echo.

if "%~1"=="" (
    echo Usage: scan.bat "Burlington, VT"
    echo        scan.bat "05401"
    echo        scan.bat "Acton, MA"
    echo.
    echo Make sure the backend is running first:
    echo   Start backend:   cd backend ^&^& uvicorn app.main:app --reload --port 8000
    echo   Start frontend:  cd frontend ^&^& npm run dev
    echo.
    exit /b 1
)

CONFIGS_DIR=%CONFIGS_DIR% venv\Scripts\python.exe scripts\scan.py %*
