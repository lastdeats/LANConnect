@echo off
setlocal
cd /d "%~dp0"
py -3.12 -m pip install -r requirements-windows.txt
if errorlevel 1 goto error
py -3.12 build_windows.py
if errorlevel 1 goto error
echo.
echo Built and smoke-tested binaries in dist. Device tests are still required.
pause
exit /b 0
:error
echo Build failed. Requires Windows, Python 3.12, PyInstaller and Pillow.
pause
exit /b 1
