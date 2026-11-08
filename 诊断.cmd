@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 番茄文档 自检 ===
python -m tomato_word --check %*
echo.
set "LOG=%~dp0runtime\rust.log"
if not exist "%LOG%" set "LOG=%LOCALAPPDATA%\TomatoWord\runtime\rust.log"
echo === 引擎日志（最后 30 行） ===
echo %LOG%
if exist "%LOG%" (
    powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 30"
) else (
    echo 还没有日志，说明引擎还没成功启动过。
)
pause
