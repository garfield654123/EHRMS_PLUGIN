---
name: setup-guide
description: ehrms-tools Plugin 配置與測試指引。引導使用者完成 Python 套件安裝、環境變數設定、MCP 連線配置，並執行連線測試驗證 DB MCP 與 JIRA MCP 是否正常運作。Use when setting up the plugin for the first time, verifying connections, or troubleshooting MCP issues.
---

# ehrms-tools 配置與測試指引

## JIRA Issue 編號規則

**當使用者只提供數字（例如 `27046`）時，一律自動補上 `EHRMSONE-` 前綴，視為 `EHRMSONE-27046`。**
僅在使用者明確提供完整 Key（例如 `NPR-123`、`CSHR-456`）時，才使用其指定的前綴。

此規則適用於本 skill 所有涉及 Jira Issue Key 的步驟。

## 指令對照

| 指令 | 說明 |
|------|------|
| `/setup-guide` | 執行完整配置 + 測試流程 |
| `/setup-guide config` | 只執行配置引導（不測試） |
| `/setup-guide test` | 只執行連線測試（DB + JIRA） |
| `/setup-guide test db` | 只測試 DB MCP |
| `/setup-guide test jira` | 只測試 JIRA MCP |

---

## A. 完整流程（`/setup-guide`）

依序執行 **配置引導** → **連線測試**，每個步驟完成後才進行下一步。

```
Step 1：環境確認
Step 2：套件安裝
Step 3：環境變數設定
Step 4：MCP 連線配置
Step 5：同步 lab_UTF8（EHRMS_GIT）共用 skills
Step 6：連線測試
Step 7：輸出總結報告
```

---

## B. 配置引導（`/setup-guide config`）

### Step 1：確認 Python 環境

使用 Bash 工具執行：
```bash
py --version
```

- 版本 >= 3.10 → 繼續
- 版本不符或找不到指令 → 提示：「請安裝 Python 3.10 以上版本，並確認已加入 PATH」，停止流程

### Step 2：安裝 Python 套件

檢查 setup.bat 是否存在（讀取 `${CLAUDE_PLUGIN_ROOT}/setup.bat`），存在則提示使用者執行：

```
請在終端機執行以下指令完成套件安裝：
! plugins\ehrms-tools\setup.bat
```

若使用者確認已安裝（或已跳過），繼續下一步。

> 若 setup.bat 不存在，改提示執行：
> ```
> ! pip install -r plugins/ehrms-tools/requirements.txt
> ```

### Step 3：確認環境變數需求

詢問使用者要配置哪些 MCP：
```
請問要配置哪個 MCP？
  1. DB MCP（MSSQL 資料庫查詢）
  2. JIRA MCP（Jira Cloud 查詢）
  3. 兩者都配置
```

依選擇，列出對應的必填環境變數，並逐一詢問使用者提供值：

**DB MCP 所需資訊：**
- `MSSQL_SERVER` — MSSQL 伺服器 IP 或主機名稱
- `MSSQL_DATABASE` — 資料庫名稱
- `MSSQL_USERNAME` — 資料庫帳號
- `MSSQL_PASSWORD` — 資料庫密碼（輸入時提醒不會顯示在歷史記錄中）

**JIRA MCP 所需資訊（逐一詢問使用者）：**

1. 詢問 Email：
   ```
   請輸入你的 Atlassian 帳號 Email（例如 your.name@104.com.tw）：
   ```
   將輸入值存為 `JIRA_EMAIL`。

2. 詢問 API Token：
   ```
   請輸入你的 Jira API Token：
   ```
   將輸入值存為 `JIRA_API_TOKEN`。

   > **如何取得 Jira API Token**（若使用者表示不知道或尚未建立）：
   > 1. 開啟 https://id.atlassian.com/manage-profile/security/api-tokens
   > 2. 點選「Create API token」
   > 3. 輸入名稱（例如 `claude-code-mcp`）並點選建立
   > 4. **立即複製**產生的 Token（離開頁面後無法再查看）
   > 5. 回到這裡貼上 Token 繼續設定

   Token 輸入後不會顯示在對話歷史中，請放心提供。

`JIRA_BASE_URL` 固定為 `https://104corp.atlassian.net`，不需詢問。

### Step 4：寫入 MCP 設定

取得使用者提供的值後，呼叫 `/mcp-config add [jira|db]` 的對應邏輯完成寫入。

若使用者選擇「兩者都配置」，依序完成 DB → JIRA。

完成後提示：
```
✅ 設定已寫入，請重新啟動 Claude Code，再執行 /setup-guide test 驗證連線。
```

### Step 5：同步 lab_UTF8（EHRMS_GIT）共用 skills

本 plugin 需搭配 EHRMS 原始碼 repo（EHRMS_GIT，團隊慣稱 lab_UTF8）的專案 skills 使用。
下列 skills 以 plugin 為準源（source of truth），plugin 安裝或升版後需同步至該 repo 的 `.claude/skills/`：

- `crisis-triage`
- `mail-query`
- `weekly-report`

> ⚠️ `test-report` 兩邊同名但用途不同（repo 版＝Notion 測試報告產生器，plugin 版＝測試步驟撰寫規範），
> **不在同步清單**，絕不可互相覆蓋。

同步流程：

1. 確認 EHRMS_GIT repo 的本機路徑：先檢查預設路徑 `C:\D\EHRMS_GIT`，不存在則詢問使用者；
   使用者表示本機沒有此 repo → 跳過本步驟
