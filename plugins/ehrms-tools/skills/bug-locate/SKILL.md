---
name: bug-locate
description: EHRMS 程式碼定位分析，追蹤跨層呼叫關係（ASP → VB6 → SP）。Use when tracing functionality, finding code entry points, or mapping call chains across layers. Results feed into /debug for fix planning.
---

執行環境：Claude Code 與 Codex 共用；工具名稱與檔案讀取方式依 [平台工具對照](../../references/platform-runtime.md) 選擇。
程式碼追蹤需在 EHRMS_GIT repo 目錄內執行。

# EHRMS Code Locate

快速定位 EHRMS 專案中的程式碼位置，追蹤跨層呼叫關係（ASP → VB6 → SP）。

EHRMS 是一款部署在客戶端的產品，大部分客戶資料由客戶自行的資料庫提供。

## 核心原則

1. **快速定位**：系統化方法找到相關程式碼
2. **完整追蹤**：追蹤跨層呼叫關係
3. **不修改程式碼**：僅定位，不進行修改
4. **精簡輸出**：僅顯示關鍵程式碼位置

- 專案結構參考：See [../../references/project-structure.md](../../references/project-structure.md)
- 編碼處理規則：See [../../references/encoding-rules.md](../../references/encoding-rules.md)
- 搜尋技巧與命名規則：See [search-patterns.md](search-patterns.md)

## 工作流程

### 步驟 1：收集定位資訊

詢問使用者提供以下資訊（至少一項）：

1. **功能描述**：例如「請假申請」、「加班時數計算」
2. **畫面/頁面名稱**：例如「考勤資料維護」
3. **檔案名稱**：例如 `Attendance_DataOP_Export_Step1.asp`
4. **關鍵字**：例如「OT_HOURS」、「LEAVE_APPLY」
5. **模組名稱**：例如「Personnel」、「Payroll」
6. **錯誤訊息**：例如「此員工假勤重複，故無法存檔」

### 步驟 2：釐清問題與入口

1. 跟使用者確認問題發生位置，取得 ASP 檔案或 function 入口
2. 先查知識庫：`wiki_lookup`（原則、選單、程式所在資料夾、關鍵資料表）＋`recall`（程式入口與細節）；Wiki 尚未收錄的主題再參考 domain-knowledge：
   - 請假/加班相關：EHRMS_GIT 的 `.claude/skills/domain-knowledge/ask-leave.md`
   - 刷卡比對相關：EHRMS_GIT 的 `.claude/skills/domain-knowledge/card-match.md`
   - 彈性工時相關：EHRMS_GIT 的 `.claude/skills/domain-knowledge/flexible.md`
3. 如果有多個 function，需確認使用者要追蹤哪一個
4. 若客戶提供的錯誤訊息來自後端，預設檔案為 Big5 編碼進行搜尋

### 步驟 3：建立程式碼地圖

#### ASP 檔案

1. **識別主要功能區塊**（新增、修改、刪除、查詢）
2. **找出 VB6 類別呼叫**：搜尋 `CreateObject`、`Set obj = ...`
3. **找出 JavaScript 函式**：前端驗證、AJAX 呼叫、表單提交
4. **找出表單欄位與資料庫欄位對應**
5. **識別跨檔案關聯**：
   - 多步驟流程：`[功能]_Step1.asp` → `Step2.asp` → `Step3.asp`
   - Include 引用：`<!--#include file="..."-->`
   - AJAX 呼叫：`XMLHttpRequest`, `$.ajax`
   - 彈出視窗：`window.open`, `showModalDialog`
   - 框架頁面：`<frame>`, `<iframe>`

6. **發現跨檔案關聯時，詢問使用者追蹤方向**：
   ```
   發現此檔案有以下關聯：
   1. 前/下一步驟：[檔案名稱]
   2. Include 引用：[檔案名稱]
   3. AJAX 呼叫：[檔案名稱]
   
   請問要追蹤哪一個？
   ```

#### VB6 檔案（.cls / .bas）

1. 列出所有 Public Function/Sub（名稱、參數、回傳值）
2. 識別 SP 呼叫：搜尋 `ExecuteSQL`, `GetRecordset`, `ExecuteNonQuery`, `ExecuteScalar`
3. 找出參數傳遞與回傳值

#### SQL 檔案（.SQL / .VIW / .UDF）

1. 識別 SP/View/Function 名稱
2. 列出輸入參數與輸出欄位
3. 可使用 DB MCP 工具確認邏輯（參考 [../db-query/SKILL.md](../db-query/SKILL.md)）

### 步驟 4：追蹤呼叫鏈

建立完整呼叫關係：

1. **ASP 跨檔案追蹤**：Step1 → Step2 → Step3、Include、AJAX、彈出視窗
2. **前端 → 後端**：ASP 呼叫哪些 VB6 類別/函式
3. **後端 → 資料庫**：VB6 呼叫哪些 SP/View/Function
4. **資料庫 → 表格**：SP/View/Function 使用哪些表格

### 步驟 5：輸出定位報告

**結構化輸出 4 個區塊：**

#### 1. 【定位摘要】
```
功能：[功能描述]
入口檔案：[檔案路徑]
涉及層級：[前端 ASP / 後端 VB6 / 資料庫 SP / 跨層]
相關檔案數量：N 個
```

#### 2. 【程式碼地圖】
```
前端 ASP
  └─ /EHRMS/[模組]/[檔名].asp
     ├─ 功能：[描述] (L[行數])
     └─ VB6 呼叫：[類別].[函式] (L[行數])

後端 VB6
  └─ /VB/EHRMS/[檔名].cls
     └─ Public Function [函式名稱] (L[行數])
        └─ SP 呼叫：[SP 名稱] (L[行數])

資料庫層
  └─ SP: /script/StoredProcedure/[SP名稱].SQL
     └─ 使用表格：[表格1], [表格2]
```

#### 3. 【呼叫鏈追蹤】
```
[ASP 檔案] (L行數)
  → [VB6 類別].[函式]()
    → [SP 名稱]
      → [表格名稱]
```

#### 4. 【關鍵程式碼位置】
```
主要邏輯位置：
  - [檔案路徑] L[行數]-L[行數]：[功能描述]

相關檔案：
  - [檔案路徑]：[用途說明]

資料庫物件：
  - 表格：[名稱] - [用途]
  - SP：[名稱] - [用途]
```

### 步驟 6：後續建議

- 如果使用者沒有主動提及，不用提供修正建議
- 不主動修改程式碼，只敘述目前邏輯
- 定位完成後沉澱：若非特定客戶客製案例，將定位到的入口寫入 `remember`（Engineer 型：TOPIC=功能名稱、ENTRY_PATH=入口檔案::函式、CONTENT=呼叫鏈摘要＋關鍵邏輯要點、keywords=3~8個、source="bug-locate"；跨案例可泛化才寫）
- 如果使用者要求整理成完整規則文件，才建立文件存到 domain-knowledge 目錄（人工審閱用，非每次都寫）
- 定位完成後若需要修復 → 轉交 `/debug` skill 進行根因診斷與程式碼修改
- 若定位到的標準版邏輯無法解釋使用者描述的實際行為，且已知或懷疑是特定客戶 → 建議呼叫 custom-locate skill 查該客戶的客製版本

## 跨模組共用提醒

- `/EHRMS/function` 中的共用函式
- `/EHRMS/Library` 中的共用程式庫（.lbi）
- `/VB/EHRMS` 中的共用模組（.bas）
