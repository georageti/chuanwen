@echo off
chcp 65001 >nul
rem 双击启动川文：已经在运行就只打开网页；没在运行就在后台启动（不留黑色窗口）
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo 还没安装。请先双击「安装川文.bat」。
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" run.py
