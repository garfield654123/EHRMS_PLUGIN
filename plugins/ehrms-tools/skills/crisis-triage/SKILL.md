---
name: crisis-triage
description: EHRMS Jira 維運單（Crisis 單）查單與除錯流程規範。重要規則：EHRMS 是架在客戶端的地端系統，公司內 DB 沒有客戶資料，查維運單時禁止用 DB MCP 找客戶資料/測試資料。Use when the user asks to 查/看 a Jira ticket (e.g. "幫我看 32543", "查 EHRMSONE-XXXXX"), when the issue type is Crisis - Level A/B/C, or when debugging a customer-reported (客戶回報/維運) problem.
---

# EHRMS 維運單（Crisis 單）查單與除錯流程

## 核心規則（最優先）

**EHRMS 是架在客戶端的地端系統，公司內可連的 DB「沒有」客戶資料。**

查維運單（issuetype 為 Crisis - Level A/B/C，或內容為客戶回報問題）時：

- ❌ **禁止**用 DB MCP 查客戶的公司、員工、假勤、行事曆、薪資等業務資料——必然查不到，浪費時間且可能誤導分析方向
- ❌ **禁止**用 DB MCP 嘗試「找測試資料」來重現客戶問題
- ✅ 需要資料佐證時 → 在診斷報告中列出「**請客戶端 / CS 協助執行的查詢 SQL**」，由有權限的人在客戶環境執行後回填結果
- ✅ DB MCP 僅可用於與客戶資料無關的用途：查表結構定義（HRMS_TABLES、get_table_columns）、hrms_sys_rule、schema 關聯分析

例外：使用者**明確要求**查 DB 時才使用（例如指名「用 DB MCP 查」或問題明確屬於自家環境資料）。

## 標準流程

```
取單 → recall＋jira_lookup → 判斷單型 → 讀附件截圖 → 追碼 → 診斷報告（前置 remember＋jira_log）→ 停止等確認
```

### 1. 取單

- 單號只給數字時補預設前綴 `EHRMSONE`
- `get_issue` 一次到位：回傳精簡欄位＋完整描述＋**最新 10 筆評論**（新→舊）；需要更早的討論再用 `get_comments` 調大 `limit`
- 取單後立刻並行呼叫（帶問題敘述）：ehrms-memory 的 `recall`（過往沉澱的系統知識與程式入口）＋ `jira_lookup`（過往相似案件的根因與解法）
- 從範本欄位雜訊中萃取：標題、描述、issuetype、狀態、客戶名稱、附件清單

### 2. 判斷單型

優先用 `get_issue` 回傳的**結構化欄位**判斷，不要從描述範本文字猜：

| 依據（get_issue 回傳欄位） | 判定 | DB MCP |
|---|---|---|
| `issue_type` = Crisis - Level A/B/C | 維運單 | 禁用（見核心規則） |
| `category`=維運、`source`=客戶、或 `customer` 有客戶名稱 | 維運單 | 禁用（見核心規則） |
| 上述欄位皆非客戶情境（內部開發/測試單、自家環境問題） | 一般單 | 可使用 |

- `severity`（嚴重度，如 Fatal）納入報告的風險評估
- `customer`（客戶名稱）寫進診斷報告開頭，並用於「請 CS/客戶端執行」的指引

### 3. 附件截圖

- 描述只有一兩句話時，重點通常在截圖 → `get_attachment`（預設 auto）：圖片直接以影像回傳可當場判讀；非圖片會存到本地，再用 Read 開啟
- 常見錯誤畫面格式「發生時間/狀況描述」＝ VB6 `ErrMsg(ERR)` 輸出，代表後端 VB6 runtime 錯誤

### 4. 追碼

- 入口優先採 `recall` 命中的 Engineer 記憶（entry_path）；沒有命中時以功能關鍵字 Grep 原始碼定位入口，再沿呼叫鏈追蹤
- Big5 的 .cls 先轉 UTF-8 暫存檔再搜尋中文訊息（直接 Grep 中文搜不到）
- 診斷證據來源＝**截圖 + 程式碼**，不是 DB 資料

### 5. 輸出與收尾

- 依專案 CLAUDE.md 輸出 5 區塊診斷報告（問題摘要/問題程式碼/修復方案/驗證步驟/風險評估）
- 需要客戶資料佐證的部分，附上 SQL 與說明請 CS/客戶端執行
- 報告輸出前沉澱兩件事（互補、各寫各的）：
  1. **結案紀錄** → 先 `jira_lookup(單號)` 查重：無紀錄 → `jira_log(kind="Crisis", jira_key=單號, title=一句話摘要, root_cause=根因, resolution=解決方式, changed_files=有改程式才填, keywords=3~8 個)`；已有紀錄且本次結論不同 → 帶 `supersedes=舊ID` 訂正；內容相同則不重寫
  2. **可泛化知識** → 跨單通用的系統行為或程式入口才用 `remember`（System/Engineer 兩型；訂正帶 `supersedes=舊ID`）
- 輸出後停止等使用者確認，不可直接改碼
