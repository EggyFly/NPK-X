@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PKG=%~dp0"
set "PY=%PKG%bin\python\python.exe"
if not exist "%PY%" (echo [ERROR] bundled Python not found: "%PY%" & pause & exit /b 1)
set "GAME_ROOT=%~1"
if "%GAME_ROOT%"=="" set /p "GAME_ROOT=Game folder containing char.npk: "
if "%GAME_ROOT:~-1%"=="\" set "GAME_ROOT=%GAME_ROOT:~0,-1%"
set "OUT=%~2"
if "%OUT%"=="" set "OUT=%CD%\export_mode3"
set "EXTRA="
if not "%~3"=="" set "EXTRA=%3 %4 %5 %6 %7 %8 %9"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%PY%" "%PKG%tools\extract_mode3.py" --game-root "%GAME_ROOT%" --root "%OUT%" --path-dict "%PKG%data\pathdict.txt.gz" --profile "%PKG%data\mode3_profile.json" --jobs 6 %EXTRA%
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [ERROR] exit code %RC%. Read docs\VERSION_COMPATIBILITY.md.
pause
exit /b %RC%
