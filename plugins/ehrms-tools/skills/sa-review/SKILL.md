---
name: sa-review
description: SA 測試輔助 review。針對 status="SA Testing" 且 assignee=ziping.zhou（不看 Engineer）、尚未被 AI review 過的 EHRMSONE 單，釐清本次修改範圍（含 master 落後的版本校正），產出三面向 review（需求符合性／改A壞B影響範圍／語法流程）與測試機測試資料建議，直接以一則「AI 分析僅供參考」留言貼到 Jira 單上。僅在使用者明確指名要執行 sa-review（或日後排程呼叫）時使用，不可因為泛泛提到「幫我看 SA 測試的單」就自動套用，因為本 skill 會對 Jira 留言。參數：`dry-run`（只產草稿不貼）、`limit=N`（預設 5）、單號清單（指定處理哪些單）。
---

# SA 測試輔助 Review

EHRMS 的測試分兩關：**SA 測試**（工程人員測）→ **CS 測試**（顧問測），兩者都過才能釋出。本 skill 在 SA 測試階段先替測試者做 **code review** 與 **測試資料準備**，加快測試流程。

## 觸發限制（先讀，優先於其餘步驟）

**本 skill 不可被動觸發。** 只在以下情況才執行：

1. 使用者明確指名要執行 `sa-review`／「跑 sa-review」等明確指涉本 skill 的要求。
2. 日後掛上的本機排程呼叫 `claude -p "/ehrms-tools:sa-review"`（目前尚未掛排程，先手動）。

使用者只是泛泛聊到「看一下 SA 測試的單」不算觸發條件——本 skill 會在同事的單上直接留言，屬對外副作用動作。

`$ARGUMENTS` 可帶：
- `dry-run`：走完整分析，但**不呼叫 `add_comment`**，把原本要貼的留言全文寫進日誌（`action="dry_run"`）並印在 stdout。
- `limit=N`：本次最多處理 N 張（預設 **5**）。
- 單號（如 `33067 32725` 或 `EHRMSONE-33067`）：只處理指定的單，略過步驟 1 的 JQL 與排序（仍套用步驟 2 的跳過規則；單的狀態若不是 SA Testing，在報告中註明）。

## 適用範圍與限制

- **寫入動作僅限 `add_comment`**。Jira MCP 沒有 transition 工具，本 skill 不改單的狀態（測完移到 CS 測試由使用者自己操作）。
- **不修改任何程式碼、不建立分支、不 commit、不 checkout、不 merge、不 push。** 可執行 `git fetch origin`（只更新遠端追蹤，不動工作目錄）。
- **git 指令一律用 PowerShell 工具執行**（headless 環境下 Bash 工具的 `git` 需要核准會被擋）。
- **無人值守（排程／headless）時不可詢問使用者**；不確定的地方在留言中明確標示「未確認」，寧可少說不要猜。手動模式下遇到關鍵分歧可以詢問使用者。
- **不評估客製影響**：只看標準版（`EHRMS_GIT`），不查 `CUSTOM_GIT`。留言中加一句「未評估客製影響」。
- **DB 使用（與維運單規則不同）**：SA 測試在公司自己的測試機進行，DB MCP 連的 `EHRDB_MULTI_IE` 即測試環境，**允許唯讀 SELECT 查測試機的業務資料來找測試資料**。仍然禁止 INSERT／UPDATE／DELETE／DDL（DB MCP 本身也只接受單一 SELECT）。
- **DB MCP 工具名稱（兩組擇一，連的是同一個測試機資料庫 EHRMS-LAB-DB／EHRDB_MULTI_IE）**：優先用 plugin 版 `mcp__plugin_ehrms-tools_ehrms-database__mssql_query`／`get_table_columns`／`search_tables`；若回報「No such tool available」或 plugin 版 MCP 連線失敗（headless 下失敗會被快取 15 分鐘），改用獨立版 `mcp__ehrms-database__mssql_query`／`get_table_columns`／`search_tables`，不要因此放棄查詢。兩組都失敗才在留言標示「未查詢」並寫明原因，不可猜測資料內容。
- **留言揭露限制**：可貼員工編號、公司別、姓名與關鍵特徵（如職等、異動筆數）。**不貼**身分證號、薪資、生日、銀行帳號等敏感欄位。
- 所有 SQL 引用的表名與欄位，先以 `get_table_columns` 或 `INFORMATION_SCHEMA.COLUMNS` 確認存在才可使用。

