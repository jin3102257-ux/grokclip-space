@echo off
rem GrokClip Space - Windows launcher. Zero cost, no admin rights, no network.
rem   run_windows.bat                    start the floating orb
rem   run_windows.bat --self-test [out]  generate a sample Space and self-check
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
set "PYW="
set "PYWEXE="
py -3 -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY=py -3"
  set "PYW=pyw -3"
  set "PYWEXE=pyw"
  goto found
)
python -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY=python"
  set "PYW=pythonw"
  set "PYWEXE=pythonw"
  goto found
)

echo.
echo  [GrokClip Space] 未找到可用的 Python 3.9+（需要带 Tkinter）。
echo  请从 Python 官网安装（免费）：https://www.python.org/downloads/windows/
echo  安装时勾选 "Add python.exe to PATH" 和 "tcl/tk and IDLE"，装好后重新双击本文件。
echo.
echo  Python 3.9+ with Tkinter was not found. Install it from python.org, then run this file again.
echo.
pause
exit /b 1

:found
if /i "%~1"=="--self-test" (
  %PY% -m grokclip %*
  exit /b %errorlevel%
)
if /i "%~1"=="--console" (
  rem 调试模式：在当前窗口运行并显示错误信息
  %PY% -m grokclip
  exit /b %errorlevel%
)
where %PYWEXE% >nul 2>&1
if errorlevel 1 (
  start "GrokClip Space" /min %PY% -m grokclip
) else (
  start "" %PYW% -m grokclip
)
exit /b 0
