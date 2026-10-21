@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PKG=%~dp0"
set "PY=%PKG%bin\python\python.exe"
set "GAME_ROOT=%~1"
if "%GAME_ROOT%"=="" set /p "GAME_ROOT=Game folder containing char.npk: "
set "EXTRA="
if not "%~2"=="" set "EXTRA=%2 %3 %4 %5 %6 %7 %8 %9"
set "PYTHONUTF8=1"
"%PY%" "%PKG%tools\validate_npk_mode3.py" --game-root "%GAME_ROOT%" --paths "%PKG%data\pathdict.txt.gz" --profile "%PKG%data\mode3_profile.json" --output-dir "%CD%\_validation_reports" %EXTRA%
echo.
pause
