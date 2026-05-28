@echo off
echo Starting ArcGIS Service Monitor...
echo Dashboard: http://localhost:8000
echo.
python "%~dp0..\monitor_backend.py"
pause
