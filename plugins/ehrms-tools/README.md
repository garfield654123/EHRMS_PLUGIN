# ehrms-tools Plugin 使用說明

EHRMS 開發工具包，整合 **JIRA MCP**、**DB MCP**、**ehrms-memory 團隊共用記憶** 及 **MCP 設定管理 Skill**。

---

## v3.0.0：移除 codegraph 程式圖譜

v2.x 曾提供 `ehrms-codegraph` 程式圖譜 MCP（find_entry / trace / verify_call_path），v3.0.0 起整個移除，plugin 剩三個 MCP：`ehrms-database`、`EHRMS-jira-mcp`、`ehrms-memory`。記憶表 `HRMS_MEMORY` 的 `INDEX_SHA` 欄位（原記錄 codegraph 索引版本）同步停用，程式不再讀寫；既有 DB 的該欄位可由 DBA DROP（見 `hrms_memory.sql` 頂部說明）。

---

## ehrms-memory 團隊共用記憶

`ehrms-memory` MCP 提供檢索（recall）與沉澱（remember），資料存於 MSSQL `HRMS_MEMORY` 表。設計要點：

1. **兩型記憶**：`System`＝系統使用層知識（客服視角：操作順序、前置條件、功能行為）；`Engineer`＝程式入口與邏輯要點（維運視角，entry_path 必填）
2. **寫入端把關**：`remember` 內建確定性去重——高相似同結論 → 不新增、舊筆信心 +1；高相似不同結論 → 要求明確帶 `supersedes`（訂正）或 `force`（新議題）
3. **訂正一級公民**：`remember(supersedes=舊ID)` 插入新版取代舊版，讀取端自動排除被取代者（append-only，永不 UPDATE/DELETE 內容）
4. **使用即強化**：`recall` 命中自動累計 `HIT_COUNT`/`LAST_HIT_AT`，常用記憶浮上來、零引用記憶成為去蕪存菁候選
5. **記錄者＝Jira 帳號**：`CREATED_BY` 取自 `JIRA_EMAIL`（如 ziping.zhou），與 Jira 溯源一致
6. **定期去蕪存菁**：`/memory-curate` skill 合併重複、訂正矛盾、淘汰過時（全程 supersede 表達，物理刪除只有 DBA 能做）
7. **沉澱時機由 skill 流程編排**（查案 skill 的固定步驟：開頭 recall、結論後 remember），不再使用 Stop hook 強迫寫入

建表 SQL：`servers/MEMORY/hrms_memory.sql`（含最小權限 GRANT/DENY，請 DBA 執行）。

---

## 目錄

