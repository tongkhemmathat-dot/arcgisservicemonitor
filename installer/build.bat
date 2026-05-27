@echo off
setlocal
cd /d "%~dp0.."

echo ============================================
echo  ArcGIS Service Monitor — Build Installer
echo ============================================
echo.

REM ---- Check Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://python.org
    pause & exit /b 1
)

REM ---- Check Inno Setup ----
set ISCC=""
for %%p in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do if exist %%p set ISCC=%%p

if %ISCC%=="" (
    echo [ERROR] Inno Setup 6 not found.
    echo         Download from: https://jrsoftware.org/isinfo.php
    pause & exit /b 1
)

REM ---- Step 1: Install build dependencies ----
echo [1/5] Installing build dependencies...
pip install --upgrade pyinstaller cryptography >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause & exit /b 1
)

REM ---- Step 2: Build EXE with PyInstaller ----
echo [2/5] Building EXE with PyInstaller...
pyinstaller installer\build.spec ^
    --distpath installer\dist ^
    --workpath installer\build ^
    --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    pause & exit /b 1
)

REM ---- Step 3: Create open_dashboard helper ----
echo [3/5] Creating helper scripts...
echo @start "" http://localhost:%%1 > installer\dist\ArcGISMonitor\open_dashboard.bat

REM ---- Step 4: Build Inno Setup installers ----
echo [4/5] Building installers...

echo   - Dev     (port 8001)...
%ISCC% "installer\installer-dev.iss" /O"installer\output"
if errorlevel 1 ( echo [ERROR] Dev installer failed. & pause & exit /b 1 )

echo   - Staging (port 8002)...
%ISCC% "installer\installer-staging.iss" /O"installer\output"
if errorlevel 1 ( echo [ERROR] Staging installer failed. & pause & exit /b 1 )

echo   - Production (port 8000)...
%ISCC% "installer\installer-prod.iss" /O"installer\output"
if errorlevel 1 ( echo [ERROR] Production installer failed. & pause & exit /b 1 )

REM ---- Done ----
echo [5/5] Done!
echo.
echo  Output installers:
echo    installer\output\ArcGISMonitor-Dev-Setup-1.0.0.exe
echo    installer\output\ArcGISMonitor-Staging-Setup-1.0.0.exe
echo    installer\output\ArcGISMonitor-Production-Setup-1.0.0.exe
echo.
echo  Install paths (default, can change during install):
echo    Dev        : C:\ArcGISMonitor\dev\       port 8001
echo    Staging    : C:\ArcGISMonitor\staging\   port 8002
echo    Production : C:\ArcGISMonitor\prod\      port 8000
echo.
pause