## 留言標記（判定「是否 review 過」的唯一事實來源）

每則留言**必須**以此標記開頭（`第 N 次` 的 N 為第幾次 review，首次為 1）：

```
【AI 分析僅供參考｜SA 測試輔助 review（第 N 次）】
```

第二行固定記錄本次審過的範圍，供下次比對：

```
審過 commit：<sha1>,<sha2>…（無程式異動時寫「無」）
```

## 步驟 1：取得候選單

JQL：

```
project = EHRMSONE AND status = "SA Testing" AND assignee = "ziping.zhou@104.com.tw" ORDER BY updated ASC
```

- `ORDER BY updated ASC`＝**最舊優先**，先清積壓。
- 用 `search_issues`（精簡清單，勿裸查整包欄位）；結果超過 50 筆就翻頁。
- 取得清單後依步驟 2 逐張判斷，**實際進入完整分析的單**數達到 `limit` 就停止，其餘記為「待處理」。

## 步驟 2：跳過規則（查該單最新的 AI 標記留言）

對每張候選單先 `get_comments`（limit 30，新→舊），找**最新一則以留言標記開頭的留言**：

| 情況 | 處理 |
|---|---|
| 沒有標記留言 | 需要處理（第 1 次） |
| 有標記，且上次是正常 review（有記錄審過 commit） | 先完成步驟 3 算出目前的 commit 集合；**與留言記錄的 SHA 集合相同** → `skipped_reviewed`；**出現新 commit** → 重審（第 N+1 次） |
| 有標記，且上次是「查無程式異動」 | 標記留言**之後**有非 AI 的新評論 → 重審（第 N+1 次）；沒有 → `skipped_reviewed` |

## 步驟 3：釐清修改範圍（含版本校正）

目的：精確找出「本次修改的程式碼」，不被 master 落後造成的大量差異干擾。

### 3.1 取單
`get_issue`（`comments_limit` 取 30）：描述、所有評論（特別留意工程師寫的**測試步驟**與**修改範圍、對應分支**）、附件（圖片用 `get_attachment` 判讀）。

### 3.2 找修改位置（依序，找到即用，可合併多個來源）
1. **評論**：掃描全部評論，找分支名稱、commit SHA、修改範圍說明（這是最優先來源）。
2. `git log --all --oneline --grep="EHRMSONE-<單號>"`：取得 commit 清單（含標題有「(前置)」的前置 commit）。
3. 分支：`EHRMSONE-<單號>`、`EHRMSONE-<單號>_master`（本地與 remote）。
4. `/SQL` 資料夾中檔名含該單號的部署腳本，以及同 commit 異動的 `script/` 檔案。
5. **以上皆無** → 走「查無程式異動」流程（步驟 7 格式 B），不給判定。

### 3.3 版本校正——只看本次改動
1. `git fetch origin`。
2. 有個人分支時：`base = git merge-base origin/master <branch>`；用 **`git diff <base>...<branch>`（三點）** 只看分支自己的改動。
3. 只有 commit（已併入 lab 或版號分支，沒有個人分支）時：逐一 `git show <sha>` 或 `git diff <sha>^ <sha>`，範圍只限步驟 3.2 找到的 commit。
4. 降噪：用 `--ignore-cr-at-eol` 排除純換行差異；整檔都變時先確認是不是 CRLF↔LF 或編碼噪音，不是本次改動。
5. **編碼**：`.cls`、`.bas`、`script/StoredProcedure/*.SQL` 多為 Big5，`git diff` 可能顯示 Bin；用 `git show <rev>:<path>` 取位元組，以 PowerShell 的 codepage 950 解碼閱讀。`.asp` 可能是 UTF-8 BOM，先判斷再解碼。
6. **master 落後檢查**：若分支修改的檔案，在 `base` 之後也被 `origin/master` 修改過（`git diff <base> origin/master --name-only` 與分支修改檔交集），在報告中標示「master 已有後續異動」，並以 `git merge-tree`（模擬合併，不動工作目錄）評估合併後結果；出現衝突則列出衝突檔案，不處理。
7. 記錄本次審過的 commit SHA 集合（供留言第二行）。

