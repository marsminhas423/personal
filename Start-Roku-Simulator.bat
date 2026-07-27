@echo off
REM ============================================================
REM  Roku Simulator - one-click launcher for Windows
REM  Double-click this file to start the simulator.
REM  Keep it in the SAME folder as roku_sim.py
REM
REM  To change the device name, edit the name in quotes below.
REM ============================================================

title Roku Simulator
cd /d "%~dp0"

set DEVICE_NAME=My Roku Simulator

echo ============================================================
echo   Starting Roku Simulator...
echo ============================================================
echo.

REM Make sure the simulator file is next to this launcher.
if not exist "roku_sim.py" (
    echo ERROR: roku_sim.py was not found in this folder:
    echo   %cd%
    echo.
    echo Put Start-Roku-Simulator.bat in the SAME folder as roku_sim.py
    echo and try again.
    echo.
    pause
    exit /b
)

REM Try the "python" command first, then the "py" launcher.
where python >nul 2>nul
if %errorlevel%==0 (
    python roku_sim.py --name "%DEVICE_NAME%"
    goto done
)

where py >nul 2>nul
if %errorlevel%==0 (
    py roku_sim.py --name "%DEVICE_NAME%"
    goto done
)

echo Python was not found on this PC.
echo Install it from https://www.python.org/downloads/
echo During install, TICK the box "Add python.exe to PATH".

:done
echo.
echo ============================================================
echo   Simulator stopped. Press any key to close this window.
echo ============================================================
pause >nul
