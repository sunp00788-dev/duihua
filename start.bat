@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 对画启动中。请保持这个窗口开着，关掉窗口服务就停。
py -3 server.py
if errorlevel 1 python server.py
echo.
echo 如果上面提示找不到 Python，请先安装 Python 3.11 或更新版本，并勾选 Add python.exe to PATH。
pause
