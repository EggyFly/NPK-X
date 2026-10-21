@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PKG=%~dp0"
set "PY=%PKG%bin\python\python.exe"
if not exist "%PY%" (echo [ERROR] bundled Python not found & pause & exit /b 1)
set "PYTHONUTF8=1"
"%PY%" "%PKG%tools\selfcheck.py"
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
