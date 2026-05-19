@echo off
:: Always run from the folder this .bat file lives in
cd /d "%~dp0"

echo ================================================
echo  Plinth SIP - Load Parcel Data
echo ================================================
echo.

if "%~1"=="" (
    echo HOW TO USE:
    echo   Drag your downloaded Acton zip file onto this .bat file.
    echo.
    echo   OR double-click this file and pass the path:
    echo   ingest_and_score.bat "C:\Downloads\Acton_L3_SHP.zip"
    echo.
    echo Download from:
    echo   https://www.mass.gov/info-details/massgis-data-property-tax-parcels
    echo.
    pause
    exit /b 1
)

set SHAPEFILE=%~1
echo Source: %SHAPEFILE%
echo.

set DATABASE_URL=postgresql+psycopg://plinth:plinth_dev@localhost:5432/plinth_sip
set CONFIGS_DIR=.\configs

echo [1/2] Ingesting parcels...
backend\venv\Scripts\python.exe backend\scripts\ingest.py "%SHAPEFILE%" --municipality ma_acton
if errorlevel 1 (
    echo.
    echo ERROR: Ingestion failed. See above.
    goto :fail
)
echo.

echo [2/2] Scoring parcels...
backend\venv\Scripts\python.exe backend\scripts\score.py --municipality ma_acton
if errorlevel 1 (
    echo.
    echo ERROR: Scoring failed. See above.
    goto :fail
)

echo.
echo ================================================
echo  Done! Open http://localhost:3000 to see the map.
echo ================================================
pause
exit /b 0

:fail
echo.
echo ================================================
echo  FAILED - see error above
echo ================================================
pause
exit /b 1
