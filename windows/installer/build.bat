@echo off
setlocal
cd /d "%~dp0..\.."

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
echo [1/4] Installing build dependencies...
pip install --upgrade pyinstaller cryptography >nul 2>&1
if errorlevel 1 ( echo [ERROR] pip install failed. & pause & exit /b 1 )

REM ---- Step 2: Build EXE with PyInstaller ----
echo [2/4] Building EXE with PyInstaller...
python -m PyInstaller windows\installer\build.spec ^
    --distpath windows\installer\dist ^
    --workpath windows\installer\build ^
    --noconfirm
if errorlevel 1 ( echo [ERROR] PyInstaller build failed. & pause & exit /b 1 )

REM ---- Step 3: Create helper script ----
echo [3/4] Creating helper scripts...
REM open_dashboard.bat URL is rewritten by installer [Code] section at install time
echo @start "" http://localhost:8000 > windows\installer\dist\ArcGISMonitor\open_dashboard.bat

REM ---- Step 4: Build installer with Inno Setup ----
echo [4/4] Building installer...
%ISCC% "windows\installer\installer.iss" /O"windows\installer\output"
if errorlevel 1 ( echo [ERROR] Inno Setup build failed. & pause & exit /b 1 )

echo.
echo  Done!
echo  Output: windows\installer\output\ArcGISMonitor-Setup-1.0.0.exe
echo  Default install path: C:\ArcGISMonitor  (can change during install)
echo.
pause
