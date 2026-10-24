@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PKG=%~dp0"
set "PY=%PKG%bin\python\python.exe"
if not exist "%PY%" (echo [ERROR] bundled Python not found: "%PY%" & pause & exit /b 1)
set "CACHE=%LOCALAPPDATA%\EggyNpkBrowser"
set "YES="
if not "%~1"=="" if /i not "%~1"=="--yes" set "CACHE=%~1"
if /i "%~1"=="--yes" set "YES=--yes"
if /i "%~2"=="--yes" set "YES=--yes"
"%PY%" "%PKG%tools\cache_clean.py" --cache-dir "%CACHE%" %YES%
echo.
if /i not "%YES%"=="--yes" pause