### 3.4 合併狀態查證（只報告查得到證據的事實）
對每個審過的 commit，用 git 實際查證下列三項，結果寫進留言「範圍」那一行：

- **是否在 lab**：`git merge-base --is-ancestor <sha> origin/lab`（或 `git branch -r --contains <sha>`）。**未合併**要標示「測試機可能測不到本次修改」。
- **是否在 master**：`git merge-base --is-ancestor <sha> origin/master`，並可再以 `git log origin/master --grep="EHRMSONE-<單號>"` 輔助。
- **是否在版號分支**：`git branch -r --contains <sha>` 中符合 `origin/YYYYMM_NNN*` 的分支。

**判定原則：**
- 只有 git 實際查得到證據才可寫「是」。查不到寫「否」；若因合併方式（例如 squash）導致 SHA 不同而無法確認，寫「未確認」。
- **PR 編號、commit 訊息中的 "Merge pull request" 字樣不可當作「已合併 master」的依據**（PR 可能合併到 lab 或其他分支）。
- master 是版更後由專人回併，`origin/master` 的更新時間可能落後；「不在 master」不代表未釋出，不可據此推論單的狀態是否過時。
- 單子是否「已釋出」不在本 skill 的判斷範圍，不要下結論。
### 3.5 DB 物件型異動（分支只有 SQL／SP 時）
若本次異動只有 SQL 與 `script/StoredProcedure` 等 DB 物件：
1. 從 SQL 找出異動的物件名稱（`CREATE／ALTER PROCEDURE／VIEW／FUNCTION`）。
2. 以 `mssql_query` 讀測試機上的 `OBJECT_DEFINITION(OBJECT_ID('dbo.<物件>'))`（唯讀、僅此用途）。
3. 與「分支的 SQL／`script` 檔」比對，報告**測試機實際定義是否與分支一致**；不一致以測試機為準並標示差異。
4. 舊版依據＝`base`（或 `origin/master`）時的 `script/` 檔；用 DB 定義對照舊版找出實際差異，做 review。

## 步驟 4：三面向 review

### 面向 1：需求符合性（給**建議**）
依描述、評論、測試步驟，判斷修改是否做到要求：**符合／部分符合／可能遺漏**，並說明依據（指到檔案::函式或 SQL 片段）。邏輯面的疑慮（例如缺 Null 處理但不一定觸發）歸在此面向或面向 2，不放面向 3。

### 面向 2：改 A 壞 B——依賴風險（只列高風險項，給風險等級）
**目的**：從依賴範圍中挑出「改了 A、B 沒跟著改而可能壞掉」的項目，整合成精簡結論；留言**不列出全部掃描範圍**，後續要深入分析可由此往下調查。

1. **依賴範圍仍必須用掃描工具產生，不可自行 Grep、不可憑記憶列舉**（作為判定的唯一資料來源，確保每次一致）：
   ```powershell
   & 'C:\D\JIRA_AI\tools\Get-Dependents.ps1' -Diff '<base>...<tip>' -Ref origin/lab -Depth 2 -MaxShow 15 -OutFile 'C:\D\JIRA_AI\sa_review_tmp\<單號>.deps.txt'
   ```
   - 以 PowerShell 工具執行，**`timeout` 設為 300000**（約 1～2 分鐘）。
   - `-Diff`：步驟 3.3 的修改範圍（三點寫法）；只有 commit 時寫 `<sha>^...<sha>`；多個範圍各跑一次（檔名加序號）。
   - `-Ref` 固定 `origin/lab`，留言註明「掃描基準 origin/lab @<sha>」。
   - 一般 ASP 頁面視為終點；單層引用超過 400 處視為廣泛共用（直接列為高風險，原因寫「廣泛共用」）。
