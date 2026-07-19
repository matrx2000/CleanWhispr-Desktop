@echo off
REM CleanWispr one-click launcher: creates the Python environment on first run,
REM installs/updates dependencies when needed, then starts the app windowless.
setlocal
cd /d "%~dp0"

REM prefer the py launcher (installed by python.org installers), fall back to python
set "PY=py -3"
%PY% --version >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo Python 3.11+ is required but was not found.
    echo Opening the download page - tick "Add python.exe to PATH" in the installer,
    echo then run this file again.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

REM reuse an existing environment (either name), create venv\ otherwise
set "VENV=venv"
if exist ".venv\Scripts\python.exe" set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo First-time setup: creating the Python environment...
    %PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo Could not create the environment. Is Python 3.11+ installed?
        pause
        exit /b 1
    )
)

REM install dependencies only when requirements.txt changed since last install
fc /b requirements.txt "%VENV%\.requirements.stamp" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies - this can take a few minutes on first run...
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet
    "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed - check your internet connection.
        pause
        exit /b 1
    )
    copy /y requirements.txt "%VENV%\.requirements.stamp" >nul
)

REM pythonw = no console window; the app lives in the system tray
start "" "%VENV%\Scripts\pythonw.exe" main.py
exit /b 0
