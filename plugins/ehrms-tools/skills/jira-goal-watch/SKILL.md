---
name: jira-goal-watch
description: 僅供本機排程（Windows Task Scheduler，每天 02:00）headless 呼叫 `claude -p "/ehrms-tools:jira-goal-watch"`，或使用者明確指名要求執行本 skill 時使用；不可因為使用者只是泛泛提到「檢查我的 jira 單」「review 未處理的單」就自動判斷要套用本 skill。複查 EHRMSONE 專案中 ziping.zhou 名下未處理的單：issuetype=Story 走「bug 單進度報告」分支，issuetype=Crisis 走「維運問題判斷」分支（快篩＋追碼＋備妥客戶端查詢語法），判斷後直接在該單加一則內部留言。全程不可詢問使用者，目前 Jira MCP 只有 add_comment 可用，不支援更新狀態/transition。
---

# Jira Goal Watch — 下班後未處理單複查批次

## 觸發限制（先讀，優先於其餘步驟）

**本 skill 不可被動觸發。** 只在以下兩種情況才執行本文件的流程：

1. 本機排程（Windows Task Scheduler `EHRMS_Jira_Goal_Watch`，每天 02:00）呼叫 `claude -p "/ehrms-tools:jira-goal-watch"`。
2. 使用者在對話中**明確指名**要執行 `jira-goal-watch`／「幫我跑 goal watch」等明確指涉本 skill 的要求。

使用者只是泛泛聊到「檢查一下 jira」「review 我的單」「有沒有未處理的單」，**不算**觸發條件，不可自行判斷套用本 skill——因為本 skill 會直接對 Jira 留言，是有對外副作用的動作，不應該被模糊的對話內容意外觸發。

## 適用範圍與限制

- **headless 批次流程，全程不可詢問使用者**（無人在場執行）。判斷沒有把握時，寧可略過不留言，也不可用猜測的內容留言到 Jira 上。
- **git 指令一律用 PowerShell 工具執行**：headless 環境下 Bash 工具的 `git` 需要核准會被擋，PowerShell 版可正常執行（例如 `git log --all --grep`、`git -C C:/D/CUSTOM_GIT show`）。
- **寫入動作僅限 `add_comment`**——目前 Jira MCP 沒有更新狀態/transition 的工具，不可嘗試用其他方式（例如硬改 summary/description）模擬狀態變更。
- 每則自動留言**必須**以固定標記開頭：
  ```
  【AI 排程自動回覆｜依 ziping.zhou 設定的 goal 執行，如有誤請忽略並等待本人確認】
  ```
- 沿用 `crisis-triage` 的 DB 使用限制：EHRMS 是架在客戶端的地端系統，公司內可連的 DB **沒有客戶資料**。禁止用 DB MCP 查客戶的公司、員工、假勤、薪資等業務資料，也禁止用 DB MCP 找測試資料重現問題。需要資料驗證時，一律是「擬 SQL 給客戶端執行」，不是自己連客戶 DB 查。（結構類讀取的例外見步驟 5 末段。）
- **排程預設不做 dry-run 分階段**——排程呼叫（不帶參數）直接正式留言；用下面嚴格的觸發條件把關取代分階段驗證。
- **手動 dry-run 開關**：使用者手動呼叫且 `$ARGUMENTS` 含 `dry-run`（或「試跑」）時，走完整分析流程，但**一律不呼叫 `add_comment`**，改把「原本會留言的內容」寫進日誌（`action="dry_run"`，`comment_body` 填該留言全文）並在步驟 7 彙總中列出。用於新增／調整判斷邏輯後先驗證輸出品質，不對 Jira 產生任何寫入。排程不會帶此參數，故不影響排程行為。
- **不做主動失敗通知**——排程失敗只留在本機 log／Task Scheduler 執行紀錄，不主動推播。
- **`$ARGUMENTS`（goal 文字）用途**：排程呼叫時通常不帶參數，直接套用本文件的固定流程。若使用者手動帶了自訂 goal 文字，可用來臨時調整本次的複查角度／範圍，但**不可放寬**上述安全限制（速率上限、留言標記、唯讀原則等一律優先於 goal 內容）。

## 步驟 1：取得候選單清單