2. **風險等級（固定規則，不憑感覺）**：
   - **高**：同時滿足 (a) 修改單元的簽章／參數／回傳值／回傳欄位／排序／預設值／判斷條件有變，且 (b) 掃描結果中有呼叫端（含第 2 層）**不在本次修改檔案內**，即呼叫端未連帶修改。或修改單元為廣泛共用（超過 400 處）。
   - **中**：(a) 成立，但呼叫端都在本次修改檔案內，或 (a) 僅為內部實作調整（回傳與簽章不變）而依賴範圍廣。
   - **低**：只改註解／新增獨立函式／無外部呼叫端。
   - 整張單的風險等級取各項最高者。
3. **留言只列高風險項**：每項寫「修改單元 → 呼叫端（路徑:行｜所在函式）→ 原因（例如：回傳欄位由 A 改為 B，呼叫端仍讀 A，未連帶修改）」。呼叫端必須能在 deps 檔中找到，**不得加入 deps 檔以外的依賴**（額外觀察寫在「測試資料」段並標「AI 補充」）。
4. 高風險項超過 10 筆時列前 10 筆（依路徑排序），並寫「另有 N 處高風險，詳見 <單號>.deps.txt」。無高風險時寫「未發現高風險依賴」。
5. 工具失敗或逾時：寫「依賴範圍掃描未完成：<原因>」，風險等級寫「未確認」，不可改用自行搜尋補上。
6. **貼出前驗證**：確認留言每個高風險項的呼叫端路徑:行都能在 `<單號>.deps.txt` 找到（`Select-String` 檢查）；找不到的移除。（舊版 `Test-CommentDeps.ps1` 要求逐字貼出完整掃描輸出，不適用新格式，不再執行。）
7. 本面向不評估客製影響，註明「未評估客製影響」。
### 面向 3：語法流程（給**通過／不通過**）
判定依據沿用 `code-review-checklist`、`program-standards`，不另外發明標準。

- **不通過**（只看會導致執行或部署失敗的問題）：語法錯誤、`If／End If` 等區塊不成對、衝突標記殘留、MSSQL 2008 不相容語法、SQL 不具冪等性（重跑會失敗）、編碼損毀或整檔換行噪音、必然在執行期當掉的 Null／型別錯誤。
- **備註**（不影響判定）：命名、風格、缺少修復註解、註解格式不一致。

## 步驟 5：測試資料準備

> **硬性要求**：不得未經查詢就直接寫「需手動準備」。每個情境都必須**實際執行至少一次測試機查詢**再下結論；只有該情境**完全不依賴資料庫資料**（例如純前端輸入檢核、任意文字欄位）才可免查，並在留言寫明理由（如「不依賴特定資料，任一員工即可」）。留言中要寫出「已查詢」與結果筆數。

