# 在背景啟動 EHRMS 線上知識庫 MCP，並確認它有回應。已經在跑就不重複啟動。
#   .\start-kb-online.ps1                 預設連接埠 8780，只接受本機連線
#   .\start-kb-online.ps1 -Stop           停止
#   .\start-kb-online.ps1 -Store sqlite   資料表不可用時，改把使用紀錄等資料存在本機檔案
#   .\start-kb-online.ps1 -BindHost 0.0.0.0 -AllowedHosts "主機名稱:8780"    開放給內網其他電腦
# 連線設定（MSSQL_*、JIRA_*）讀取順序見 kb_online/config.py。紀錄檔在 $DataDir\server.log。
param(
    [int]$Port = 8780,
    [string]$BindHost = "127.0.0.1",
    [string]$AllowedHosts = "",
    [ValidateSet("sqlite", "mssql")][string]$Store = "mssql",
    [string]$DataDir = (Join-Path $env:USERPROFILE ".ehrms-tools\kb-online"),
    [switch]$Stop
)

$pidFile = Join-Path $DataDir "server.pid"
$url = "http://127.0.0.1:$Port/health"

function Get-ServerProcess {
    if (-not (Test-Path $pidFile)) { return $null }
    $id = Get-Content $pidFile -ErrorAction SilentlyContinue
    if (-not $id) { return $null }
    return Get-Process -Id $id -ErrorAction SilentlyContinue
}

$running = Get-ServerProcess
if ($Stop) {
    # py 啟動器底下還有真正的 python 行程，要整棵一起結束
    if ($running) { taskkill /PID $running.Id /T /F | Out-Null; Write-Output "已停止（PID $($running.Id)）" }
    else { Write-Output "沒有在執行" }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    exit 0
}
if ($running) { Write-Output "已經在執行（PID $($running.Id)）：http://127.0.0.1:$Port/mcp"; exit 0 }

New-Item -ItemType Directory -Force $DataDir | Out-Null
$env:KB_PORT = "$Port"
$env:KB_HOST = $BindHost
$env:KB_STORE = $Store
$env:KB_DATA_DIR = $DataDir
$env:PYTHONIOENCODING = "utf-8"
if ($AllowedHosts) { $env:KB_ALLOWED_HOSTS = "127.0.0.1:*,localhost:*,$AllowedHosts" }

$script = Join-Path $PSScriptRoot "run_server.py"
$proc = Start-Process -FilePath "py" -ArgumentList @("-3", "-B", "`"$script`"") -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $DataDir "server.out.log") -RedirectStandardError (Join-Path $DataDir "server.log")
# py 啟動器會再開一個 python 行程；記下啟動器的 PID，停止時連同子行程一起結束
Set-Content -Path $pidFile -Value $proc.Id -Encoding ascii

for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri $url -TimeoutSec 2
        Write-Output "已啟動（PID $($proc.Id)）：http://127.0.0.1:$Port/mcp　版本 $($health.version)　資料存放 $($health.store)"
        exit 0
    } catch { }
    if ($proc.HasExited) { break }
}
Write-Output "啟動失敗，請看紀錄檔：$(Join-Path $DataDir 'server.log')"
exit 1
