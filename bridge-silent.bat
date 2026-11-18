@echo off
cd /d "%~dp0"

set "PYW="
where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW ( if exist "D:/Anaconda3/pythonw.exe" set "PYW=D:/Anaconda3/pythonw.exe" )
if not defined PYW ( if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe" set "PYW=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe" )

if not defined PYW (
  echo [ERROR] pythonw.exe not found. Use bridge.bat instead.
  pause
  exit /b 1
)

start "" %PYW% "%~dp0bridge.py"
exit /b 0