1. 依修改內容與工程師寫的測試步驟，推導 **2～4 個測試情境**（含正向情境，以及依賴範圍中可直接驗證的情境）。
2. 對每個情境推導篩選條件，先驗證欄位，再用 `mssql_query` **唯讀 SELECT** 在測試機找符合的資料（`TOP 3～5`）。
   **先用 `mcp__plugin_ehrms-tools_ehrms-memory__recall` 查相關記憶**（以修改的功能與資料表名稱描述問題），團隊已沉澱的資料表線索（例如績效 M4 的 HRMSP_／PORTALP_ 表群）會直接命中，可少走彎路。命中的記憶僅作線索，欄位與資料仍須自行驗證。

   **找表的線索（依序使用，不要只憑猜測表名前綴）：**
   1. **從修改程式碼的上下文**：diff 內的 SQL、檔案所在目錄與 VB 專案名稱（例如 `Portal_FrontendM4`、`modM4Portal.bas`、`ehrPortal/Performance/` 都屬績效 M4）。
   2. **查 `HRMS_TABLES`**：以 `FUNCTION_NAME`／`TABLE_DESC` 關鍵字（中文，如「績效」「考核」）搜尋表名。`SERVICE_CATALOG` 絕大多數是空的（1792 筆），不可依賴。
   3. **已驗證的模組表前綴對照**：
      - **績效（M4）**：後台為 `HRMSP_*`（31 張），員工入口為 `PORTALP_*`（8 張）。注意**不是 `HRMS_PER*`**。
        - 規劃與表單：`HRMSP_START_PLAN`（規劃）、`HRMSP_START_PLAN_MEMBER`（規劃對象）、`HRMSP_START_PLAN_TIMES`（期別）、`HRMSP_START_FORM`（表單）、`HRMSP_START_FORM_MEMBER`、`HRMSP_PLAN_RELATION`（評核關係）。
        - 員工作答與結果：`PORTALP_EMPLOYEE_PLAN`（員工的規劃）、`PORTALP_EMPLOYEE_PLAN_STEP`（階段／STEP_STATUS）、`PORTALP_FORM_ANSWER`（作答）、`PORTALP_FORM_SUB_SCORE`／`PORTALP_FORM_TOTAL_SCORE`（分數）。
        - 其他：`HRMSP_RESULT_ALLOT*`（結果分配）、`HRMSP_JUDGMENT*`（評等）、`HRMSP_EXTRA_*`（額外目標／欄位）、`HRMSP_WORK_TALENT_*`（工作／人才）。
      - 以 `ZZ_` 開頭的是客製表，只屬特定客戶，不納入標準測試資料。
      - **代碼值不是表名／欄位名**：績效表單的欄位以代碼值定義（例如 `TA001`＝項次、`TA007`＝權重），代碼存在 `HRMSP_EXTRA_COLUMN.EXTRA_COLUMN_CODE`，作答存在 `PORTALP_FORM_ANSWER.COLUMN_CODE`；頁面欄位名稱形如 `sub{代碼}_{列}_{欄}`。在程式或頁面看到 `TAxxx` 這類代碼時，要查這兩處的**欄位值**，不可去找名為 TAxxx 的表或欄位，更不可因此下結論「測試機沒有資料」。
   4. 找到候選表後，仍須先以 `get_table_columns` 驗證欄位，並以 `COUNT(*)` 確認測試機有資料再設計篩選條件。
3. 留言中列出：員工編號、公司別、姓名、為何適合（關鍵特徵）。
4. **找不到符合的資料**：只說明「測試機找不到」並列出需要哪些條件，**不提供建立資料的步驟或 INSERT／UPDATE 草稿**。
5. 無法從資料庫推得的步驟（例如排程發信設定）不處理，註明「需手動準備」。

## 步驟 5.5：實測驗證與 review 失敗判定

**目的**：只讀碼的疑慮不等於失敗。要判「review 失敗」必須有可重現的證據，並且在留言中補上獨立的【失敗報告】，讓工程師能直接拿去修。

### 5.5.1 實測驗證（有疑慮的項目才做）
- 對步驟 4 中「可能出錯」的項目，用步驟 5 找到的測試資料實際驗證：先取**重跑／操作前**的資料快照，執行後再取**之後**的資料，比對差異。
- **headless（排程）模式**：DB MCP 只能 SELECT，AI 無法執行 SP 或操作畫面，不可自行建立／修改測試資料。此時只能在留言列出「待實測」的情境與前後對照用的 SELECT 語法，**不得判失敗**。
- **手動模式**：由使用者在測試機執行（SP 重跑、畫面操作），AI 提供重跑用語法與前後對照的 SELECT 語法，使用者回報後 AI 再查詢結果比對。AI 不代為執行寫入類動作。
- 比對時要記錄「操作前」「操作後」的關鍵欄位值，供失敗報告引用；操作前資料若無法確認是哪個版本產生，需在報告註明「基準版本未確認」。