```
project = EHRMSONE AND (
  (status = "To Do" AND "customfield_13907" = "ziping.zhou@104.com.tw")
  OR
  (status IN ("待處理", "In Progress", "上Lab") AND assignee = "ziping.zhou@104.com.tw" AND "customfield_13907" = "ziping.zhou@104.com.tw")
)
ORDER BY updated ASC
```

（`customfield_13907` = Engineer 欄位，與 Jira 的 `assignee` 是不同概念——assignee 可能是前線顧問，Engineer 才是實際負責的工程師；已用真實資料驗證過兩者常常不同人。`To Do` 狀態只看 Engineer，其餘狀態要 assignee 與 Engineer 都符合。`ORDER BY updated ASC` 讓最久沒動的單優先排入，避免每晚都被同一批新單佔滿速率上限。）

用 `search_issues` 取得精簡欄位（key／summary／status／issuetype／updated），**勿裸查整包欄位**。

## 步驟 2：分類

- `issuetype = "Story"` → **bug 單分支**（步驟 4）
- `issuetype in ("Crisis - Level A", "Crisis - Level B", "Crisis - Level C")` → **維運分支**（步驟 5）
- 其他 issuetype → 不屬於本 skill 處理範圍，記錄 `action="skipped_uncategorized"`，不分析、不留言

## 步驟 3：共用跳過規則——查最新留言是否已由 AI 處理過

對每個分類後的候選單，先用 `get_comments`（少量筆數即可，最新優先）看最新一則留言：

- 若最新留言的開頭是本 skill 的固定標記（見上方限制）→ 代表上次排程已經處理過、之後沒有人（客戶/同事）再回應 → 記錄 `action="skipped_already_handled"`，跳過，不進入完整分析。
- 若最新留言不是 AI 標記（不論是完全沒有 AI 留言過，或 AI 留言過但之後有人類又回應了）→ 視為需要（重新）處理，進入分類對應的完整分析。
- **不重複留言**：要留言前，先與該單最近一則 AI 標記留言比對；若本次結論（分類／commit／判斷）與上次實質相同，記錄 `action="skipped_unchanged"`，不留言（dry-run 亦同，不產生重複的 `dry_run`）。
- **工程師已有完整結論且無新證據不補充**：若該單最新留言出自 Engineer 本人，且內容已含追碼結論或客戶端查詢包，而本次分析找不到新增證據 → 記錄 `action="skipped_unchanged"`，不留言。
- **無新動態不重跑**：上述只涵蓋「已留言」的單。對每張單另讀 `C:\D\JIRA_AI\goal_watch_log.jsonl` 中該單最新一筆紀錄；若其 `action` 為 `skipped_inconclusive`／`skipped_low_confidence`，且該單 Jira `updated` 時間不晚於該紀錄的 `checked_at`（代表之後沒有新評論／新資訊）→ 記錄 `action="skipped_unchanged"`，跳過，不進入完整分析。這避免同一張資訊不足的單每晚重複消耗額度（過去單張被重跑多達 11 次）。

## 步驟 4：bug 單分支（issuetype=Story，上限 10 則/晚）

1. `get_issue`（含最新評論）取得完整描述與對話串。
2. 掃描留言內容，找是否有人提到**特定分支名稱**（例如 `SC_YYYYMMDD_模組` 這類慣用命名，或留言中明講的分支/commit）。
   - 有提到 → 以該分支為主要查證對象。
   - 沒提到 → 依序查 `lab` 分支、`master` 分支。
3. 在 `C:\D\EHRMS_GIT` 這個 repo 用 `git log --all --grep="<issue_key>"`（或鎖定步驟 2 找到的分支 `git log <branch> --grep="<issue_key>"`）搜尋是否有提到這個 Jira 單號的 commit；找到後用 `git branch --contains <commit>` 或直接查該分支歷史，確認是否已經 merge 進 `lab`。
4. 判斷結果：
   - 有找到對應 commit → 報告「已在程式碼中修正，是否已上 lab：是/否」，並附上簡短說明（commit 摘要、分支名稱，不附完整 diff）。
   - 完全查無對應 commit → **不可用猜的**，留言內容明講「查無對應 commit，需人工確認目前處理進度」。
