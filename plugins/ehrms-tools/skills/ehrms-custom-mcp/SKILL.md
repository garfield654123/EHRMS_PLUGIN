---
name: ehrms-custom-mcp
description: 透過 ehrms-custom-mcp 直接查詢客戶端 EHRMS 資料庫（客戶開啟 EHRMS_Connect.exe、提供 6 碼配對碼後，AI 產生 SELECT、工程師逐筆核准執行）。Use when the user gives a 6-digit pairing code (配對碼), asks to 連客戶資料庫／直接查客戶 DB／用 custom mcp 查, or when a 維運單 needs customer data and the user has the customer connected. 不處理資料異動（只能 SELECT）。
---

執行環境：目前只有 Claude Code 以使用者層級註冊 `ehrms-custom-mcp`（工具 `connect`、`query`）；工具不存在時告知使用者尚未註冊，改回「提供 SQL 請客戶端執行」。
開發規格：`servers/REMOTE_DB/SPEC.md`。

# 客戶資料庫查詢（ehrms-custom-mcp）

用來取代「給 SQL → 客戶端執行 → 複製結果貼回」。每筆 `query` 都會在 Claude Code 跳出核准提示，**由工程師審核 SELECT 後才執行**。

## 前置條件

| 項目 | 由誰 | 說明 |
|---|---|---|
| relay 與 zrok 分享在執行 | 工程師 | `powershell -ExecutionPolicy Bypass -File servers\REMOTE_DB\start-relay.ps1` |
| 客戶端開啟 EHRMS_Connect.exe 並登入 | 工程師經 Splashtop／AnyDesk 操作 | 帳號預設 ehrms；資料庫留空會自動偵測（通常為客戶統編） |
| 6 碼配對碼 | 使用者提供 | exe 畫面上的數字，有「複製」按鈕 |

AI 不得自行猜測或嘗試配對碼；沒有配對碼就向使用者要。

## 標準流程

```
取得配對碼 → connect（帶預期統編）→ 列出客戶資訊請使用者確認
→ 查表結構 → 撰寫 SELECT → query（逐筆由使用者核准）→ 依結果分析
```

### 1. 連線並確認客戶

- `connect(code, expected_sno)`：已知客戶統編（Jira 單、使用者告知）就帶 `expected_sno`，會自動比對資料庫名稱與 HRMS_COMPANY 公司統編
- 把回傳的資料庫、表單資料庫、SQL Server、公司清單列給使用者，**確認是正確客戶後才可 query**
- 出現 ⚠ 統編不符或「不是 8 碼統編格式」時，停下來問使用者，不可直接查詢

### 2. 撰寫 SELECT

- 欄位與關聯先用 DB MCP `get_table_columns`、`search_tables` 或 `recall`、`wiki_lookup` 確認（這些查的是結構與知識庫，不是客戶資料）
- 客戶端多為 **MSSQL 2008**：不可用 `FOR JSON`、`STRING_AGG`、`TRY_CONVERT`、`IIF`、`FORMAT`、`OFFSET/FETCH`
- 只能**單一 SELECT**（或 `WITH ... SELECT`）；不可有分號、`INTO`、`EXEC`、`USE`、`xp_`／`sp_` 等，會被客戶端擋下
- 一律加篩選條件（員工、日期區間、單號），只選需要的欄位；最多回傳 500 筆，被截斷時加條件重查
- 避免撈個資欄位（身分證、生日、住址、帳號、薪資明細）；確有必要時在 `reason` 說明
- 同一問題需要多個查詢時，能合併就合併，減少使用者核准次數

### 3. 執行 query

- `query(sql, reason, database)`
  - `reason`：具體寫要確認什麼，有 Jira 單號就帶上，例如「EHRMSONE-32543：確認員工 A001 在 10/1 的打卡原始資料」
  - `database`：`main`（預設，主資料庫＝統編）或 `wf`（表單資料庫＝統編_WF，簽核表單相關）。connect 顯示「表單資料庫：無」時不可用 `wf`
- 使用者拒絕核准時，不重送同一筆；詢問要如何調整

### 4. 依結果分析

- 只根據查到的資料下結論；資料不足就再查或說明缺什麼，不補推測值
- 回覆中只引用必要的欄位與筆數，不整批轉貼個資
- 寫回知識庫（`jira_log`、`remember` 等）只寫已確認的原因與處理方式，**不寫客戶的實際資料內容**

## 常見錯誤

| 訊息 | 原因與處理 |
|---|---|
| 尚未連線，請先…呼叫 connect | 先向使用者取得配對碼 |
| 找不到配對碼 … 的客戶端 | exe 已關閉、被中斷，或 1 小時沒有查詢自動中斷；請使用者在客戶端重新登入並提供新配對碼 |
| 無法連到 relay | 工程師電腦的 relay 沒在執行；請使用者執行 start-relay.ps1 |
| 配對碼對應的資料庫已改變 | 客戶端換了資料庫重新登入；重新 connect 並再次確認客戶 |
| 等待客戶端回應逾時（60 秒） | 查詢太重或客戶端斷線；縮小範圍後重試 |
| 只允許 SELECT／語法含不允許的關鍵字 | 改寫成單一 SELECT；欄位名剛好是禁用字（如 `use`）時用中括號或別名仍可能被擋，改用其他寫法 |

## 連線行為（回答使用者時參考）

- 客戶端 1 小時內沒有任何查詢會自動中斷；每筆查詢重新計時
- 網路短暫中斷時客戶端會以同一配對碼自動重連，不需重新 connect
- 目前為測試階段：經 zrok 對外、沒有額外驗證，查詢紀錄尚未寫入資料庫（規劃中）
