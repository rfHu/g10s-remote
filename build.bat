@echo off
setlocal
cd /d "%~dp0"
title Build G10S-Remote

echo ============================================
echo   Build G10S-Remote
echo ============================================
echo.

REM 版本号单一来源：VERSION 文件（纯 semver，如 0.0.3）
set /p VER=<VERSION
if not defined VER (
  echo [ERROR] VERSION file is empty.
  pause
  exit /b 1
)
echo Version: v%VER%

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( if exist "D:\Anaconda3\python.exe" set "PY=D:\Anaconda3\python.exe" )
if not defined PY ( if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" )
if not defined PY (
  echo [ERROR] Python not found.
  pause
  exit /b 1
)
echo Python: %PY%

%PY% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo.
  echo Installing PyInstaller from pypi.org ...
  %PY% -m pip install --index-url https://pypi.org/simple --disable-pip-version-check pyinstaller
  if errorlevel 1 (
    echo [ERROR] pip install failed. Check network.
    pause
    exit /b 1
  )
)

echo.
echo [1/2] Generating help page ^& icon ...
%PY% tools\make_help.py
if errorlevel 1 (
  echo [ERROR] help page generation failed.
  pause
  exit /b 1
)
%PY% tools\make_icon.py
if errorlevel 1 (
  echo [ERROR] icon build failed.
  pause
  exit /b 1
)

echo.
echo [2/2] Packaging ...
taskkill /IM G10S-Remote.exe /F >nul 2>nul
taskkill /IM G10S-Remote-v%VER%.exe /F >nul 2>nul
timeout /t 1 >nul

%PY% -m PyInstaller --noconfirm --onefile --noconsole --name "G10S-Remote-v%VER%" ^
  --icon "%~dp0app.ico" ^
  --add-data "%~dp0app.ico;." ^
  --add-data "%~dp0app-dialog.ico;." ^
  --add-data "%~dp0app-watch.ico;." ^
  --add-data "%~dp0app-custom.ico;." ^
  --add-data "%~dp0help.html;." ^
  --add-data "%~dp0VERSION;." ^
  --distpath "%~dp0dist" --workpath "%~dp0build" "%~dp0bridge.py"
if errorlevel 1 (
  echo [ERROR] PyInstaller failed.
  pause
  exit /b 1
)

echo.
echo ============================================
echo   Done:  dist\G10S-Remote-v%VER%.exe
echo ============================================
pause