### 5.5.2 判定「review 失敗」（符合任一項才判失敗）
1. **實測不符**：測試機實測結果與預期不符，且有操作前／後資料可對照。
2. **面向 3 不通過**：語法或流程問題會導致執行或部署失敗。
3. **面向 1 為「不符合」且證據確鑿**：能同時指出具體資料／畫面結果與對應的程式片段。

只靠讀碼推論、沒有實測的疑慮：**不判失敗**，寫在對應評估中並標「未實測」。修改的某一部分失敗、其他部分未驗證時，仍判整張單 review 失敗，報告中分開列出「失敗的修改」與「未驗證的修改」，不可把未驗證的部分算成失敗。

### 5.5.3 失敗報告格式
判定失敗時，在留言最前面（標記與「審過 commit」之後）加入下列區塊，其餘評估內容照格式 A／B 保留：

```
[AI review失敗]
測試資料：<公司別>｜<員工編號> <姓名>（ID <系統內部 ID，若有>）｜<關鍵特徵，例如到職日 yyyy/mm/dd 當天異動身分類別>
失敗情境：<一句：什麼條件下，哪個結果被改錯，預期應是什麼>
實測結果：操作前 <關鍵欄位值>　→　操作後 <關鍵欄位值>（基準版本未確認時註明）
問題程式碼：<檔案或物件::區塊，行號或修改標記>
<3～6 行造成問題的關鍵程式碼>
原因與建議：<一到兩句；建議僅供參考，不修改程式>
未驗證：<本次修改中尚未實測的部分，無則寫「無」>
```

- **測試資料只列失敗案例實際用到的資料**（1～3 筆），並遵守留言揭露限制（不貼身分證號、薪資、生日、銀行帳號）。其他可供測試的資料仍放在「測試資料」段。
- 「問題程式碼」必須是從 diff 或測試機 `OBJECT_DEFINITION` 實際讀到的內容，不得憑記憶重寫；DB 物件型異動以測試機定義為準並註明來源。
- 所有欄位必須有證據，查不到的寫「未確認」，不可留占位符。
- 失敗後使用者修正並要求重審時，重審沿用步驟 2 的規則；若上次是失敗，需在新留言中明確寫出「上次失敗項目：已修正／仍存在／未驗證」。

## 步驟 6：留言

> **格式為硬性要求**：
> - 判定 review 失敗時（步驟 5.5.2），必須帶【失敗報告】區塊（步驟 5.5.3），且不可省略「操作前／操作後」。
> - 區塊標題（結論／修改摘要／評估 1～3／測試資料）必須完整出現，順序固定；「結論」一律放在最前面。
> - 面向 1 的符合程度、面向 2 的風險等級、面向 3 的通過／不通過都必須明確寫出。
> - 留言中**不可出現未替換的占位符**（如「N」「<…>」「xxx」）；無法給出時整句省略或寫「未確認」。

### 格式 A：有程式異動（純文字，簡潔好讀）

```
【AI 分析僅供參考｜SA 測試輔助 review（第 N 次）】
審過 commit：<sha1>,<sha2>

結論：<AI review失敗／未發現問題／有疑慮待實測>（<一句話理由>）

（僅在判定 review 失敗時加入：步驟 5.5.3 的失敗報告區塊，從 [AI review失敗] 開始；未失敗時整段省略）

修改摘要
<一到兩行：分支或 commit／修改檔案::函式／改了什麼>
合併狀態：在 lab：是／否｜在 master：是／否／未確認｜版號分支：<名稱或無>

評估（每項一行，細節只在必要時補一行）
1. 需求符合性：符合／部分符合／可能遺漏　— <一句依據，指到檔案::函式或 SQL 片段>
2. 改A壞B：風險 高／中／低／未確認　— <一句；只列高風險項（最多 3 筆，格式「修改單元 → 呼叫端 路徑:行」），無則寫「未發現高風險依賴」>（掃描基準 origin/lab @<sha>；未評估客製影響）
3. 語法流程：通過／不通過　— <一句；不通過需寫原因>

測試資料（已查詢，N 筆；最多列 3 筆）
- <情境>：<公司別>｜<員工編號> <姓名>｜<關鍵特徵>　→　預期：<結果>
找不到資料的情境：<需要的條件>（測試機目前查無）
需手動準備：<無則省略>
```

