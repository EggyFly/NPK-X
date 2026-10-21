@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PKG=%~dp0"
set "PY=%PKG%bin\python\python.exe"
if not exist "%PY%" (echo [ERROR] bundled Python not found: "%PY%" & pause & exit /b 1)
set "GAME_ROOT=%~1"
if "%GAME_ROOT%"=="" set /p "GAME_ROOT=Game folder containing char.npk: "
set "EXTRA="
if not "%~2"=="" set "EXTRA=%2 %3 %4 %5 %6 %7 %8 %9"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%PY%" "%PKG%tools\rebuild_data.py" --game-root "%GAME_ROOT%" --output "%PKG%data\pathdict.txt.gz" --force %EXTRA%
echo.
pause
