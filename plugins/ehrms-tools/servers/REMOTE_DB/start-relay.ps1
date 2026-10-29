# 啟動 relay（只聽本機 8770）與 zrok 固定網址分享 wss://ehrms-relay.shares.zrok.io（第一階段測試用）。
# 用法：powershell -ExecutionPolicy Bypass -File start-relay.ps1
# 停止：關掉 relay 與 zrok2 的程序（工作管理員，或 Stop-Process）。
param([string]$Name = 'ehrms-relay', [int]$Port = 8770)
$ErrorActionPreference = 'Stop'
$zrok = Join-Path $env:LOCALAPPDATA 'Programs\zrok\zrok2.exe'
if (-not (Test-Path $zrok)) { throw "找不到 zrok：$zrok" }

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  Write-Host "port $Port 已有程式在聽，沿用（若不是 relay 請改用 -Port）"
} else {
  Start-Process py -ArgumentList '-3', '-u', (Join-Path $PSScriptRoot 'relay.py'), '--host', '127.0.0.1', '--port', "$Port" -WindowStyle Hidden | Out-Null
  Write-Host "relay 已啟動（127.0.0.1:$Port）"
}

$running = Get-CimInstance Win32_Process -Filter "Name='zrok2.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape("public:$Name") }
if ($running) {
  Write-Host 'zrok 分享已在執行，沿用'
} else {
  # 上次沒正常關閉的分享會繼續佔用名稱，先刪除
  $overview = (& $zrok overview --json 2>$null | Out-String) | ConvertFrom-Json
  $stale = ($overview.names | Where-Object { $_.name -eq $Name -and $_.namespaceToken -eq 'public' }).shareToken
  if ($stale) { & $zrok delete share $stale 2>$null | Out-Null }
  Start-Process $zrok -ArgumentList 'share', 'public', "http://127.0.0.1:$Port", '-n', "public:$Name", '--headless' -WindowStyle Hidden | Out-Null
  Write-Host 'zrok 分享已啟動'
}
Write-Host "客戶端中繼伺服器：wss://$Name.shares.zrok.io"
