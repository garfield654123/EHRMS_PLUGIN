@echo off
echo ===== EHRMS Tools Plugin - 安裝 Python 依賴 =====
pip install -r "%~dp0requirements.txt"
echo.
echo 完成！請重新啟動 Claude Code 讓 MCP 生效。
pause
