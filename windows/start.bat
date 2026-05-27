@echo off
echo Starting ArcGIS Service Monitor...
echo Backend API: http://localhost:8000
echo Dashboard:   H:\ArcGISMonitor\index.html
echo.
python H:\ArcGISMonitor\monitor_backend.py
pause
