@echo off
echo ===== EHRMS Tools Plugin - 安裝 Python 依賴 =====
py -3 -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 exit /b 1
echo.
echo Dependencies installed. Restart Claude Code or Codex after configuring the plugin.
pause
