@echo off
REM desk launcher for Windows. Double-click to start the app; it opens in your browser.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)

%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run this again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating a virtual environment and installing dependencies...
  %PY% -m venv .venv || (echo venv failed & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || (echo pip install failed & pause & exit /b 1)
)

echo Starting desk... close this window to stop it.
".venv\Scripts\python.exe" server.py
pause
