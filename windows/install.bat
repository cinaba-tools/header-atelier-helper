@echo off
setlocal EnableExtensions
rem Header Atelier - Akamai staging switch: Windows installer (per-user, NO admin).
rem Just double-click this file. If SmartScreen warns about a downloaded file,
rem choose "More info" > "Run anyway" - everything stays inside your own account.

set "SCRIPT_DIR=%~dp0"
set "PYEXE="

rem Prefer the py launcher (python.org installs), then python on PATH.
rem The "-c import sys" probe also filters out the WindowsApps store stub.
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)"') do set "PYEXE=%%i"
)
if not defined PYEXE (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)"') do set "PYEXE=%%i"
  )
)
if not defined PYEXE (
  echo Python 3 was not found on this PC.
  echo.
  echo Install it first - NO admin rights needed - from:
  echo   https://www.python.org/downloads/   ^(check "Add python.exe to PATH"^)
  echo or from the Microsoft Store ^(search "Python 3"^).
  echo Then double-click this installer again.
  echo.
  pause
  exit /b 1
)

"%PYEXE%" "%SCRIPT_DIR%installer.py"
echo.
pause
