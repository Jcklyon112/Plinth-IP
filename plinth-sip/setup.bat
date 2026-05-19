@echo off
echo ================================================
echo  Plinth SIP - First Time Setup
echo ================================================
echo.

:: Always run from the folder this .bat file lives in
cd /d "%~dp0"

echo [1/6] Starting fresh database...
docker compose down -v
if errorlevel 1 (
    echo.
    echo ERROR: Docker is not running.
    echo Open Docker Desktop from the Start menu and wait for
    echo the whale icon in the taskbar to stop spinning, then retry.
    goto :fail
)
docker compose up db -d
echo Waiting for database to be ready...
timeout /t 15 /nobreak >nul
echo Done.
echo.

echo [2/6] Creating Python virtual environment...
cd backend
if exist venv (
    echo Removing old virtual environment...
    rmdir /s /q venv
)
python -m venv venv
if errorlevel 1 (
    echo ERROR: Could not create virtual environment. Is Python installed?
    goto :fail
)
echo Done.
echo.

echo [3/6] Installing Python packages...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Package installation failed.
    goto :fail
)
echo Done.
echo.

echo [4/6] Running database migrations...
set DATABASE_URL=postgresql+psycopg://plinth:plinth_dev@localhost:5432/plinth_sip
alembic upgrade head
if errorlevel 1 (
    echo ERROR: Migration failed. Is Docker still running?
    goto :fail
)
echo Done.
echo.

echo [5/6] Seeding database...
set CONFIGS_DIR=..\configs
python scripts\seed.py
echo Done.
echo.

echo [6/6] Installing frontend packages...
cd ..\frontend
where npm >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\nodejs\npm.cmd" (
        "C:\Program Files\nodejs\npm.cmd" install
    ) else (
        echo ERROR: npm not found. Install Node.js LTS from https://nodejs.org
        goto :fail
    )
) else (
    npm install
)
if errorlevel 1 (
    echo ERROR: npm install failed.
    goto :fail
)
echo Done.
echo.

echo ================================================
echo  SUCCESS - Setup complete!
echo  Run start.bat to launch the platform.
echo ================================================
pause
exit /b 0

:fail
echo.
echo ================================================
echo  SETUP FAILED - see error above
echo ================================================
pause
exit /b 1
