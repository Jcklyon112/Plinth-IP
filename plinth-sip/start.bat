@echo off
echo ================================================
echo  Plinth SIP - Starting Platform
echo ================================================
echo.

:: Always run from the folder this .bat file lives in
cd /d "%~dp0"

echo Starting database...
docker compose up db -d
if errorlevel 1 (
    echo.
    echo ERROR: Docker is not running.
    echo Open Docker Desktop from the Start menu, wait for the
    echo whale icon in the taskbar to stop spinning, then retry.
    goto :fail
)
timeout /t 5 /nobreak >nul
echo Database ready.
echo.

echo Starting backend API...
start "Plinth Backend" cmd /k ""%~dp0backend\run_backend.bat""

echo Waiting for backend to start...
timeout /t 8 /nobreak >nul

echo Starting frontend...
start "Plinth Frontend" cmd /k ""%~dp0frontend\run_frontend.bat""

echo.
echo ================================================
echo  Platform is starting up!
echo.
echo  Go to: http://localhost:3000
echo  Wait about 15 seconds for everything to load.
echo.
echo  To stop: close the Backend and Frontend windows.
echo ================================================
pause
exit /b 0

:fail
echo.
pause
exit /b 1