1. [前置需求](#前置需求)
2. [安裝步驟](#安裝步驟)
3. [取得 Jira API Token](#取得-jira-api-token)
4. [設定 MCP 連線](#設定-mcp-連線)
5. [驗證連線](#驗證連線)
6. [工具使用說明](#工具使用說明)
   - [DB MCP 工具](#db-mcp-工具)
   - [JIRA MCP 工具](#jira-mcp-工具)
   - [/mcp-config Skill](#mcp-config-skill)
7. [目錄結構](#目錄結構)
8. [常見問題](#常見問題)

---

## 前置需求

在開始之前，請確認以下環境已備妥：

| 需求 | 版本要求 | 說明 |
|------|----------|------|
| Python | 3.10 以上 | 執行 MCP 伺服器所需 |
| Claude Code | 最新版 | Plugin 執行環境 |
| MSSQL ODBC Driver | 13 / 17 / 18 / Native Client | DB MCP 連線所需 |
| Jira API Token | — | JIRA MCP 認證所需（見下方說明） |

> **確認 Python 版本**：在終端機執行 `py --version`，確認輸出為 `3.10.x` 以上。

---

## 安裝步驟

### 步驟 1：安裝 Plugin

在 Claude Code 中，使用 Plugin Marketplace 安裝 ehrms-tools，或將此專案複製到本地後執行：

```bat
plugins\ehrms-tools\setup.bat
```

此腳本會自動安裝所有必要的 Python 套件。

> 若需手動安裝：
> ```bash
> pip install -r plugins/ehrms-tools/requirements.txt
> ```

安裝的套件清單：

| 套件 | 用途 |
|------|------|
| `mcp>=1.0.0` | MCP SDK（核心） |
| `pydantic>=2.0.0` | 資料驗證 |
| `pyodbc>=4.0.0` | MSSQL 資料庫連線 |
| `python-dotenv>=1.0.0` | 環境變數讀取 |
| `fastapi>=0.104.0` | HTTP 伺服器框架 |
| `uvicorn[standard]>=0.24.0` | ASGI 伺服器 |
| `sse-starlette>=1.8.0` | SSE 支援 |
| `sqlparse>=0.4.4` | SQL 語法解析 |
| `httpx>=0.27.0` | HTTP 非同步客戶端 |
| `starlette>=0.27.0` | ASGI 框架 |

### 步驟 2：同步 lab_UTF8（EHRMS_GIT）共用 skills

本 plugin 需搭配 EHRMS 原始碼 repo（EHRMS_GIT，團隊慣稱 lab_UTF8）的專案 skills（`.claude/skills/`）一起使用。
其中 `crisis-triage`、`mail-query`、`weekly-report` 三個 skills 以 **plugin 為準源**——
plugin 安裝或升版後，須將 plugin 內 `skills/` 的最新版同步覆蓋到 EHRMS_GIT 的 `.claude/skills/` 對應目錄。

- 交由 AI 安裝時，`/setup-guide` 的 Step 5 會自動比對並引導完成同步
- 手動同步後請在 EHRMS_GIT 以慣例訊息 commit：`docs(skills): 同步 <skill 名> 至 ehrms-tools vX.Y.Z 版`

> ⚠️ `test-report` 在兩邊同名但用途不同（EHRMS_GIT 版＝Notion 測試報告產生器，plugin 版＝測試步驟撰寫規範），
> 不在同步清單內，切勿互相覆蓋。

---

## 取得 Jira API Token

使用 JIRA MCP 需要個人 API Token，請依下列步驟取得：

1. 登入 [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. 點選「**Create API token**」
3. 輸入名稱（例如：`claude-code-mcp`）並點選「**Create**」
4. **複製產生的 Token**（離開頁面後無法再查看）

> Token 格式類似：`ATATT3xFfGF0a1b2c3d4...`

---

## 設定 MCP 連線

### 方法 A：透過 Skill 互動式設定（推薦）

在 Claude Code 中執行：

```
/mcp-config add
```

依提示選擇要新增的 MCP（JIRA / DB / 兩者），並輸入對應連線資訊，Skill 會自動完成設定。

---

### 方法 B：使用 `claude mcp add` 指令

開啟終端機執行以下指令（請替換 `<>` 中的實際值）：

```bash
# 設定 DB MCP
claude mcp add ehrms-database py "${CLAUDE_PLUGIN_ROOT}/servers/MCP_1.0/server.py" \
  --env MCP_MODE=stdio \
  --env PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/servers/MCP_1.0" \
  --env MSSQL_SERVER=<DB伺服器IP> \
  --env MSSQL_DATABASE=<資料庫名稱> \
  --env MSSQL_USERNAME=<帳號> \
  --env MSSQL_PASSWORD=<密碼>

# 設定 JIRA MCP
claude mcp add EHRMS-jira-mcp py "-m" "jira_mcp" \
  --env PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/servers/JIRA_MCP" \
  --env JIRA_BASE_URL=https://104corp.atlassian.net \
  --env JIRA_EMAIL=<你的Email> \
  --env JIRA_API_TOKEN=<你的API_Token>
```

---

### 方法 C：手動編輯 `.claude.json`

在你的專案根目錄的 `.claude.json`（或 `~/.claude/.claude.json` 全域設定）中加入：

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
        "MSSQL_PASSWORD": "你的密碼"
      }
    },
    "EHRMS-jira-mcp": {
      "command": "py",
      "args": ["-m", "jira_mcp"],
      "env": {
        "PYTHONPATH": "${CLAUDE_PLUGIN_ROOT}/servers/JIRA_MCP",
        "JIRA_BASE_URL": "https://104corp.atlassian.net",
        "JIRA_EMAIL": "你的Email",
        "JIRA_API_TOKEN": "你的API_Token"
      }
    }
  }
}
```

#### 完整環境變數說明

**DB MCP 環境變數**

| 變數 | 必填 | 說明 | 範例值 |
|------|------|------|--------|
| `MSSQL_SERVER` | ✅ | MSSQL 伺服器 IP 或主機名稱 | `<DB_SERVER_IP>` |
| `MSSQL_DATABASE` | ✅ | 資料庫名稱 | `<DB_NAME>` |
| `MSSQL_USERNAME` | ✅ | 資料庫帳號 | `<DB_USER>` |
| `MSSQL_PASSWORD` | ✅ | 資料庫密碼 | `your_password` |
| `MCP_MODE` | — | 伺服器模式 | `stdio`（預設） |
| `MCP_SESSION_TTL` | — | Session 存活時間（秒） | `3600`（預設） |

**JIRA MCP 環境變數**

| 變數 | 必填 | 說明 | 範例值 |
|------|------|------|--------|
| `JIRA_BASE_URL` | ✅ | Jira Cloud 網址 | `https://104corp.atlassian.net` |
| `JIRA_EMAIL` | ✅ | 你的 Atlassian 帳號 Email | `your.name@104.com.tw` |
| `JIRA_API_TOKEN` | ✅ | Jira API Token | `ATATT3xFfGF0...` |

---

## 驗證連線

完成設定後，**重新啟動 Claude Code**，接著執行：

```
/mcp-config
```

正常結果如下（兩個 MCP 均顯示連線中）：

```
【目前專案 MCP 設定】

1. ehrms-database
   CLI：✅ 可見  |  連線：✅ 連線中

2. EHRMS-jira-mcp
   CLI：✅ 可見  |  連線：✅ 連線中
```

> 首次連線時，Claude Code 視窗可能會出現「允許此 MCP 執行？」提示，請點選**允許**。

---

## 工具使用說明

工具由 Claude Code 自動呼叫，你只需要用自然語言提問，Claude 會判斷並呼叫適當的工具。

---

### DB MCP 工具

透過 `ehrms-database` 提供以下工具：

| 工具 | 說明 | 使用情境 |
|------|------|----------|
| `echo` | 測試 MCP 連線是否正常 | 確認服務啟動 |
| `mssql_test_connection` | 測試 MSSQL 資料庫連線 | 確認帳密與網路 |
| `mssql_query` | 執行 SQL 查詢 | 直接下 SELECT/查詢語句 |
| `search_tables` | 依關鍵字搜尋相關資料表 | 不知道表名時先搜尋 |
| `get_table_columns` | 查詢資料表欄位定義 | 了解表結構 |
| `analyze_table_joins` | 分析資料表之間的 JOIN 關係 | 跨表查詢時參考 |
| `db_mcp_init` | 初始化查詢 Session | 複雜查詢工作流程第一步 |
| `db_generate_query` | 依需求描述自動生成 SQL | 不確定如何寫 SQL 時 |
| `db_validate_query` | 驗證 SQL 語法與安全性 | 執行前先驗證 |

#### 複雜查詢推薦工作流程

遇到需要跨表查詢或不熟悉表結構時，建議依以下順序：

```
1. db_mcp_init（輸入主題關鍵字，建立查詢 Session）
        ↓
2. db_generate_query（描述你的查詢需求，自動生成 SQL）
        ↓
3. db_validate_query（驗證語法與安全性）
        ↓
4. mssql_query（執行查詢，取得結果）
```

#### 使用範例

- 「幫我查員工 12345 的基本資料」
- 「搜尋跟薪資相關的資料表」
- 「查詢 EMP_BASIC 表有哪些欄位」
- 「幫我分析 EMP_BASIC 和 EMP_SALARY 的 JOIN 關係」
- 「初始化一個查詢 session，主題是請假紀錄」

---

### JIRA MCP 工具

透過 `EHRMS-jira-mcp` 提供以下工具：

工具設計原則：**預設回最小可用視圖，用參數按需加深**，避免上下文爆量。

| 工具 | 說明 | 使用情境 |
|------|------|----------|
| `get_issue` | 單張 Issue 完整內容：全文描述＋常用自訂欄位（客戶/嚴重度/來源/類別）＋最新 N 筆評論（預設 10，新→舊） | 深入看一張單 |
| `get_issue_summary` | 取得 Issue 基本摘要（輕量） | 快速查看標題與狀態 |
| `search_issues` | JQL 搜尋；預設精簡清單（key/summary/status/assignee/updated），`detail="full"` 回完整欄位 | 掃清單找目標 |
| `get_comments` | 最新 N 筆評論（預設 20，新→舊，`limit` 可調） | 追蹤討論進度 |
| `get_attachment` | 下載附件：圖片可直接檢視、文字檔轉純文字（UTF-8/CP950）、其他存本地 | 取得附件內容 |
| `get_issue_changelog` | 變更歷史（新→舊，預設 50 筆，`fields` 可只看特定欄位流轉） | 追「誰何時改了什麼」 |
| `get_user_info` | 取得 Jira 用戶資訊 | 查詢用戶帳號資料 |
| `list_custom_fields` | 以關鍵字搜尋自訂欄位（帶 `query` 過濾） | 找 customfield id |
| `add_comment` | 對 Issue 新增評論 | 回覆或更新進度 |

#### 使用範例

- 「幫我查 EHRMSONE-29158 的內容」
- 「搜尋我目前指派的所有 In Progress Issue」
- 「查 EHRMSONE-29158 的評論」
- 「查詢 assignee 是 ziping.zhou@104.com.tw 的未完成 Issue」
- 「幫我在 EHRMSONE-29158 加一則評論：已完成初步分析」

---

### memory MCP 工具（團隊共用記憶＋Jira 結案紀錄）

透過 `ehrms-memory` 提供（需 MSSQL_* 環境變數；`JIRA_EMAIL` 決定記錄者身分）。
兩組工具互補：`recall`/`remember` 管**跨單可泛化知識**（HRMS_MEMORY），
`jira_lookup`/`jira_log` 管**單一案件結案紀錄**（HRMS_JIRA，一單一筆有效紀錄）。

| 工具 | 說明 |
|------|------|
| `recall` | 檢索記憶，System/Engineer 分組回傳；命中自動累計引用次數。查案流程第一步呼叫 |
| `remember` | 寫入記憶（唯一寫入口，內建去重）；`supersedes=舊ID` 完成訂正。結論確認後呼叫 |
| `jira_lookup` | 查結案紀錄：給單號 → 精確查該單；給問題敘述 → 相似案件檢索（✅已審核案例優先，⏳未審核僅供參考）。查案開頭與 recall 並行 |
| `jira_log` | 寫入結案紀錄（單號/單型/根因/解法/修改程式）；同單重複會擋下，修正結論帶 `supersedes=舊ID`。Crisis=維運單、Story=改程式與 bug fix（changed_files 必填）。新紀錄一律 ⏳未審核 |

案例人工審核（類似記憶的 REVIEW 機制，但針對案件）：新寫入的結案紀錄預設 `pending`（⏳未審核）；
根因經人工確認後由 DBA/高權限工具在 DB 端標記 `verified`（✅，相似檢索優先引用）或
`rejected`（結論錯誤，檢索排除、被取代的舊版自動復活）。REVIEW_* 欄位不授權 MCP 帳號，
審核 UPDATE 範例 SQL 見 `servers/MEMORY/hrms_jira.sql` ④ 節。

使用範例：

- 查案開頭：「recall：災防假加班時數計算錯誤」＋「jira_lookup：災防假加班時數計算錯誤」並行
- 結案沉澱：`jira_log(jira_key="EHRMSONE-32543", kind="Crisis", title="...", root_cause="...", resolution="...")`
- 結論沉澱：`remember(kind="Engineer", topic="災防假加班時數計算", content="...", entry_path="...", ref_key="EHRMSONE-32543")`
- 修正結論：`jira_log(..., supersedes="21")` / `remember(..., supersedes="21")` → 新版取代舊版
- 定期維護：執行 `/memory-curate` 去蕪存菁

建表 SQL：`servers/MEMORY/hrms_jira.sql`（含最小權限 GRANT/DENY，請 DBA 執行）。

---

### /mcp-config Skill

用於管理 Claude Code 的 MCP 伺服器設定。

| 指令 | 說明 |
|------|------|
| `/mcp-config` | 查看目前 MCP 設定清單與連線狀態 |
| `/mcp-config add` | 互動式新增 MCP（JIRA / DB / 兩者） |
| `/mcp-config add jira` | 直接新增 JIRA MCP |
| `/mcp-config add db` | 直接新增 DB MCP |
| `/mcp-config cli-list` | 執行 `claude mcp list` 顯示 CLI 清單 |
| `/mcp-config cli-add` | 用 `claude mcp add` 新增 MCP |

---

## 目錄結構

```
plugins/ehrms-tools/
├── .claude-plugin/
│   └── plugin.json          # Plugin 基本資訊
├── .mcp.json                # MCP 伺服器設定（3 個 MCP）
├── servers/
│   ├── JIRA_MCP/            # JIRA MCP 伺服器原始碼
│   │   └── jira_mcp/
│   │       ├── __main__.py  # 入口點（py -m jira_mcp）
│   │       ├── server.py    # MCP 工具定義與路由
│   │       ├── client.py    # Jira REST API 客戶端
│   │       └── config.py    # 設定讀取（環境變數）
│   ├── MCP_1.0/             # DB MCP 伺服器原始碼
│   │   ├── server.py        # MCP 工具定義與路由
│   │   ├── config.py        # 設定讀取（環境變數）
│   │   ├── tools/           # 各工具實作模組
│   │   └── utils/           # 共用工具（DB 連線池、格式化、Session 管理）
│   └── MEMORY/              # 團隊共用記憶＋Jira 結案紀錄 MCP
│       ├── server.py        # recall / remember / jira_lookup / jira_log
│       ├── memory_core.py   # 記憶檢索評分、去重管線、supersede 訂正
│       ├── memory_db.py     # HRMS_MEMORY 資料層（append-only）
│       ├── jira_core.py     # 結案紀錄去重、同單 supersede、相似案件檢索
│       ├── jira_db.py       # HRMS_JIRA 資料層（append-only）
│       ├── hrms_memory.sql # HRMS_MEMORY 建表＋View＋最小權限（DBA 執行）
│       └── hrms_jira.sql   # HRMS_JIRA 建表＋View＋最小權限（DBA 執行）
├── skills/
│   ├── crisis-triage/       # 維運單（Crisis 單）查單與除錯流程
│   ├── mail-query/          # AutoEngine 通知信程式碼定位
│   ├── test-report/         # Bug Fix 測試步驟撰寫規範
│   ├── weekly-report/       # 週會報告產生器
│   ├── mcp-config/          # /mcp-config：MCP 設定管理
│   ├── setup-guide/         # /setup-guide：安裝設定指引
│   └── memory-curate/       # /memory-curate：記憶去蕪存菁
├── requirements.txt         # Python 套件依賴清單
└── setup.bat                # Windows 安裝腳本
```

---

## 常見問題

| 問題 | 解決方式 |
|------|---------|
| 啟動後 MCP 連線失敗 | 確認 Python 套件已安裝，手動執行 `py plugins/ehrms-tools/servers/MCP_1.0/server.py` 測試是否有錯誤訊息 |
| 出現「允許此 MCP 執行？」提示 | 點選「允許」，這是首次連線的安全確認 |
| `claude mcp list` 顯示空白 | 確認 `.claude.json` 中 `mcpServers` 不為空，重新啟動 Claude Code 後再試 |
| JIRA MCP 啟動時報錯 | 確認 `JIRA_BASE_URL`、`JIRA_EMAIL`、`JIRA_API_TOKEN` 均已正確設定 |
| DB MCP 連線逾時 | 確認 MSSQL Server IP 可連通（`ping <DB_SERVER_IP>`），並確認帳號密碼正確 |
| ODBC Driver 找不到 | 安裝 [Microsoft ODBC Driver for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)（建議版本 17 或 18） |
| `py` 指令找不到 | 確認 Python 已加入 PATH，或改用 `python` 指令後回報 |
| `/mcp-config` 指令無效 | 確認 Plugin 已正確安裝並啟用，重新載入 Claude Code |
