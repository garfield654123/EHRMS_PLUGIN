---
name: test-report
description: 在 bug-fix Step 3 或手動呼叫時，整合 Jira MCP 資訊與 git log，自動建立 Notion 測試報告頁面。Use when you need to create a test report after completing a bug fix in EHRMS.
---

執行環境：Claude Code 與 Codex 共用；工具名稱與檔案讀取方式依 [平台工具對照](../../references/platform-runtime.md) 選擇。

# 測試報告產生器

整合 Jira MCP、git log、對話上下文，建立 Notion 測試報告頁面。

**目標資料庫：** `collection://c71519b9-3000-4e8b-8622-0d63bfb97eb0`（📄 測試報告）
**Jira Base URL、使用者設定：** 見 `~/.ehrms-tools/users.md`（由 `user-config` 管理）

## 觸發方式

- 由 `/bug-fix` Step 3 自動呼叫（主要路徑）
- 手動呼叫 `/test-report`（補建報告）

## 工作流程

### 步驟 1：蒐集 Jira 資訊（MCP）

```
mcp__plugin_ehrms-tools_EHRMS-jira-mcp__get_issue(issue_key="EHRMSONE-XXXXX")
```

從 Jira 取得：

| 欄位 | 用途 |
|------|------|
| Summary（標題） | Notion 頁面標題、報告名稱 |
| Description（問題描述） | 原本問題區塊 |
| 根因欄位 / Comment | 根因分析 |
| Resolution / 修復 Comment | 正確案例（解決方式） |

若欄位為空 → 標記「[待補充]」，不自行捏造。

### 步驟 2：蒐集 Git 資訊

```bash
# 此分支相對 master 的 commit 記錄
git log EHRMSONE-XXXXX --not origin/master --oneline

# 修改的檔案清單
git diff origin/master..EHRMSONE-XXXXX --name-only

# 當前分支名稱確認
git branch --show-current
```

從 commit message 輔助判斷修改摘要，補充至「正確案例」與「程式碼內容」欄位。

> 若由 `/bug-fix` Step 3 呼叫，git 資訊應已蒐集完畢，直接使用，不重複執行。

### 步驟 3：讀取使用者設定

讀取 `~/.ehrms-tools/users.md`（由 user-config 管理）：
1. 取得 `active:` 欄位的使用者名稱
2. 找到對應 Notion User ID
3. 取得系統設定中的 Jira Base URL
4. 若找不到 → 詢問使用者

### 步驟 4：整理測試步驟草稿

根據 Jira Description 與 commit message 提供測試步驟草稿：

```
1. [根據問題描述推測的前置條件]
2. [操作步驟簡述]
3. 確認 [預期結果]
（以下請使用者補充詳細步驟）
```

### 步驟 5：確認報告摘要

列出即將建立的報告內容，等待使用者確認：

```
【即將建立 Notion 測試報告】

標題：EHRMSONE-XXXXX: [Jira 標題]
日期：YYYY-MM-DD
Owner：[active 使用者]
Tags：BugFix
Git 分支：EHRMSONE-XXXXX
Jira 連結：[Jira Base URL]EHRMSONE-XXXXX

修改檔案：
  - EHRMS/xxx/yyy.asp
  - VB/EHRMS/zzz.cls

⚠️ 測試步驟為草稿，建議使用者在 Notion 建立後補全。

確認後將建立 Notion 頁面，是否繼續？[Y/N]
```

⚠️ 等待使用者確認後才執行步驟 6

### 步驟 6：建立 Notion 頁面（含完整內容）

> ⚠️ **不使用 template_id**：Notion 範本為非同步載入，若建立後立即寫入內容會因時序問題覆蓋空白頁面。改為在建立頁面時直接帶入完整 `content`，確保同步寫入。

使用 `notion-create-pages`，同時設定 properties 與 content：

**Properties：**
```
data_source_id: c71519b9-3000-4e8b-8622-0d63bfb97eb0
Page: "EHRMSONE-XXXXX: [Jira 標題]"
Tags: ["BugFix"]
date:Date:start: "YYYY-MM-DD"
date:Date:is_datetime: 0
Owner: [Notion User ID]
```

**Content（Notion Markdown 格式，對應測試文件範本結構）：**

```markdown
### 原本問題
[Jira Description；若為空則填「[待補充]」]

### 錯誤範例
[以人類可讀的方式說明「原本行為哪裡錯」，重點是現象與原因，而非程式碼。
格式範例：
  「XXX 時數會因 YYY 被重複計算 N 次，導致結果偏大／偏小／變負數。」
  若有具體數字案例，列出：原始值 → 錯誤計算結果。
  若無 debug 上下文 → 填「[待補充]」]

### 正確案例
[以人類可讀的方式說明「正確行為應該是什麼」，對應錯誤案例做比較說明。
格式範例：
  「XXX 時數只應計算一次，不受 YYY 影響，結果應為 Z 小時。」
  若有具體數字案例，列出：原始值 → 正確計算結果。
  若為空 → 填「[待補充]」]

### 測試內容
[測試案例說明]

### 測試步驟
[步驟 4 的草稿內容]

### 注意事項
[風險評估、影響範圍；若無則填「[待補充]」]

## 以下為詳細更改部分(供內部參考)

### 有更動過之檔案
[git diff --name-only 結果，每行一個檔案路徑]
Jira：[Jira Base URL]EHRMSONE-XXXXX
Git 分支：EHRMSONE-XXXXX

### 程式碼內容
[說明修改了哪個函數／哪段邏輯，以及修改的方向。格式：
  修改檔案：[檔案名稱] 的 [函數名稱]
  修改說明：[一句話說明改了什麼邏輯，例如「加入中間層 GROUP BY 以防止重複計算」]
  Commit：[git log --oneline 的摘要]
  若無上下文 → 僅填 commit 摘要]
```

### 步驟 7：回報結果

```
✅ 測試報告已建立

標題：EHRMSONE-XXXXX: [Jira 標題]
連結：[Notion 頁面 URL]

⚠️ 測試步驟為草稿，請至 Notion 頁面補充完整測試步驟。
```

## 注意事項

- 此資料庫為 wiki，**不可使用 `notion-update-data-source` 更新屬性值**
- 若 Jira 單號不存在或無法取得，標題格式改為：`[YYYY-MM-DD] 問題簡述`
- `錯誤範例` 欄位來源判斷：
  - 有 `/debug` 流程上下文 → 使用【問題程式碼】區塊的關鍵片段
  - 無上下文（直接從 `/bug-fix` 呼叫）→ 填「[待補充]」
