@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 优先用 pythonw 静默启动（不留控制台窗口，更像一个"文档"）
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m tomato_word %*
    exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
    python -m tomato_word %*
    exit /b
)
echo 没有找到 Python。请先安装 Python 3.9+ 并勾选 "Add python.exe to PATH"。
pause
