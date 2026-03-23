# ehrms-tools Plugin

EHRMS 開發工具包，整合 JIRA MCP、DB MCP 查詢工具及 MCP 設定管理 Skill。

---

## 目錄結構

```
plugins/ehrms-tools/
├── .claude-plugin/
│   └── plugin.json          # Plugin 基本資訊
├── .mcp.json                # MCP 伺服器設定（供 Claude Code Plugin 使用）
├── servers/
│   ├── JIRA_MCP/            # JIRA MCP 伺服器原始碼
│   │   └── jira_mcp/
│   │       ├── __main__.py  # 入口點（py -m jira_mcp）
│   │       ├── server.py    # MCP 工具定義與路由
│   │       ├── client.py    # Jira REST API 客戶端
│   │       └── config.py    # 設定讀取（環境變數）
│   └── MCP_1.0/             # DB MCP 伺服器原始碼
│       ├── server.py        # MCP 工具定義與路由
│       ├── config.py        # 設定讀取（環境變數）
│       ├── tools/           # 各工具實作
│       └── utils/           # 共用工具（DB 連線、格式化）
├── skills/
│   └── mcp-config/
│       └── SKILL.md         # /mcp-config Skill 定義
├── requirements.txt         # Python 套件依賴
└── setup.bat                # Windows 安裝腳本
```

---

## 快速安裝

### 步驟 1：安裝 Python 套件

執行安裝腳本（需要 Python 3.10+）：

```bat
plugins\ehrms-tools\setup.bat
```

或手動執行：

```bash
pip install -r plugins/ehrms-tools/requirements.txt
```

安裝清單：

| 套件 | 用途 |
|------|------|
| `mcp>=1.0.0` | MCP SDK（必須） |
| `pydantic>=2.0.0` | 資料驗證 |
| `pyodbc>=4.0.0` | MSSQL 連線（DB MCP） |
| `python-dotenv>=1.0.0` | 環境變數讀取 |
| `fastapi>=0.104.0` | HTTP 模式框架（DB MCP） |
| `uvicorn[standard]>=0.24.0` | ASGI 伺服器 |
| `sse-starlette>=1.8.0` | SSE 支援 |
| `sqlparse>=0.4.4` | SQL 語法解析 |
| `httpx>=0.27.0` | HTTP 客戶端（JIRA MCP） |
| `starlette>=0.27.0` | ASGI 框架（JIRA MCP） |

---

### 步驟 2：設定環境變數

本 Plugin 透過 `.mcp.json` 的 `env` 欄位注入設定，**不需要建立 `.env` 檔案**。

環境變數需在 **使用者全域設定** 或 **專案 `.claude.json`** 的 `mcpServers.env` 欄位中填入。

#### DB MCP 必填環境變數

| 變數名稱 | 說明 | 範例 |
|----------|------|------|
| `MSSQL_SERVER` | MSSQL 伺服器位址 | `<DB_SERVER_IP>` |
| `MSSQL_DATABASE` | 資料庫名稱 | `<DB_NAME>` |
| `MSSQL_USERNAME` | 使用者名稱 | `<DB_USER>` |
| `MSSQL_PASSWORD` | 密碼 | `your_password` |

#### JIRA MCP 必填環境變數

| 變數名稱 | 說明 | 範例 |
|----------|------|------|
| `JIRA_BASE_URL` | Jira Cloud 網址 | `https://104corp.atlassian.net` |
| `JIRA_EMAIL` | Jira 帳號 Email | `ziping.zhou@104.com.tw` |
| `JIRA_API_TOKEN` | Jira API Token | `ATATT3xFfGF0...` |

> **如何取得 Jira API Token**：Atlassian 帳號 → 安全性 → API tokens → 建立 token

---

### 步驟 3：設定 MCP 連線

#### 方法 A：透過 `claude mcp add`（全域設定，推薦）

```bash
# DB MCP
claude mcp add ehrms-database py "${CLAUDE_PLUGIN_ROOT}/servers/MCP_1.0/server.py" \
  --env MCP_MODE=stdio \
  --env PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/servers/MCP_1.0" \
  --env MSSQL_SERVER=<DB_SERVER_IP> \
  --env MSSQL_DATABASE=<DB_NAME> \
  --env MSSQL_USERNAME=<DB_USER> \
  --env MSSQL_PASSWORD=your_password

# JIRA MCP
claude mcp add EHRMS-jira-mcp py "-m" "jira_mcp" \
  --env PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/servers/JIRA_MCP" \
  --env JIRA_BASE_URL=https://104corp.atlassian.net \
  --env JIRA_EMAIL=ziping.zhou@104.com.tw \
  --env JIRA_API_TOKEN=your_api_token
```

