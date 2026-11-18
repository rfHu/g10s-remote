@echo off
chcp 65001 >nul
cd /d "%~dp0"
python keywatch.py
echo.
echo 已退出。日志在同目录 keywatch.log
pause
