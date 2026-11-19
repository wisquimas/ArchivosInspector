@echo off
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo No se encontro Python en el PATH.
  pause
  exit /b 1
)
python server.py