5. 留言＝固定標記＋上述報告內容，呼叫 `add_comment`（dry-run 模式改為只寫日誌，不呼叫）。

## 步驟 5：維運分支（issuetype=Crisis，上限 5 則/晚）

> 本分支的目標是「儘可能精準分析」：**結論必須有證據（程式位置／FAQ／截圖），需要客戶端資料時要事先備好查詢語法**。過去只憑評論與描述做三選一分類，結論流於含糊（見 `C:\D\JIRA_AI` skip_log 統計），因此追碼為硬性步驟，不可省略。

### 5.1 取單與快篩（並行呼叫）

1. `get_issue`（含最新評論；結構化欄位 `customer`／`severity`／`category` 一併記下）。
2. 同時以問題敘述呼叫 ehrms-memory：`recall`（系統知識與 Engineer 入口）、`jira_lookup`（過往相似案，⏳未審核者僅供方向、須自行追碼驗證）、`faq_lookup`、`bulletin_lookup`。
3. 有圖片附件用 `get_attachment` 判讀；**影片（mp4 等）無法判讀**，不猜，於日誌 note 註明「僅有影片」。

### 5.2 分類（五選一，每一類都要有具體依據）

| 分類 | 判定依據 | 追碼（5.3） |
|---|---|---|
| 需求 | 客戶要的是現有系統沒有的功能（描述／對話明確） | 可免（FAQ/既有功能確認即可） |
| 設定／操作問題 | FAQ／公告命中且完整涵蓋，或追碼確認為參數／權限／操作所致 | FAQ 完整命中可免，否則必做 |
| 程式缺陷（bug） | 追碼可指出邏輯錯誤 | **必做** |
| 環境／資料問題 | 資料異常、主機時間不同步、版本／DLL 未部署、客製落後標準版等 | **必做** |
| 資訊不足 | 描述、截圖、評論皆無法判斷 | —（不留言，見 5.5） |

不可為了留言而勉強歸類；「設定／需求／諮詢」是正當分類，不等於失敗。

### 5.3 追碼（必做項目）

- **入口**：優先採 `recall` 命中的 Engineer `entry_path`；沒有才以功能關鍵字 Grep 定位，比照 `crisis-triage` 步驟 4。Big5 的 `.cls` 先以 PowerShell codepage 950 讀出再搜尋中文。
- **呼叫鏈**：ASP → VB6 → SP/View/Function，追到能說明行為的那一層。
- **SP/View/Function 定義**：先查 repo（`script/StoredProcedure`、`script/View`、`script/Function`）；repo 沒有、或版本可疑（例如只有舊版同名系列）時，改用 DB MCP 讀取物件定義（見下方「DB 使用限制的例外」）。
- **客製懷疑**：`customer` 有客戶名稱且 `recall`／描述提到客製，或標準版程式碼無法解釋客訴行為時，比照 `custom-locate` 找 `C:/D/CUSTOM_GIT` 的客戶分支，但**排程環境一律唯讀、不切換分支**：用 `git -C C:/D/CUSTOM_GIT branch -a` 找分支，再以 `git -C C:/D/CUSTOM_GIT show <分支>:<路徑>` 或 `git -C C:/D/CUSTOM_GIT grep <關鍵字> <分支> -- <目錄>` 讀取，**禁止 `git checkout`**（CUSTOM_GIT 是共用工作目錄）。
- 結論須具體到「檔案::函式（行）」，不可只寫模組名稱。

### 5.4 需客戶端資料時：預先備好查詢包

追碼後若仍需客戶資料才能確認，針對每個「根因假設」各備一段：

```
假設 H1：一句話說明
SQL（唯讀 SELECT，條件用佔位符如 @EMPLOYEE_ID、@DATE，MSSQL 2008 相容）：
  ...
結果解讀：若 <條件> → H1 成立；若 <條件> → 排除 H1，改看 H2
```

- **留言前必須驗證**：查詢包引用的每個表名與欄位，都要用 `get_table_columns` 或 `mssql_query` 查 `INFORMATION_SCHEMA.COLUMNS` 確認存在，驗證過才可寫進留言；未驗證的欄位不得出現在查詢包，並在留言的「需客戶端確認」段末註明「欄位已以 schema 驗證」。
- 引用的表名／欄位先以 `get_table_columns`／`search_tables` 確認存在；**不對業務資料表執行這些 SQL**（公司 DB 無客戶資料），只擬草稿。
- 附註「請貴司資料庫管理人員於連線時協助查詢，結果請回覆到本單」。