2. 逐一 diff 比對 plugin 的 `skills/<name>/SKILL.md` 與 `<repo>/.claude/skills/<name>/SKILL.md`
3. 全部相同 → 回報「✅ 共用 skills 已是最新」；有差異 → 顯示 diff 摘要，經使用者確認後以 plugin 版覆蓋
4. 覆蓋後在 EHRMS_GIT **只 stage 被同步的 skill 檔案**（該 repo 常有其他未提交修改）並 commit，
   訊息沿用慣例：`docs(skills): 同步 <skill 名> 至 ehrms-tools vX.Y.Z 版`

> 注意：EHRMS_GIT 的 `.gitignore` 含 `.claude/`，skill 檔案雖已被追蹤，
> stage 時仍需 `git add -f .claude/skills/<name>/SKILL.md` 才不會被擋下。

---

## C. 連線測試（`/setup-guide test`）

測試 DB MCP 與 JIRA MCP 是否正常運作，**依序執行**以下檢查，每項記錄通過 ✅ 或失敗 ❌。

### DB MCP 測試（`ehrms-database`）

**Test DB-1：服務回應測試**
呼叫 `echo` 工具，輸入任意字串（例如 `"ping"`）：
- 有回應 → ✅ DB MCP 服務正常
- 無回應或錯誤 → ❌ DB MCP 無法連線（記錄錯誤訊息）

**Test DB-2：資料庫連線測試**
呼叫 `mssql_test_connection` 工具：
- 連線成功 → ✅ 資料庫連線正常，記錄回傳的伺服器資訊
- 連線失敗 → ❌ 記錄錯誤原因（IP 不通 / 帳密錯誤 / 驅動程式缺少）

**Test DB-3：基本查詢測試**
呼叫 `mssql_query` 工具，執行：
```sql
SELECT TOP 1 TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
```
- 有回傳資料 → ✅ 查詢功能正常
- 查詢失敗 → ❌ 記錄錯誤訊息

---

### JIRA MCP 測試（`EHRMS-jira-mcp`）

**Test JIRA-1：認證測試**
呼叫 `get_user_info` 工具，username 填入配置階段使用者提供的 `JIRA_EMAIL`；若跳過配置直接執行測試，則先詢問：
```
請輸入你的 Jira Email 以驗證認證是否正常：
```
- 有回傳使用者資料 → ✅ JIRA MCP 認證正常，顯示使用者名稱與 accountId
- 認證失敗 → ❌ 記錄錯誤原因（Token 無效 / Email 錯誤 / URL 錯誤）

**Test JIRA-2：Issue 查詢測試（固定單號）**
呼叫 `get_issue_summary` 工具，issueKey 填入 `EHRMSONE-27046`：
- 有回傳摘要 → ✅ Issue 查詢功能正常，顯示標題與狀態
- 查詢失敗 → ❌ 記錄錯誤訊息（權限不足 / 連線問題）

**Test JIRA-3：使用者自訂單號測試**
詢問使用者提供一個 Jira 單號進行驗證：
```
請提供一個你有權限查看的 Jira 單號（例如輸入 29158 或 EHRMSONE-29158）：
```
- 若使用者只輸入數字，自動補上 `EHRMSONE-` 前綴
- 呼叫 `get_issue_summary` 查詢使用者提供的 Issue Key
- 有回傳摘要 → ✅ 顯示該 Issue 的標題、狀態、指派人
- 查詢失敗 → ❌ 記錄錯誤（Issue 不存在 / 無讀取權限）

---

## D. 輸出測試報告

所有測試完成後，輸出格式如下：

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ehrms-tools 連線測試報告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 DB MCP（ehrms-database）
  DB-1 服務回應     ✅ 正常
  DB-2 資料庫連線   ✅ 正常（<DB_SERVER_IP> / <DB_NAME>）
  DB-3 基本查詢     ✅ 正常

 JIRA MCP（EHRMS-jira-mcp）
  JIRA-1 認證       ✅ 正常（ziping.zhou）
  JIRA-2 固定單號   ✅ 正常（EHRMSONE-27046）
  JIRA-3 自訂單號   ✅ 正常（EHRMSONE-XXXXX）

 整體狀態：✅ 所有測試通過，可以開始使用！
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

若有失敗項目，在報告末尾加入對應的排除建議：

| 失敗項目 | 排除建議 |
|----------|----------|
| DB-1 服務回應失敗 | 確認 Python 套件已安裝；手動執行 `py <PLUGIN_ROOT>/servers/MCP_1.0/server.py` 查看錯誤訊息 |
| DB-2 資料庫連線失敗 | 確認 MSSQL Server IP 可 ping 通；確認帳號密碼正確；確認已安裝 MSSQL ODBC Driver 17 或 18 |
| DB-3 查詢失敗 | 確認帳號有讀取 `INFORMATION_SCHEMA` 的權限 |
| JIRA-1 認證失敗 | 確認 API Token 未過期；確認 Email 與 Token 配對正確；重新產生 Token 後更新設定 |
| JIRA-2 查詢失敗 | 確認帳號有 EHRMSONE 專案的讀取權限；若 Issue 不存在請回報 Plugin 維護者更新測試單號 |
| JIRA-3 查詢失敗 | 確認該單號存在且帳號有讀取權限；只輸入數字時系統會自動補上 EHRMSONE- 前綴 |

最後提示：
```
如需重新配置，執行 /setup-guide config
如需查看 MCP 設定清單，執行 /mcp-config
```
