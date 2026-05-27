@echo off
setlocal
cd /d "%~dp0.."

echo ============================================
echo  ArcGIS Service Monitor — Build EXE
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://python.org
    pause & exit /b 1
)

REM Install / upgrade build tools
echo [1/3] Installing build dependencies...
pip install --upgrade pyinstaller cryptography >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause & exit /b 1
)

REM Build EXE with PyInstaller
echo [2/3] Building EXE with PyInstaller...
pyinstaller installer\build.spec ^
    --distpath installer\dist ^
    --workpath installer\build ^
    --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    pause & exit /b 1
)

REM Create helper batch for opening dashboard
echo @start "" http://localhost:8000 > installer\dist\ArcGISMonitor\open_dashboard.bat

echo [3/3] Done!
echo.
echo  EXE output : installer\dist\ArcGISMonitor\
echo  Next step  : Open installer\installer.iss with Inno Setup 6 and click Build
echo              Download Inno Setup: https://jrsoftware.org/isinfo.php
echo.
pause
