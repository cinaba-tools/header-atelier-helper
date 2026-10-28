@echo off
setlocal EnableExtensions
rem Header Atelier helper: Windows uninstaller (per-user, NO admin).
rem Just double-click this file. Pure batch on purpose: it works even if
rem Python has been removed in the meantime.

set "DIR=%LOCALAPPDATA%\header-atelier-helper"
set "NAME=com.cinaba.header_atelier"
rem Folder and name used up to helper 1.4.x.
set "OLDDIR=%LOCALAPPDATA%\tcportal-staging"
set "OLDNAME=top.abasiletools.tcportal.staging"

for %%D in ("%DIR%" "%OLDDIR%") do call :stopproxy "%%~D"

echo Removing the browser registrations and installed files ...
rem One key per Chromium browser - keep in step with installer.py.
for %%N in ("%NAME%" "%OLDNAME%") do call :unregister "%%~N"
if exist "%DIR%" rmdir /s /q "%DIR%"
if exist "%OLDDIR%" rmdir /s /q "%OLDDIR%"

echo.
echo Done. Also open Chrome and set every hostname back to Production in the
echo Header Atelier popup (or just remove them) so the proxy setting is cleared.
echo.
pause
exit /b 0

:stopproxy
if not exist "%~1\proxy.pid" exit /b 0
echo Stopping the staging proxy ...
set "PROXYPID="
set /p PROXYPID=<"%~1\proxy.pid"
if defined PROXYPID taskkill /PID %PROXYPID% /F >nul 2>&1
exit /b 0

:unregister
reg delete "HKCU\Software\Google\Chrome\NativeMessagingHosts\%~1" /f >nul 2>&1
reg delete "HKCU\Software\Microsoft\Edge\NativeMessagingHosts\%~1" /f >nul 2>&1
reg delete "HKCU\Software\Vivaldi\NativeMessagingHosts\%~1" /f >nul 2>&1
reg delete "HKCU\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\%~1" /f >nul 2>&1
reg delete "HKCU\Software\Chromium\NativeMessagingHosts\%~1" /f >nul 2>&1
exit /b 0
