@echo off
REM CleanWispr one-click build: sets up the Python environment (with build
REM tooling) if needed, then produces the standalone Windows app in dist\.
setlocal
cd /d "%~dp0.."

set "PY=py -3"
%PY% --version >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo Python 3.11+ is required but was not found.
    echo Install it from https://www.python.org/downloads/ - tick "Add python.exe
    echo to PATH" in the installer - then run this file again.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

set "VENV=venv"
if exist ".venv\Scripts\python.exe" set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo First-time setup: creating the Python environment...
    %PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo Could not create the environment.
        pause
        exit /b 1
    )
)

REM build tooling (includes runtime deps + PyInstaller); reinstall on change
fc /b requirements-build.txt "%VENV%\.requirements-build.stamp" >nul 2>nul
if errorlevel 1 (
    echo Installing build dependencies - this can take a few minutes...
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet
    "%VENV%\Scripts\python.exe" -m pip install -r requirements-build.txt
    if errorlevel 1 (
        echo Dependency installation failed - check your internet connection.
        pause
        exit /b 1
    )
    copy /y requirements-build.txt "%VENV%\.requirements-build.stamp" >nul
)

echo Building - this takes a few minutes...
"%VENV%\Scripts\python.exe" scripts\build_windows.py
if errorlevel 1 (
    echo Build failed - see the messages above.
    pause
    exit /b 1
)
echo.
echo Done. Your app is in dist\CleanWispr\CleanWispr.exe
echo (portable zip: dist\CleanWispr-portable-win64.zip)
pause
exit /b 0
