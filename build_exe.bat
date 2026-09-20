@echo off
REM Builds bioprint.exe (Windows) from this folder's source.
REM Double-click this file. When it finishes, find the exe at:
REM   dist\bioprint.exe
REM
REM Requires Python (with pip) installed and on PATH: https://python.org
REM (check "Add python.exe to PATH" during install)

cd /d "%~dp0"

echo Installing build requirements (Flask + PyInstaller)...
python -m pip install --quiet -r requirements.txt
python -m pip install --quiet pyinstaller
if errorlevel 1 (
    echo.
    echo Could not install requirements. Make sure Python and pip are
    echo installed and on PATH, then re-run this file.
    pause
    exit /b 1
)

echo Building bioprint.exe ...
python -m PyInstaller ^
    --onefile ^
    --console ^
    --name bioprint ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --hidden-import=database ^
    --hidden-import=behavioral_engine ^
    bioprint_launcher.py

if errorlevel 1 (
    echo.
    echo Build failed - see the errors above.
    pause
    exit /b 1
)

echo.
echo Done! Your executable is at: dist\bioprint.exe
echo Copy dist\bioprint.exe anywhere and double-click it to run BioPrint.
echo A bioprint.db file will be created next to it on first run.
pause
