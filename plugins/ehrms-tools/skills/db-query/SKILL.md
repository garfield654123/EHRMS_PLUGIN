---
name: db-query
description: EHRMS MSSQL 資料庫查詢規範，透過 MCP 工具驗證表格結構並產生 SQL 語法。Use when writing SQL queries, validating table structures, or answering database-related questions about EHRMS.
---

執行環境：Claude Code 與 Codex 共用；工具名稱與檔案讀取方式依 [平台工具對照](../../references/platform-runtime.md) 選擇。

# EHRMS DB Query Rules

查詢 EHRMS 系統 DB 相關資料時必須套用的規則。

## 核心規則

1. **必須使用 MCP 驗證**：查詢 DB 問題時，使用 EHRMS DB MCP 查詢並驗證表格結構後才提供給使用者
2. **資料不共用假設**：EHRMS 架設在客戶端，MCP 連結的資料庫不一定有該筆資料。除非使用者明確指定，否則僅提供語法讓使用者自行執行
3. **SP 可直接查詢**：大部分 Stored Procedure 在資料庫有紀錄，可直接用 MCP 查詢
4. **MSSQL 相容性**：需相容 2008-2022 版本，禁止使用 TRY_CONVERT 等新語法（詳見 [../program-standards/mssql-compat.md](../program-standards/mssql-compat.md)）
5. **表格命名辨識**：
   - `hrms_*` / `portal_*` = 標準功能表格
   - `ZZ_*` = 客製功能表格（使用者主要詢問標準功能）
6. **禁止執行 INSERT / UPDATE / DELETE**：改為提供語法讓使用者檢視後自行執行

## 工作流程

### 步驟 1：初始化 Session（依主題蒐集相關表格）

使用 `db_mcp_init` 以主題關鍵字取得相關表格清單：

```
db_mcp_init(topic="查詢主題，例如：請假餘額、出勤統計")
```

回傳 `session_id`，後續步驟帶入使用，啟用快取加速。

### 步驟 2：確認表格結構

使用以下工具確認涉及表格的欄位：

```
search_tables(description="關鍵字")          # 依描述搜尋相關表格
get_table_columns(table_name="hrms_xxx",    # 查詢欄位詳情
                  session_id="session_id")
analyze_table_joins(table_name="hrms_xxx",  # 查詢 JOIN 關係
                    session_id="session_id")
```

### 步驟 3：產生 SQL 語法

若已有 session context，優先使用 `db_generate_query` 產生語法草稿：

```
db_generate_query(session_id="session_id",
                  prompt="查詢需求描述")
```

否則依 MCP 確認的表格結構手動撰寫，遵循 MSSQL 2008 相容規範。

### 步驟 4：驗證語法

使用 `db_validate_query` 驗證語法結構正確性：

```
db_validate_query(session_id="session_id",
                  sql_query="SELECT ...")
```

### 步驟 5：執行或提供語法

| 類型 | 處理方式 |
|------|---------|
| SELECT | 可用 `mssql_query` 在 MCP DB 執行驗證（非客戶端資料） |
| INSERT / UPDATE / DELETE | **僅提供語法，禁止執行**，由使用者在客戶端自行執行 |

### 步驟 6：輸出格式

```
【查詢語法】
- 用途：[說明]
- 涉及表格：[表格清單]
- 已驗證：✅ 語法結構正確（MCP 驗證）
- ⚠️ 請在客戶端資料庫執行，MCP 資料僅供結構參考

[SQL 語法]
```

僅依據 MCP 查詢到的資料給出回答，不隨意增補不存在的表格與架構。
