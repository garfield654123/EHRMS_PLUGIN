---
name: jira-auto-log
description: 定期批次掃描 status="CS CONFIRM" 的 EHRMSONE Jira 單，若 HRMS_JIRA 尚無紀錄則分析根因/解法並寫入；客戶操作失誤或原已有 FAQ/公告涵蓋的單則不寫 HRMS_JIRA，改記到本地略過清單。由本機排程（Windows Task Scheduler）headless 呼叫 `claude -p "/ehrms-tools:jira-auto-log"`，全程不可詢問使用者。
---

# Jira CS CONFIRM 單自動結案紀錄批次

## 適用範圍與限制（先讀，優先於其餘步驟）

- **headless 批次流程，全程不可詢問使用者**（無人在場執行）。遇到判斷不確定的單，寧可保守歸類為「無法判斷」寫進略過清單，也不要用不確定的結論寫進 `HRMS_JIRA`。
- **唯讀分析，不修改任何程式碼、不建立分支、不提交任何變更**——本 skill 只做分析與紀錄寫入。
- 沿用 `crisis-triage` 的 DB 使用限制：EHRMS 是架在客戶端的地端系統，公司內可連的 DB **沒有客戶資料**。禁止用 DB MCP 查客戶的公司、員工、假勤、薪資等業務資料，也禁止用 DB MCP 找測試資料重現問題。DB MCP 僅可用於查表結構等與客戶資料無關的用途。
- 記錄者身分：本流程執行時的 `MEMORY_USER` 環境變數由排程腳本設為排程專用值（如 `jira-auto-scheduler`），使 `HRMS_JIRA.CREATED_BY` 能一眼區分「排程寫入」與人工手動寫入。**本 skill 本身不處理這個設定**，由呼叫端（排程腳本）保證。
- `REVIEW_STATUS` 一律維持 MCP 預設的 `pending`（未審核）——不嘗試判斷或影響審核狀態，人工審核完全交由 DB 端事後維護。

## 步驟 1：取得候選單清單

JQL：`project = EHRMSONE AND status = "CS CONFIRM" ORDER BY updated DESC`

用 `search_issues` 取得單號清單，只取精簡欄位（key／summary／issuetype／updated），**勿裸查整包欄位**（會吃掉大量 token，見 `list_custom_fields`/`get_issue` 的既有教訓）。

## 步驟 2：逐單處理——查重優先，貴的分析放後面

對每個單號依序：

1. **先 `jira_lookup(單號)` 查重**——若 `HRMS_JIRA` 已有紀錄（不論 `REVIEW_STATUS` 是 pending/verified/rejected，只要有紀錄就算已處理過），**直接跳過該單，不進行任何後續分析**，處理下一單。
2. 若無紀錄，才進入完整分析：
   a. 平行呼叫 `recall`（系統/程式知識）、`jira_lookup`（敘述檢索，找相似案件）、`faq_lookup`、`bulletin_lookup`。
   b. `get_issue` 取得完整描述與最新評論（比照 `crisis-triage` 步驟 1 的取單方式，一次到位取精簡欄位＋完整描述＋評論）。
   c. 判斷單型——比照 `crisis-triage`「判斷單型」對照表：`issue_type`＝Crisis - Level A/B/C，或 `category`=維運／`source`=客戶／`customer` 有客戶名稱 → 維運單；皆非上述情境 → 一般單。
   d. 有附件截圖時讀取判讀；比照 `crisis-triage` 步驟 4 追碼確認根因——**但本 skill 只做唯讀分析與結論歸納，不執行任何程式碼修改**。

## 步驟 3：分類——三選一，寫入判斷

| 情境 | 判斷依據 | 動作 |
|---|---|---|
| **A. 客戶操作失誤** | 追碼／截圖／評論顯示問題成因是客戶操作誤解或資料誤植，非程式邏輯缺陷 | 不寫 `HRMS_JIRA`，寫入本地略過清單（步驟 5） |
| **B. 已有 FAQ／公告涵蓋** | `faq_lookup`／`bulletin_lookup` 命中，且內容已完整涵蓋此單的根因與解法 | 不寫 `HRMS_JIRA`，寫入本地略過清單，並記錄命中的 FAQ／公告 ID |
| **C. 有分析價值的真實根因** | 非 A、非 B，且已能明確歸納出根因與解法（追碼有具體依據，不是用猜的） | 呼叫 `jira_log` 寫入（步驟 4） |

**無法明確歸類、或追碼追不出具體根因時**（例如評論資訊不足、需要客戶端資料才能確認、issuetype 不明）→ 一律視為「無法判斷」，寫入本地略過清單（`reason=inconclusive`），**不可為了湊出結論而勉強套入 A/B/C**。

## 步驟 4：寫入 HRMS_JIRA（僅情境 C）

```
jira_log(
  jira_key, kind, title, root_cause, resolution,
  changed_files（Story 必填）, keywords
)
```

- `kind` 判斷比照 `crisis-triage` 對照表：`issuetype`＝Crisis - Level A/B/C → `Crisis`；一般開發/Bug 且確實有改程式 → `Story`（`changed_files` 必填，repo 相對路徑＋函式，勿用本機絕對路徑）
- 寫入前不必再查重（步驟 2.1 已查過）；若 `jira_log` 仍回報「已有紀錄」（例如批次執行中途被其他來源寫入），照實記錄該次衝突，跳過即可，不強行 supersede

## 步驟 5：本地略過清單（僅情境 A／B／inconclusive）

寫入本機檔案 `C:\D\JIRA_AI\skip_log.jsonl`（不存在就建立，append-only，一行一筆 JSON，不覆蓋既有內容）：

```json
{"jira_key":"EHRMSONE-XXXXX","reason":"customer_error|faq_covered|inconclusive","note":"一句話說明判斷依據","ref":"命中的 FAQ/BULLETIN ID（情境 B 才有，其餘留空）","checked_at":"ISO 8601 時間"}
```

不建立新的 DB 表——這是排程自身的執行紀錄，非團隊共用知識，不污染 `HRMS_JIRA`／`HRMS_MEMORY`。

## 步驟 6：批次回報

流程結束印到 stdout（供排程執行紀錄擷取，非互動）的彙總，需明確區分各項數字，不要混著講：

- 候選單總數
- 已有 `HRMS_JIRA` 紀錄而跳過數（步驟 2.1）
- 進入完整分析數，其中：
  - A／B／inconclusive 各自略過幾筆（略過清單）
  - C 成功寫入 `HRMS_JIRA` 幾筆
- 寫入失敗或發生例外的單號與原因

## 注意事項

- 只查詢與比對，不修改任何檔案、不執行任何 SQL UPDATE/DELETE（`HRMS_JIRA` 本身也是 DB 端 DENY UPDATE/DELETE，只能 INSERT）
- 判斷單型、追碼方法、DB 使用限制以 `crisis-triage` 的內容為權威來源，本 skill 不重複維護，只讀取引用
- 與 `crisis-triage`（互動式、單張單、輸出後停下等人確認）是互補關係：本 skill 是它的無人值守批次版，只在「有把握」時才寫入，其餘一律保守略過
