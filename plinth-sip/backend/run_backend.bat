@echo off
:: Run from wherever this file lives (backend folder)
cd /d "%~dp0"

set DATABASE_URL=postgresql+psycopg://plinth:plinth_dev@localhost:5432/plinth_sip
set CONFIGS_DIR=..\configs

echo Installing/verifying Python packages...
venv\Scripts\python.exe -m pip install -r requirements.txt --quiet

echo.
echo Running database migrations...
venv\Scripts\python.exe -m alembic upgrade head

echo.
echo Seeding database...
venv\Scripts\python.exe scripts\seed.py

echo.
echo Plinth Backend starting on http://localhost:8000
echo Press Ctrl+C to stop.
echo.

venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

echo.
echo Backend stopped.
pause
