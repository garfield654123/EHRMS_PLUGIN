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

### 面向 2：改 A 壞 B——這次修改的依賴範圍（只陳述事實，不評風險、不下判定）
**目的**：把「這次修改影響到哪些程式」寫清楚，而且**每次執行的結果必須一致**。風險高低、要不要迴歸測試、測什麼，一律由使用者自行判斷，本面向**不輸出**無／低／中／高、不輸出「有風險／高風險」等判定，也不寫建議迴歸測試。

1. **必須用掃描工具產生依賴範圍，不可自行 Grep、不可憑記憶或推測列舉**（自由搜尋每次結果會不同）：
   ```powershell
   & 'C:\D\JIRA_AI\tools\Get-Dependents.ps1' -Diff '<base>...<tip>' -Ref origin/lab -Depth 2 -MaxShow 15 -OutFile 'C:\D\JIRA_AI\sa_review_tmp\<單號>.deps.txt'
   ```
   - 以 PowerShell 工具執行，**`timeout` 設為 300000**（掃描約 1～2 分鐘）。
   - `-Diff`：步驟 3.3 決定的修改範圍（三點寫法）。只有 commit 沒有分支時，寫 `<sha>^...<sha>`。有多個分支或範圍就各跑一次。
   - `-Ref`：固定用 `origin/lab`（測試機狀態），即使審的 commit 尚未進 lab 也一樣；留言中註明「掃描基準 origin/lab @<sha>」。
   - 工具會**自動從 diff 萃取修改單元**（SP／View／UDF 物件、被改動的 VB／ASP 函式、共用 include 檔），並掃描第 1 層（直接引用）與第 2 層（引用上一層所在函式或共用檔者）。一般 ASP 頁面視為終點，不再往下追；單層引用超過 400 處視為廣泛共用，不再展開。
2. **照貼工具輸出的事實**：修改單元、基準 sha、第 1／2 層的筆數與分布、依路徑排序的代表清單、「另有 N 處未展開」。**不得刪減、改排序、改數字**。可用一行白話補充這些依賴是什麼功能（例如「薪資計算與薪資單產出」），但不得評估風險。
   **貼出前必須通過程式驗證（不可跳過）**——AI 逐行抄寫容易縮短或改寫，過去的草稿曾被發現把第 2 層清單截短、改寫「另有 N 處」、簡寫修改範圍：
   1. 掃描工具必須帶 `-OutFile`（見上方指令；有多個範圍時檔名加序號，如 `<單號>_2.deps.txt`），輸出才會留存成檔。
   2. 組好**完整留言**後，用 PowerShell 以 `[IO.File]::WriteAllText('C:\D\JIRA_AI\sa_review_tmp\<單號>.comment.txt', $body, (New-Object System.Text.UTF8Encoding($false)))` 寫成檔案（headless 下 Write 工具可能被擋，用 PowerShell 寫檔）。
   3. 執行 `& 'C:\D\JIRA_AI\tools\Test-CommentDeps.ps1' -CommentFile 'C:\D\JIRA_AI\sa_review_tmp\<單號>.comment.txt' -DepsFile 'C:\D\JIRA_AI\sa_review_tmp\<單號>.deps.txt'`（多個 deps 檔就都列入）。
   4. 輸出 **PASS** 才可呼叫 `add_comment`（dry-run 則才可寫入日誌）。輸出 **FAIL** 會列出缺少的行：把這些行**原樣**補進留言，重新寫檔並再驗證；最多重試 3 次，仍 FAIL 就不貼，日誌記 `action="error"`、`note` 寫明驗證失敗。
   5. 留言中的「修改範圍」各行、第 1／2 層的代表清單、「…另有 N 處未展開」那行，都必須**逐字**照工具輸出，不可改寫、簡寫、縮短。
   6. 工具沒有輸出第 2 層（沒有可往上追的函式或共用檔）時，留言寫「第 2 層：無（無可往上追的函式或共用檔）」。
   7. **面向 2 不得加入掃描工具輸出以外的依賴**：不可自行用 git grep 或其他方式補充呼叫端、頁面或函式（即使確實查到也不行），否則同一張單每次產出的依賴範圍會不一致。工具將一般 ASP 頁面視為終點，因此頁面型修改的依賴範圍為「無」是預期結果，照實寫「第 1 層：無（頁面為終點）」。額外的觀察可寫在【建議測試重點】，並標明「AI 補充，非掃描工具輸出」。
3. **資料流變更（對 diff 的客觀描述）**：從 diff 讀出這次改變了哪些欄位、回傳值、排序、預設值、判斷條件，條列「舊 → 新」，不評估影響程度。
4. 第 2 層是**以名稱比對**，不同類別的同名函式可能一併列入，工具輸出已註明，原樣呈現即可。
5. 工具出現「廣泛共用，不再往下展開」時原樣呈現，不自行補算。
6. 工具失敗或逾時：寫「依賴範圍掃描未完成：<原因>」，不可改用自行搜尋補上。
7. 本面向不評估客製影響，留言中註明「未評估客製影響」。
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

## 步驟 6：留言

