@echo off
:: Re-ingest and re-score parcels from MassGIS data.
::
:: Usage: .\reingest.bat "C:\path\to\Acton_Parcels.zip"
::
:: This script will:
::   1. Fetch assessor attributes from MassGIS API (address, zoning, lot size, owner, etc.)
::   2. Ingest TaxPar geometry + join assessor data on LOC_ID
::   3. Score all parcels
cd /d "%~dp0"

set DATABASE_URL=postgresql+psycopg://plinth:plinth_dev@localhost:5432/plinth_sip
set CONFIGS_DIR=..\configs

if "%~1"=="" (
    echo.
    echo Usage: .\reingest.bat "C:\path\to\your\Acton_parcels.zip"
    echo.
    echo Download the zip from:
    echo   https://www.mass.gov/info-details/massgis-data-property-tax-parcels
    echo   Click "Download by Municipality" then find Acton.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Plinth SIP -- Re-ingest + Re-score
echo ============================================================
echo  Data file: %~1
echo  Municipality: ma_acton
echo.

echo [1/4] Fetching assessor attributes from MassGIS API...
echo       (address, owner, zoning, lot size, building area)
venv\Scripts\python.exe scripts\fetch_assessor.py --municipality ma_acton
if errorlevel 1 (
    echo.
    echo ERROR: Could not fetch assessor data from MassGIS API.
    echo Check your internet connection and try again.
    echo If the problem persists, the MassGIS API may be temporarily down.
    pause
    exit /b 1
)

echo.
echo [2/4] Running dry-run to verify joined columns...
venv\Scripts\python.exe scripts\ingest.py "%~1" --municipality ma_acton --dry-run
echo.

echo [3/4] Ingesting parcels (geometry + assessor data)...
venv\Scripts\python.exe scripts\ingest.py "%~1" --municipality ma_acton
if errorlevel 1 (
    echo.
    echo ERROR: Ingestion failed. See above for details.
    pause
    exit /b 1
)

echo.
echo [4/4] Scoring parcels...
venv\Scripts\python.exe scripts\score.py --municipality ma_acton
if errorlevel 1 (
    echo.
    echo ERROR: Scoring failed. See above for details.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done! Refresh the map at http://localhost:3000
echo  Run diagnose to verify: venv\Scripts\python.exe scripts\diagnose.py
echo ============================================================
echo.
pause