#### 方法 B：透過 Skill（互動式引導）

在 Claude Code 中執行：

```
/mcp-config add
```

依提示選擇 JIRA / DB / 兩者，並輸入對應的連線資訊。

#### 方法 C：手動編輯 `.claude.json`

在專案的 `.claude.json` 中加入以下設定：

```json
{
  "mcpServers": {
    "ehrms-database": {
      "command": "py",
      "args": ["${CLAUDE_PLUGIN_ROOT}/servers/MCP_1.0/server.py"],
      "env": {
        "MCP_MODE": "stdio",
        "PYTHONPATH": "${CLAUDE_PLUGIN_ROOT}/servers/MCP_1.0",
        "MSSQL_SERVER": "<DB_SERVER_IP>",
        "MSSQL_DATABASE": "<DB_NAME>",
        "MSSQL_USERNAME": "<DB_USER>",
        "MSSQL_PASSWORD": "your_password"
      }
    },
    "EHRMS-jira-mcp": {
      "command": "py",
      "args": ["-m", "jira_mcp"],
      "env": {
        "PYTHONPATH": "${CLAUDE_PLUGIN_ROOT}/servers/JIRA_MCP",
        "JIRA_BASE_URL": "https://104corp.atlassian.net",
        "JIRA_EMAIL": "ziping.zhou@104.com.tw",
        "JIRA_API_TOKEN": "your_api_token"
      }
    }
  }
}
```

---

### 步驟 4：驗證連線

重新啟動 Claude Code 後，執行以下指令確認：

```
/mcp-config
```

應顯示兩個 MCP 的連線狀態：

```
【目前專案 MCP 設定】

1. ehrms-database
   CLI：✅ 可見  |  連線：✅ 中

2. EHRMS-jira-mcp
   CLI：✅ 可見  |  連線：✅ 中
```

---

## 可用的 MCP 工具

### DB MCP（`ehrms-database`）

| 工具名稱 | 說明 |
|----------|------|
| `mssql_query` | 執行 SQL 查詢 |
| `mssql_test_connection` | 測試資料庫連線 |
| `search_tables` | 依關鍵字搜尋相關資料表 |
| `get_table_columns` | 查詢資料表欄位定義 |
| `analyze_table_joins` | 分析資料表 JOIN 關係 |
| `db_mcp_init` | 初始化查詢 Session（工作流程第一步） |
| `db_generate_query` | 依需求描述生成 SQL 建議 |
| `db_validate_query` | 驗證 SQL 語法與安全性 |
| `echo` | 測試連線用 |

### JIRA MCP（`EHRMS-jira-mcp`）

| 工具名稱 | 說明 |
|----------|------|
| `get_issue_summary` | 取得 Issue 基本摘要（輕量） |
| `get_issue` | 取得 Issue 完整詳細資訊 |
| `search_issues` | 使用 JQL 搜尋 Issues |
| `get_my_issues` | 取得指定使用者的 Issues |
| `get_comments` | 取得 Issue 所有評論 |
| `get_attachment` | 下載 Issue 附件 |
| `get_issue_transitions` | 取得 Issue 可用狀態轉換 |
| `get_issue_changelog` | 取得 Issue 變更歷史 |
| `get_user_info` | 取得用戶資訊 |
| `list_custom_fields` | 列出所有自訂欄位 |
| `add_comment` | 對 Issue 新增評論 |

---

## 常見問題

| 問題 | 解決方式 |
|------|---------|
| 重啟後仍無法使用 MCP | 首次連線需在 Claude Code 提示視窗點選「允許」 |
| 連線失敗但設定存在 | 確認路徑正確、套件已安裝；手動執行 `py server.py` 測試 |
| `claude mcp list` 顯示空白 | 確認 `.claude.json` 中 `mcpServers` 不為空，重啟後再試 |
| JIRA MCP 啟動時報錯 | 確認 `JIRA_BASE_URL` 與 `JIRA_API_TOKEN` 環境變數已正確設定 |
| DB MCP 連線逾時 | 確認 MSSQL Server IP 可連線，並確認帳號密碼正確 |
