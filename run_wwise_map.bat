@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PKG=%~dp0"
set "PY=%PKG%bin\python\python.exe"
if not exist "%PY%" (echo [ERROR] bundled Python not found: "%PY%" & pause & exit /b 1)
set "GAME_ROOT=%~1"
if "%GAME_ROOT%"=="" set /p "GAME_ROOT=Game folder containing char.npk: "
set "OUT=%~2"
if "%OUT%"=="" set "OUT=%PKG%wwise_media_map.csv"
set "EXTRA="
if not "%~3"=="" set "EXTRA=%3 %4 %5 %6 %7 %8 %9"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%PY%" "%PKG%tools\map_wwise_media.py" --game-root "%GAME_ROOT%" --path-dict "%PKG%data\pathdict.txt.gz" --profile "%PKG%data\mode3_profile.json" --csv "%OUT%" %EXTRA%
echo.
pause