> **格式為硬性要求，不可省略或壓縮成單句**：
> - 章節標題與編號（【1. 需求符合性｜建議】【2. 改A壞B｜依賴範圍】【3. 語法流程｜通過／不通過】【測試資料建議】【建議測試重點】）必須完整出現。
> - 面向 2 必須**完整貼出掃描工具的輸出事實**（修改單元、基準、第 1／2 層筆數與分布、代表清單、未展開筆數），不得壓縮成一句話或自行刪改。
> - 面向 3 的通過／不通過、面向 1 的符合程度都必須明確寫出；面向 2 不寫風險或判定。
> - 留言中**不可出現未替換的占位符**（如「N」「<…>」「xxx」）；無法給出具體數字或名稱時，整句省略或改寫成「未確認」。

### 格式 A：有程式異動（純文字，簡潔好讀）

```
【AI 分析僅供參考｜SA 測試輔助 review（第 N 次）】
審過 commit：<sha1>,<sha2>
範圍：<分支／commit 來源>，相對 <base> 的本次改動；在 lab：是／否；在 master：是／否／未確認；版號分支：<名稱或無>；master 落後：有／無影響
修改檔案：<檔案::函式>（精簡列出）

【1. 需求符合性｜建議】符合／部分符合／可能遺漏
- 依據：…

【2. 改A壞B｜依賴範圍（事實，掃描基準 origin/lab @<sha>）】
修改範圍（逐行原樣貼出工具輸出的「修改範圍」各行）：
  <逐行貼>
查詢單元：<工具輸出的查詢單元>
第 1 層直接引用：共 N 處、M 個檔案；分布：<層級與筆數>
  [層級] 路徑:行｜所在函式｜片段（依路徑排序，最多 15 筆）
  …另有 N 處未展開
第 2 層（名稱比對，同名函式可能一併列入，僅供範圍參考）：共 N 處、M 個檔案；分布：…
  （同上；若工具標示廣泛共用則原樣寫出）
這些依賴對應的功能：<一行白話>
資料流變更：<欄位／回傳值／排序／預設值／條件：舊 → 新>
未評估客製影響。風險與迴歸範圍請自行判斷。
【3. 語法流程｜通過／不通過】
- 不通過原因（若有）：…
- 備註：…

【測試資料建議】
情境 1：<情境>
- 員工編號 xxx｜公司別 xxx｜姓名 xxx｜特徵：…
找不到資料的情境：需要 <條件>（測試機目前查無）
需手動準備：…

【建議測試重點】
- …
```

### 格式 B：查無程式異動

```
【AI 分析僅供參考｜SA 測試輔助 review（第 N 次）】
審過 commit：無
查無對應程式異動，無法進行 code review。
已查：評論、commit 單號、分支、/SQL 資料夾、測試機 DB 物件。
可能原因：只改 DB 物件／資料、走客製、純設定、尚未推分支。
若有修改，請在評論留下分支名稱或 commit，下次執行會重新 review。

【1. 需求符合性｜建議】（僅依描述與評論，無程式 diff）
- …
```

- **dry-run 模式**：不呼叫 `add_comment`，把留言全文寫入日誌並印在 stdout。
- 一般模式：呼叫 `add_comment` 貼出。**貼出前再確認該單最新標記留言仍符合步驟 2 的判斷**（避免同時間重複貼）。

## 步驟 7：本地日誌（輔助，非事實來源）

**日誌只能 append：不得修改、刪除或改寫既有的行**（需要更正時另 append 一筆更正紀錄）。每張處理過的單 append 一行到 `C:\D\JIRA_AI\sa_review_log.jsonl`。**寫入時不可產生 UTF-8 BOM**（不要用 `Add-Content`／`Out-File` 建新檔）；用 PowerShell 的 `[IO.File]::AppendAllText(path, line + "`n", (New-Object System.Text.UTF8Encoding($false)))`：

```json
{"jira_key":"EHRMSONE-XXXXX","round":1,"action":"commented|dry_run|skipped_reviewed|skipped_not_sa_testing|pending_limit|error","commits":["sha"],"verdict":{"req":"符合|部分符合|可能遺漏|","syntax":"通過|不通過|"},"dependents":{"ref":"origin/lab@sha","l1":0,"l2":0},"comment_body":"實際留言全文（未貼則為草稿）","note":"一句話說明","checked_at":"ISO 8601"}
```

## 步驟 8：批次回報（stdout 彙總）

- 候選單總數；本次處理幾張、略過幾張（`skipped_reviewed`）、因 `limit` 待處理幾張（列單號）。
- 處理結果分佈：格式 A／格式 B 各幾張；面向 3 不通過幾張（列單號）；依賴範圍第 1 層筆數最多的單（列單號與筆數）。
- 失敗或例外的單號與原因。

## 注意事項

- 判定不確定時寫「未確認」，不可用猜測填滿留言。
- 同事的程式碼 review 語氣保持中性、就事論事，只陳述證據與風險。
- 判斷單型、DB 規則：維運／客戶回報問題（Crisis）仍遵守 `crisis-triage` 的「不查客戶資料」；本 skill 處理的是測試機上的 SA 測試，見上方 DB 使用例外。
- 與 `jira-goal-watch`（複查未處理單進度）、`jira-auto-log`（CS CONFIRM 結案紀錄）用途不同，不互相取代。