格式簡化原則：
- 整則留言以「結論」開頭，讓讀者第一眼就知道要不要往下看；詳細推論不放留言，只留可驗證的事實（欄位值、程式片段、筆數）。
- 一般控制在 25 行內（失敗報告區塊另計）；備註類、風格類內容不列。

### 格式 B：查無程式異動

```
【AI 分析僅供參考｜SA 測試輔助 review（第 N 次）】
審過 commit：無
查無對應程式異動，無法進行 code review。
已查：評論、commit 單號、分支、/SQL 資料夾、測試機 DB 物件。
可能原因：只改 DB 物件／資料、走客製、純設定、尚未推分支。
若有修改，請在評論留下分支名稱或 commit，下次執行會重新 review。

評估
1. 需求符合性（僅依描述與評論，無程式 diff）：…

- **dry-run 模式**：不呼叫 `add_comment`，把留言全文寫入日誌並印在 stdout。
- 一般模式：呼叫 `add_comment` 貼出。**貼出前再確認該單最新標記留言仍符合步驟 2 的判斷**（避免同時間重複貼）。

## 步驟 7：本地日誌（輔助，非事實來源）

**日誌只能 append：不得修改、刪除或改寫既有的行**（需要更正時另 append 一筆更正紀錄）。每張處理過的單 append 一行到 `C:\D\JIRA_AI\sa_review_log.jsonl`。**寫入時不可產生 UTF-8 BOM**（不要用 `Add-Content`／`Out-File` 建新檔）；用 PowerShell 的 `[IO.File]::AppendAllText(path, line + "`n", (New-Object System.Text.UTF8Encoding($false)))`：

```json
{"jira_key":"EHRMSONE-XXXXX","round":1,"action":"commented|dry_run|skipped_reviewed|skipped_not_sa_testing|pending_limit|error","commits":["sha"],"verdict":{"req":"符合|部分符合|可能遺漏|","syntax":"通過|不通過|","result":"pass|fail|unverified"},"failure":{"test_data":"失敗案例用到的資料","scenario":"失敗情境","before":"操作前","after":"操作後","code":"問題程式碼位置","unverified":"未驗證的修改"},"dependents":{"ref":"origin/lab@sha","l1":0,"l2":0},"comment_body":"實際留言全文（未貼則為草稿）","note":"一句話說明","checked_at":"ISO 8601"}
```

## 步驟 8：批次回報（stdout 彙總）

- 候選單總數；本次處理幾張、略過幾張（`skipped_reviewed`）、因 `limit` 待處理幾張（列單號）。
- 處理結果分佈：格式 A／格式 B 各幾張；面向 3 不通過幾張（列單號）；改A壞B 風險為「高」的單（列單號與高風險項筆數）。
- **review 失敗的單**（`verdict.result = fail`）：列單號與一句話失敗情境；`result = unverified` 的單（有疑慮但未實測）另列「待實測」單號。`failure` 欄位僅在 `fail` 時填寫，其餘省略。
- 執行失敗或例外的單號與原因。

## 注意事項

- 判定不確定時寫「未確認」，不可用猜測填滿留言。
- 同事的程式碼 review 語氣保持中性、就事論事，只陳述證據與風險。
- 判斷單型、DB 規則：維運／客戶回報問題（Crisis）仍遵守 `crisis-triage` 的「不查客戶資料」；本 skill 處理的是測試機上的 SA 測試，見上方 DB 使用例外。
- 與 `jira-goal-watch`（複查未處理單進度）、`jira-auto-log`（CS CONFIRM 結案紀錄）用途不同，不互相取代。
