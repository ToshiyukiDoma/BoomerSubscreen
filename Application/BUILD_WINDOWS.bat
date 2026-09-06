@echo off
setlocal
cd /d "%~dp0"
if not exist ".build-venv\Scripts\python.exe" (
    py -3 -m venv .build-venv
    if errorlevel 1 goto failed
)
".build-venv\Scripts\python.exe" -m pip install -r build-requirements.txt
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" build_windows.py
if errorlevel 1 goto failed
echo Build complete. See dist\OjiisanSubscreen.
pause
exit /b 0
:failed
echo Build failed. Use a 64-bit Python 3.12 or newer installation.
pause
exit /b 1