### 5.5 信心分級與留言

- **高**：追碼到明確缺陷，或 FAQ／參數／過往已審核案例與本單完整吻合。
- **中**：有具體候選根因，但需客戶端資料確認（須附 5.4 查詢包）。
- **低**：僅推測。→ **不留言**，日誌記 `action="skipped_low_confidence"`，`note` 寫明已查到什麼、還缺什麼（供人工接手）。
- 「資訊不足」同樣不留言，日誌 `note` 列出缺哪些資訊。

留言（對象為內部工程師，可直接引用程式位置）格式：

```
【AI 排程自動回覆｜依 ziping.zhou 設定的 goal 執行，如有誤請忽略並等待本人確認】
分類：需求｜設定問題｜程式缺陷｜環境／資料問題　信心：高｜中
結論：一到三句，直接講原因
證據：
- 截圖／描述：…
- 程式：檔案::函式（行）…（客製則註明客戶分支）
- FAQ／公告／過往單：ID（未審核者註明「未經審核」）
需客戶端確認（若有）：H1…（SQL 與結果解讀見 5.4）
建議下一步：…
```

呼叫 `add_comment`（dry-run 模式改為只寫日誌，不呼叫）。

### DB 使用限制的例外（僅限 5.3／5.4）

沿用「無客戶資料、不查業務資料」原則，但下列**結構類**讀取允許使用 DB MCP：`get_table_columns`、`search_tables`；以及 `mssql_query` 僅限讀取物件定義與規則資料（`OBJECT_DEFINITION(...)`、`sys.sql_modules`、`INFORMATION_SCHEMA`、`HRMS_TABLES`、`hrms_sys_rule`）。**禁止**對 `hrms_*`／`portal_*` 業務資料表 SELECT 資料列，禁止任何 INSERT/UPDATE/DELETE/DDL。
## 步驟 6：寫入本地日誌

每張處理過的單（不論哪個分支、哪個結果）都寫一行到 `C:\D\JIRA_AI\goal_watch_log.jsonl`（不存在就建立，append-only）：

```json
{"jira_key":"EHRMSONE-XXXXX","branch":"story|crisis|uncategorized","action":"commented|dry_run|skipped_already_handled|skipped_unchanged|skipped_inconclusive|skipped_low_confidence|skipped_uncategorized|rate_limited","comment_body":"實際留言內容（無留言則留空）","category":"需求|設定問題|程式缺陷|環境資料問題|資訊不足|","confidence":"高|中|低|","note":"一句話說明判斷依據（低信心/資訊不足須寫已查到什麼、還缺什麼）","checked_at":"ISO 8601 執行時間"}
```

不建立 DB 表，這是本排程自身的執行紀錄，不寫入 `HRMS_JIRA`／`HRMS_MEMORY`。

## 步驟 7：批次回報

流程結束印到 stdout 的彙總，需明確區分各項數字：

- 候選單總數，其中 story／crisis／uncategorized 各自幾筆
- 因「已由 AI 處理過」跳過幾筆
- bug 單分支：commented／查無 commit 幾筆
- 維運分支：commented（含各分類 需求／設定問題／程式缺陷／環境資料問題 筆數，以及信心 高/中 各幾筆）／skipped_low_confidence／skipped_inconclusive／skipped_unchanged 各幾筆
- 因速率上限未處理幾筆（rate_limited）
- 失敗或例外的單號與原因

## 注意事項

- 只查詢、判斷、留言，不修改任何檔案、不建立分支、不 commit、不執行任何 SQL UPDATE/DELETE。
- 判斷單型、追碼方法、DB 使用限制以 `crisis-triage` 為權威來源，本 skill 不重複維護。
- 與 `jira-auto-log`（掃 CS CONFIRM 單、寫入 `HRMS_JIRA` 知識庫、不對外留言）是互補但不同用途：查詢範圍、分類邏輯、寫入對象都不同，不要互相取代。
