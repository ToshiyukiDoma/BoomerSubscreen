@echo off
cd /d "%~dp0"
title OjiisanSubscreen SpiceAPI Test

py -3 -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo Installing PySide6...
    py -3 -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Installation failed. Make sure Python 3 is installed.
        pause
        exit /b 1
    )
)

py -3 OjiisanSubscreen.py
if errorlevel 1 (
    echo.
    echo OjiisanSubscreen stopped with an error.
    pause
)
