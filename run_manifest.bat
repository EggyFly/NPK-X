@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PKG=%~dp0"
set "PY=%PKG%bin\python\python.exe"
set "GAME_ROOT=%~1"
if "%GAME_ROOT%"=="" set /p "GAME_ROOT=Game folder containing char.npk: "
set "OUTFILE=%~2"
set "EXTRA="
if not "%~3"=="" set "EXTRA=%3 %4 %5 %6 %7 %8 %9"
if "%OUTFILE%"=="" set "OUTFILE=%CD%\nxpk_mode3_manifest.csv.gz"
set "PYTHONUTF8=1"
"%PY%" "%PKG%tools\build_mode3_manifest.py" --game-root "%GAME_ROOT%" --paths "%PKG%data\pathdict.txt.gz" --profile "%PKG%data\mode3_profile.json" --out "%OUTFILE%" %EXTRA%
echo.
pause
