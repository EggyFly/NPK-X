@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PKG=%~dp0"
set "PY=%PKG%bin\python\python.exe"
set "TARGET=%~1"
if "%TARGET%"=="" set /p "TARGET=Export folder: "
if "%TARGET%"=="" set "TARGET=%CD%\export_mode3"
set "PYTHONUTF8=1"
"%PY%" "%PKG%tools\verify_export.py" "%TARGET%"
echo.
pause
