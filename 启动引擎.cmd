@echo off
setlocal
chcp 65001 >nul
title 启动番茄超市引擎

set "ROOT=%~dp0"
set "FQPC_HOME=%ROOT%runtime"

if not exist "%ROOT%engine\番茄超市.exe" (
  echo [X] 找不到 engine\番茄超市.exe
  echo     请确认压缩包解压完整。
  pause
  exit /b 1
)

echo [1/2] 启动引擎... 引擎的窗口会先出现，随后被番茄文档藏起来。
cd /d "%ROOT%engine"
start "" "%ROOT%engine\番茄超市.exe"

echo [2/2] 等引擎写就绪日志...
set /a n=0
:wait
timeout /t 1 /nobreak >nul
set /a n+=1
if exist "%FQPC_HOME%\rust.log" (
  findstr /c:"core ready" "%FQPC_HOME%\rust.log" >nul 2>&1 && goto ready
)
if %n% lss 25 goto wait

echo [!] 等了 %n% 秒还没看到就绪日志，直接启动番茄文档让它自己重试。
goto launch

:ready
echo [OK] 引擎就绪。

:launch
echo [3/3] 启动番茄文档...
start "" "%ROOT%番茄文档.exe"
exit /b 0