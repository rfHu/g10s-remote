@echo off
setlocal
cd /d "%~dp0"
title Build G10S-Remote

echo ============================================
echo   Build G10S-Remote.exe
echo ============================================
echo.

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
echo [1/2] Generating icon ...
%PY% tools\make_icon.py
if errorlevel 1 (
  echo [ERROR] icon build failed.
  pause
  exit /b 1
)

echo.
echo [2/2] Packaging ...
taskkill /IM G10S-Remote.exe /F >nul 2>nul
timeout /t 1 >nul

%PY% -m PyInstaller --noconfirm --onefile --noconsole --name "G10S-Remote" ^
  --icon "%~dp0app.ico" --add-data "%~dp0app.ico;." ^
  --distpath "%~dp0dist" --workpath "%~dp0build" "%~dp0bridge.py"
if errorlevel 1 (
  echo [ERROR] PyInstaller failed.
  pause
  exit /b 1
)

echo.
echo ============================================
echo   Done:  dist\G10S-Remote.exe
echo ============================================
pause
