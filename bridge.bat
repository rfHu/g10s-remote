@echo off
setlocal
cd /d "%~dp0"
title G10S Remote Bridge

echo ============================================
echo   Play/Pause  -^> Enter
echo   Mic key     -^> Ctrl+Win
echo   Ctrl+C to quit
echo ============================================
echo.

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( where py >nul 2>nul && set "PY=py -3" )
if not defined PY ( if exist "D:\Anaconda3\python.exe" set "PY=D:\Anaconda3\python.exe" )
if not defined PY ( if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" )

if not defined PY (
  echo [ERROR] Python not found. Install Python 3, or run: python bridge.py
  echo.
  pause
  exit /b 1
)

echo Using: %PY%
echo Work dir: %CD%
echo.

%PY% bridge.py %*
set "RC=%ERRORLEVEL%"

echo.
echo Bridge stopped, exit code = %RC%
echo Log file: bridge.log
echo.
pause
