@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo Python 3.10 or newer was not found.
  echo Install it from https://www.python.org/downloads/ and enable "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

echo Starting ACG Radar...
echo Keep this window open while using the app.
echo Press Ctrl+C to stop.
echo.
%PY% app.py --open
if errorlevel 1 (
  echo.
  echo ACG Radar stopped with an error.
  pause
)
endlocal
