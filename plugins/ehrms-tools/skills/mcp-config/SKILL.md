---
name: mcp-config
description: 管理 Claude Code MCP 伺服器設定，支援新增 JIRA MCP 與 DB MCP、查看目前設定清單、確認連線狀態。適用所有專案。Use when setting up new MCP servers, listing current MCP configurations, or checking connection status.
---

# MCP 配置管理

## JIRA Issue 編號規則

**當使用者只提供數字（例如 `27046`）時，一律自動補上 `EHRMSONE-` 前綴，視為 `EHRMSONE-27046`。**
僅在使用者明確提供完整 Key（例如 `NPR-123`、`CSHR-456`）時，才使用其指定的前綴。

---

| MCP | 用途 | Repo |
|-----|------|------|
| **JIRA MCP** | 查詢 Jira Cloud issues | `garfield654123/JIRA_MCP` |
| **DB MCP** | EHRMS MSSQL 資料庫查詢 | `garfield654123/MCP_1.0` |

## 指令對照

| 指令 | 說明 |
|------|------|
| `/mcp-config` | 查看目前專案 MCP 設定與連線狀態 |
| `/mcp-config add [jira\|db]` | 新增 MCP（不帶參數則互動選擇）|
| `/mcp-config cli-list` | 執行 `claude mcp list` 顯示 CLI 清單 |
| `/mcp-config cli-add` | 用 `claude mcp add` 指令新增 MCP |

---

## A. 查看清單（`/mcp-config` 或 `/mcp-config cli-list`）

依序執行三個檢查，整合輸出：

1. **讀取 `.claude.json`**：找出目前專案 `mcpServers`（密碼以 `****` 遮蔽）
2. **執行 `claude mcp list`**（Bash 工具）：取得 CLI 已知清單，與 `.claude.json` 對照
3. **呼叫 `ListMcpResourcesTool`**：確認實際連線中的 server

輸出格式：
```
【目前專案 MCP 設定】C:\D\xxx

1. ehrms-database
   指令：py C:\D\MCP\server.py  |  DB：<DB_SERVER_IP> / <DB_NAME>
   CLI：✅ 可見  |  連線：✅ 中

2. jira-mcp
   指令：py -m jira_mcp  |  PYTHONPATH：C:\D\JIRA_MCP
   CLI：⚠️ 未出現  |  連線：❌ 未連線
```

若無設定：顯示「此專案尚未設定 MCP，輸入 /mcp-config add 開始新增」

---

## B. 新增 MCP（`/mcp-config add [jira|db]`）

不帶參數時顯示選單（JIRA / DB / 兩者都新增），再執行對應流程。

### 共同步驟

1. **詢問安裝路徑**（JIRA 預設 `C:\D\JIRA_MCP`，DB 預設 `C:\D\MCP_1.0`）
2. **確認檔案存在**，不存在則 clone：
   - JIRA：確認 `<dir>\jira_mcp\__main__.py`，clone `garfield654123/JIRA_MCP`，安裝 `python-dotenv httpx mcp`
   - DB：確認 `<dir>\server.py`，clone `garfield654123/MCP_1.0`，安裝 `pyodbc mcp`
3. **詢問設定參數**（名稱、連線資訊、Token 等）
4. **寫入 `.claude.json`** 對應專案的 `mcpServers`
5. **驗證**：執行 `claude mcp list` 確認 CLI 可見

### JIRA MCP 設定結構
```json
"jira-mcp": {
  "type": "stdio", "command": "py", "args": ["-m", "jira_mcp"],
  "env": { "JIRA_BASE_URL": "...", "JIRA_EMAIL": "...", "JIRA_API_TOKEN": "...",
           "DEFAULT_USER": "<email>", "TRANSPORT": "stdio", "PYTHONPATH": "<dir>" }
}
```

### DB MCP 設定結構
```json
"ehrms-database": {
  "type": "stdio", "command": "py", "args": ["<dir>\\server.py"],
  "env": { "MSSQL_SERVER": "...", "MSSQL_DATABASE": "...",
           "MSSQL_USERNAME": "...", "MSSQL_PASSWORD": "..." }
}
```

完成後提示：重新啟動 Claude Code，再執行 `/mcp-config` 確認連線。

---

## C. CLI 方式新增（`/mcp-config cli-add`）

`claude mcp add` 寫入**全域**設定（所有專案共用），不需手動編輯 JSON。

```bash
# DB MCP
claude mcp add ehrms-database py "C:\D\MCP\server.py" \
  --env MSSQL_SERVER=<DB_SERVER_IP> --env MSSQL_DATABASE=<DB_NAME> \
  --env MSSQL_USERNAME=<DB_USER> --env MSSQL_PASSWORD=****

# JIRA MCP
claude mcp add jira-mcp py "-m" "jira_mcp" \
  --env JIRA_BASE_URL=https://104corp.atlassian.net \
  --env JIRA_EMAIL=ziping.zhou@104.com.tw --env JIRA_API_TOKEN=<token> \
  --env PYTHONPATH=C:\D\JIRA_MCP
```

新增後立即驗證：
```bash
claude mcp list   # 確認出現在清單
```
再讀取 `.claude.json` 確認已寫入。

| 比較 | `claude mcp add` | 手動編輯 `.claude.json` |
|------|-----------------|----------------------|
| 範圍 | 全域 | 可指定專案 |
| 驗證 | `claude mcp list` 立即可見 | 需重啟確認 |
| 推薦 | 快速新增 | 需專案隔離時 |

---

## 常見問題

| 問題 | 解決方式 |
|------|---------|
| 重啟後仍無法使用 | 首次連線需在提示視窗點選「允許」 |
| 未連線但設定存在 | 確認路徑正確、套件已安裝；手動執行 `py <dir>\server.py` 測試 |
| `claude mcp list` 空白 | 確認 `.claude.json` 中 `mcpServers` 不為空，重啟後再試 |
| `claude mcp add` 後 list 沒出現 | 確認 `--env` 每個參數獨立一個，指令格式無錯字 |
| CLI 與 `.claude.json` 不一致 | 以 `.claude.json` 為準；重啟後再執行 `claude mcp list` |
| 如何移除 | 執行 `claude mcp remove <名稱>`，或手動刪除 `.claude.json` 對應項目 |
| JIRA Token 取得 | Atlassian 帳號 → 安全性 → API tokens → 建立 |
