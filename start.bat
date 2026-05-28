@echo off
REM Boots the ADU Rent Calculator locally.
REM   - Backend (FastAPI + SQLite) on http://localhost:8000
REM   - Frontend (Vite)            on http://localhost:3001
REM Each runs in its own console window so logs stay separate and either can be Ctrl+C'd independently.

setlocal
set ROOT=%~dp0

if not exist "%ROOT%backend\venv\Scripts\activate.bat" (
    echo.
    echo [start.bat] Backend venv not found at backend\venv. Create it once:
    echo     cd backend
    echo     python -m venv venv
    echo     venv\Scripts\activate
    echo     pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "%ROOT%frontend\node_modules" (
    echo.
    echo [start.bat] Frontend node_modules not found. Run once:
    echo     cd frontend
    echo     npm install
    echo.
    pause
    exit /b 1
)

if not exist "%ROOT%backend\.env" (
    echo.
    echo [start.bat] backend\.env not found. Copy and fill it:
    echo     copy ..\.env.example backend\.env
    echo Then set RENTCAST_API_KEY and HUD_API_TOKEN.
    echo.
    pause
    exit /b 1
)

start "Plinth ADU - Backend"  cmd /k "cd /d %ROOT%backend && call venv\Scripts\activate && uvicorn app.main:app --reload --port 8000"
start "Plinth ADU - Frontend" cmd /k "cd /d %ROOT%frontend && npm run dev"

echo.
echo Started:
echo   Backend:  http://localhost:8000   (API docs at /docs)
echo   Frontend: http://localhost:3001
echo.
echo Close the two console windows to stop. This window can be closed safely.
echo.
endlocal
