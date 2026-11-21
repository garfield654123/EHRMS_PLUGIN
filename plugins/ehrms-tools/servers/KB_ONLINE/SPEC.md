# EHRMS 線上知識庫 MCP：規格與計畫

> 決策日期：2026-11-20　決策者：子平
> 這份文件是決策的正本。討論過程在 `C:\D\FAQ_web\memory_review\討論清單_線上知識庫MCP.md`。
> 修改決策時請更新這份文件，並在「變更紀錄」加一行。

## 1. 目標

- 同事接上一個網址就能使用 EHRMS 知識庫，不用下載 plugin。
- 每次使用都在伺服器端留下紀錄。
- 先累積資料；驗證與整理之後交給「知識庫清理 agent」。
- 同事的 AI 只能新增記憶與提出疑慮，不能改掉或取代既有知識。

## 2. 架構決策

| # | 項目 | 決定 |
|---|---|---|
| 1 | 主機 | 另找一台固定主機（不放 FAQ_web 那台）。條件：常開、連得到知識庫資料庫、同事經內網連得到、可裝 Python。記憶體預留給之後的 embedding 模型 |
| 2 | 身分 | 現階段以 Jira 名單確認：第一次使用時確認使用者信箱，比對 Jira 名單；之後以電腦位址記住，位址變了再確認一次。未確認前工具不提供服務。之後改接公司帳號登入 |
| 3 | 服務切分 | 知識庫、DB、Jira 三個 MCP 各自獨立。DB MCP（含 `mssql_query`）目前另有部署，之後再整合。讀本機程式碼、開分支、客製批次建檔、查客戶端資料庫留在本機 plugin |
| 4 | skill | 仍在 EHRMS_PLUGIN 撰寫。線上版用「列出 skill／取得 skill」兩個工具按需提供，常駐說明只放索引。顧問必須的是 crisis-triage（不含追碼），建議附 crisis-connection-email。需要本機程式碼或腳本的 skill 留在 plugin |
| 5 | 資料庫帳號 | 先沿用現有帳號。之後請 DBA 另開「知識庫專用」與「清理用」帳號（授權語法在 `hrms_kb_online.sql` 最下面） |
| 6 | 使用紀錄 | 一次工具呼叫一筆，只新增不修改。欄位見第 4 節 |
| 7 | 單號關聯 | 對話中有提到 Jira 單才關聯，沒提到留空 |
| 8 | 寫入 | 團隊記憶用 `remember`；FAQ／SOP／Task／公告用 `kb_contribute`（2026-11-21 改，見決策 16；原本是「線上版只寫團隊記憶」）。Jira 結案紀錄由既有排程（jira-auto-log）負責，線上版不寫 |
| 9 | 取代與衝突 | 線上版不提供取代（supersedes）與強制寫入（force），取代只由人或其他 agent 執行。`remember` 撞到「高度相似但結論不同」時：新內容完整存成候選（不進正式記憶、不會被查到），自動幫舊記憶加上疑慮標記並指向候選，回覆 AI「已記錄為待確認」 |
| 10 | 疑慮標記 | 新增工具：AI 發現某筆知識可能有錯時標記（哪一筆、理由、依據）。只新增不修改；記憶與 Wiki 都能標。查詢結果要顯示有疑慮與理由。只有人或清理 agent 能解除 |
| 11 | 用途標記 | `remember` 加用途標記：可回覆客戶／處理步驟／資料修正語法／一般知識。存在 `HRMS_MEMORY.META`。修資料的 SQL 也寫進記憶即可（標記為資料修正語法時沿用 `task_log` 的 SQL 檢查並加註「僅供參考」——這點是實作時的理解，子平尚未明確確認） |
| 12 | 查詢工具 | 合併成「搜尋」（一次查全部來源，回傳類型、編號、摘要、是否有疑慮）與「取全文」。內部仍呼叫現有查詢程式。本機 plugin 的 9 個查詢工具不動 |
| 13 | Jira MCP | 先獨立一個，暫用子平的帳號，留言功能保留。等大家確定使用後改成各自的 token 與使用者名稱 |
| 14 | FAQ_web | 資料本來就與其他來源連動，不另外處理。AI 助理的對話紀錄之後接上記憶儲存 |
| 15 | RAG | 之後加入，接在「搜尋」後面，工具介面不變。關鍵字與語意一起用。embedding 傾向用開源模型、放內部主機（Claude 不提供 embedding 模型）。回答用的 LLM 暫定 Claude API key 或 Claude CLI，之後可能轉內部 LLM。伺服器只負責找資料，不負責回答 |

| 16 | 從對話窗填寫知識 | 顧問多半用 Claude Code 而不是 FAQ_web，所以要保留從對話窗新增或更新 FAQ／SOP／Task／公告的入口（`kb_contribute`）。**寫入就立刻生效，不設審核關卡**（審核者只有子平一人，設關卡會卡住；版更公告又有時效性）。改用三件事把關：① 記錄填寫人，有問題直接問填寫人；② 權重較低，標示「未審核」並排在人工整理的內容之後；③ 任何人有意見用 `kb_flag` 提出疑慮，之後由人或清理 agent 審核。更新既有內容時**不覆蓋**：新增一筆更新版，舊的保留並自動標示「已有更新版本」 |

## 3. 清理 agent 要回答的問題

收資料時以這五題為準，確保之後答得出來：

1. 這筆知識是不是重複的？
2. 有沒有人質疑？質疑成不成立？
3. 有沒有被用到？
4. 對不對？（對照來源單號的結案紀錄或程式碼）
5. 該不該升級成 FAQ／SOP／Task？

已知缺口：只知道「被查到」，不知道「被採用」。先不補，跑起來不夠再加。

## 4. 資料表

建表語法：`hrms_kb_online.sql`。資料表已建立（見第 6 節步驟 1），但伺服器還沒有對它們寫入過；不修改任何既有資料表。

| 表 | 用途 |
|---|---|
| `HRMS_KB_USAGE_LOG` | 使用紀錄：時間、使用者、身分來源、電腦位址、連線編號與序號、來源（真人／排程／網站）、用戶端、工具名稱、參數、查詢文字、單號、回傳的知識編號與分數（不存全文）、是否查無資料、寫入結果與指向、耗時、錯誤、伺服器版本、檢索方式版本 |
| `HRMS_KB_CANDIDATE` | 候選：與既有記憶結論不同的新內容。處理結果欄位只由人或清理 agent 更新 |
| `HRMS_KB_FLAG` | 疑慮標記。處理結果欄位只由人或清理 agent 更新 |
| `HRMS_KB_USER_BINDING` | 電腦位址對應使用者。同一位址以最新一列為準。接上公司帳號登入後不再需要 |

資料表建好之前，伺服器可以用 `KB_STORE=sqlite` 先在本機累積，欄位名稱相同，之後可整批搬入。

## 5. 程式結構

```
servers/KB_ONLINE/
  SPEC.md              本文件
  hrms_kb_online.sql   建表語法
  run_server.py        啟動入口
  kb_online/
    config.py          設定（環境變數）
    store.py           資料層：MssqlStore／SqliteStore
    identity.py        身分確認（Jira 名單＋電腦位址）
    usage_log.py       使用紀錄與斷線補送
    dispatch.py        工具的單一入口：身分把關 → 執行 → 記錄
    tools_core.py      confirm_identity、kb_info
    tools_query.py     kb_search、kb_get
    tools_write.py     remember、kb_flag
    tools_contribute.py  kb_contribute
    tools_skill.py     skill_list、skill_get、常駐說明的 skill 索引
  online_skills.json   線上版提供哪些 skill
    server.py          HTTP 連線層
tests/test_kb_online.py
```

### 查詢工具（`tools_query.py`）

| 工具 | 行為 |
|---|---|
| `kb_search(query, sources?, top_k?, jira_key?, customer_code?, version_bulletins?)` | 一次查 Wiki、團隊記憶、FAQ、SOP、Task、Jira 結案紀錄、公告；每個來源各回傳前幾筆（預設 3），分區顯示、不硬排成一條。回傳摘要與編號（如 `[faq#48]`），不含全文與 SQL。`version_bulletins=true` 另外整批列出版更公告。某個來源查詢失敗時會明講，不當成查無資料 |
| `kb_get(type, id, section?, customer_code?)` | 讀單筆全文。Wiki 可指定規則編號、章節名、上半／下半／全文；Jira 可用紀錄編號或單號；`type=custom` 查某客戶的客製（取代 `custom_lookup`） |

- 評分與資料讀取沿用 `servers/MEMORY` 的程式，沒有修改那邊的任何檔案；本機 plugin 的 9 個查詢工具不受影響。
- **不回寫 `HIT_COUNT`**：本機 `recall` 每次命中會把引用次數加一，線上版不做，引用次數改由使用紀錄計算。
- 待處理的疑慮標記會顯示在搜尋結果與全文上（標記的寫入在步驟 4）。
- 檢索品質與本機版相同（關鍵字與字詞比對），這一步沒有改善檢索本身；之後用 `shadow_set.db` 量測。

### 寫入工具（`tools_write.py`）

| 工具 | 行為 |
|---|---|
| `remember(kind, purpose, topic, content, keywords?, func_path?, entry_path?, entry_method?, ref_key?)` | 寫入 HRMS_MEMORY。記錄者是伺服器確認過的使用者（如 `amy.lin(AI)`），`SOURCE=kb-online`，用途存在 `META.purpose`。沒有 `supersedes` 與 `force` 參數，帶了也無效 |
| `kb_flag(type, id, reason, evidence, jira_key?)` | 對既有知識提出疑慮。會先確認目標存在；理由與依據都必填。Wiki 可標到某條規則（`CARD#R7`）。不修改原內容 |

`remember` 寫入前的判斷（只在同一型別內比）：

| 情況 | 判斷方式 | 結果 |
|---|---|---|
| 沒有相似的既有記憶 | 相似度（`memory_core._dup_sim`）低於 0.75 | 新增正式記憶 |
| 幾乎相同 | 相似度 ≥ 0.75，且**只比內容本身**的字詞重疊 ≥ 0.90（Engineer 還要入口檔名相同） | 不新增，回覆既有編號 |
| 相似但不是同一個說法 | 相似度 ≥ 0.75 但不符合上一列 | 新內容完整存成候選，並自動幫被撞到的記憶加疑慮標記 |

- 字面比對分不出兩筆的結論是否相反，所以門檻設得保守：寧可多存成候選，不要當成重複丟掉。本機版的做法是 System 型一律當重複，會把相反的結論丟掉，線上版不沿用。
- 0.75 與 0.90 兩個門檻是依少量例子訂的，**沒有用真實資料校準過**。上線後看使用紀錄裡 `candidate` 與 `duplicate` 的比例再調整。
- 用途為 `data_fix` 時，內容裡 ```` ```sql ```` 區塊會用 `task_log` 的同一套檢查；讀取時固定加註「僅供參考」。
- **沒有檢查內容是否含客戶資料**，目前只靠工具說明要求 AI 不要寫。
- 為了讓記錄者是伺服器確認的使用者，`servers/MEMORY/memory_db.py` 的 `insert_memory` 多了一個選填參數 `created_by`；不給時行為與原本相同。這是線上版對 MEMORY 目錄唯一的修改。

### 填寫工具（`tools_contribute.py`）

`kb_contribute(type, title, content, module?, category?, syntax?, notes?, keywords?, description?, updates?, reason?, basis?, jira_key?)`

| 類型 | 寫入的表 | 檢查 |
|---|---|---|
| `faq` | HRMS_FAQ | 模組須為既有值 |
| `sop` | HRMS_FAQ_SOP | 模組須為既有值；內文須依 SOP 模板 |
| `task` | HRMS_TASK | 模組、分類須為既有值；SQL 用 `task_log` 的同一套檢查 |
| `bulletin` | HRMS_FAQ_BULLETIN | 公告類型須為既有值（版更公告用「版本更新」等） |

- 寫入者記為「使用者(AI)」，內文尾端加註「線上填寫；填寫人：某某」與依據。
- **新增**：同一張表有高度相似的內容時不新增，並告知可以帶 `updates` 改成更新那一筆，所以不會卡住。
- **更新**（帶 `updates`）：新增一筆更新版（內文註明「更新自 faq#48」），舊的不動，並在舊的那筆加上疑慮標記「已有更新版本 faq#新編號（填寫人 某某）」。
- 查詢時：經工具寫入的資料顯示「填寫人 某某・未審核」，排序分數乘以 0.9；整批列出版更公告時會另外列出哪幾則是線上填寫的。
- 內容必須來自使用者提供的資料，這點只靠工具說明要求，伺服器無法驗證。
- 與本機 `faq_log`／`sop_log`／`task_log` 的差別：不要求依據必填、Task 不要求先有 Jira 結案紀錄、可以更新人工整理的內容（以新增更新版的方式）、多了公告。
- 為了顯示公告的填寫人，`servers/MEMORY/refdata_db.py` 讀公告時多讀了寫入者欄位。

### skill 與常駐說明（`tools_skill.py`、`online_skills.json`）

- skill 只有一份來源：`plugins/ehrms-tools/skills/<名稱>/SKILL.md`。線上版要提供哪些，由 `online_skills.json` 決定。
- **新增一個線上 skill**：在 `skills/` 加資料夾，再在 `online_skills.json` 加一筆（`name`、`when`、選填的 `note`），重啟伺服器。同事不用做任何事。
- `skill_get` 回傳時會在最前面加一段「本機工具 → 線上工具」對照表，以及該 skill 的線上補充說明（`note`），再接原本的流程全文。這樣 SKILL.md 不用為了線上版另外改寫。
- 需要讀本機程式碼或執行腳本的 skill（bug-fix、debug、custom-sa-analyze 等）不放上來，留在 plugin。
- 目前只提供 `crisis-triage`。`crisis-connection-email` 還在主工作目錄未提交，合併後在清單加一筆即可。
- 常駐說明（連線時自動載入，約 1,200 字）內容：身分確認、查詢方式與依需求的重點、來源優先順序、**每次處理完都要判斷是否寫回**（沒寫要說明原因）、限制、skill 索引。
- 「強制沉澱」目前靠常駐說明要求 AI 每次判斷並說明；伺服器端還沒有強制機制。使用紀錄可以算出「有查詢但沒有寫入」的連線，之後可據此做補沉澱。
- 沒有做 MCP 的提示範本（prompts）；只用工具提供 skill，不依賴各用戶端對 skill 的支援。

所有工具都經過 `Dispatcher.call()`，身分檢查與使用紀錄只寫在這一處。新增工具用 `@registry.tool(...)` 註冊，回傳 `ToolResult`（含回傳的知識編號、寫入結果等），紀錄會自動產生。

### 啟動與設定

```
py -3 servers/KB_ONLINE/run_server.py
```

用戶端連線網址：`http://<主機>:<KB_PORT>/mcp`　健康檢查：`GET /health`

背景啟動與停止（Windows）：

```
.\servers\KB_ONLINE\start-kb-online.ps1          預設連接埠 8780，只接受本機連線
.\servers\KB_ONLINE\start-kb-online.ps1 -Stop
```

接到 Claude Code：`claude mcp add --transport http ehrms-kb http://<主機>:8780/mcp`（加完要開新對話才會載入）。

**目前的部署（2026-11-21）**：先跑在子平的電腦上試用，連接埠 8780（8765 被其他程式佔用），只接受本機連線，
資料存放用資料庫（`KB_STORE=mssql`，啟動腳本的預設值；紀錄檔與斷線時的暫存檔在 `~/.ehrms-tools/kb-online/`），
已加到 `C:\D\FAQ_web` 專案的 Claude Code。沒有設定開機自動啟動，
重開機後要再執行一次啟動腳本。正式主機到位後再搬過去。

| 環境變數 | 預設 | 說明 |
|---|---|---|
| `KB_HOST`／`KB_PORT` | `127.0.0.1`／`8765` | 部署到主機時 `KB_HOST` 改成 `0.0.0.0` |
| `KB_STORE` | `sqlite` | `mssql`＝正式資料表；`sqlite`＝本機檔案 |
| `KB_DATA_DIR` | `~/.ehrms-tools/kb-online` | sqlite 檔與使用紀錄暫存檔的位置 |
| `KB_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*` | 允許的 Host 標頭；部署後要加上主機名稱或 IP |
| `KB_TRUST_FORWARDED` | `0` | 前面有反向代理時設 `1`，改用 `X-Forwarded-For` 當電腦位址 |
| `KB_EMAIL_DOMAINS` | 空白 | 限定可確認的信箱網域，逗號分隔 |
| `KB_ENV_FILE` | `~/.ehrms-tools/.env` | 從檔案補環境變數 |
| `JIRA_BASE_URL`／`JIRA_EMAIL`／`JIRA_API_TOKEN` | — | 比對 Jira 名單用 |
| `MSSQL_*` | — | `KB_STORE=mssql` 時必填 |

## 6. 實作計畫與進度

| 步驟 | 內容 | 狀態 |
|---|---|---|
| 1 | 建表語法 | 完成。四張表與一個 View 於 2026-11-20 18:28 建立在 EHRMS-LAB-DB／EHRDB_MULTI_IE；2026-11-21 執行補丁，`CK_HRMSKBLOG_IDSRC` 加上 `none`。伺服器已切換成寫入資料庫（`KB_STORE=mssql`） |
| 2 | 骨架：HTTP 伺服器、身分確認、使用紀錄與斷線補送 | 完成（2026-11-20），有單元測試與實際連線測試 |
| 3 | 「搜尋」「取全文」兩個查詢工具（`kb_search`、`kb_get`） | 完成（2026-11-21），有單元測試，並對真實知識庫資料庫做過唯讀連線測試 |
| 4 | `remember`（用途標記、衝突存候選並自動加疑慮標記）與疑慮標記工具 `kb_flag` | 完成（2026-11-21），有單元測試與連線測試；連線測試時 HRMS_MEMORY 的寫入以替身取代，沒有寫進真實資料表 |
| 5 | 「列出 skill／取得 skill」（`skill_list`、`skill_get`）與常駐說明 | 完成（2026-11-21），有單元測試與連線測試。尚未讓真正的 AI 用戶端照著跑過一張單 |
| 6 | 部署到主機，先給少數同事試用 | 未開始，等主機 |
| 7 | 之後：Jira MCP 上線、AI 助理對話接記憶、RAG、清理 agent | 未開始 |

## 7. 已知限制與尚未驗證

- **信箱是使用者自己說的**：伺服器只能確認「Jira 名單上有這個人」，不能確認是本人。接上公司帳號登入才會解決。
- **以電腦位址認人**：多人共用同一個對外位址（例如經 VPN）會被當成同一個人。目前前提是同事都在內網。
- **Jira 名單比對尚未對真正的 Jira 測試過**：單元測試用的是假名單。Jira 若因隱私設定不回傳信箱，程式只在搜尋結果剛好一位時接受。
- **資料庫寫入的驗證程度不同**（2026-11-21）：使用紀錄已經由實際呼叫寫進 `HRMS_KB_USAGE_LOG`（含被擋下的呼叫）；使用者對應、候選、疑慮標記只在交易內執行寫入語法後復原，確認語法與欄位正確，還沒有真正留下過資料。
- **`remember` 還沒有真的寫進過 HRMS_MEMORY**：測試都用替身攔下寫入。第一次對真實資料表寫入時要確認記錄者與 `META` 內容正確。
- **重複與候選的門檻未校準**，也沒有擋客戶資料（見寫入工具一節）。
- **來源（ORIGIN）目前一律是真人對話**：排程與網站接上時要加上辨識方式。
- **Codex 是否支援這種 HTTP 連線與之後的登入流程，尚未查證**。
- **還沒有用真正的 AI 用戶端（Claude Code 等）接上來跑過**：目前的連線測試是用程式直接呼叫工具。AI 會不會照常駐說明先取 skill、處理完會不會做寫回判斷，要實際接上才知道。

## 8. 待補事項（不擋實作）

- 主機由子平安排。
- 公司帳號是哪一種登入系統。
- Jira 共用帳號期間，留言是否自動標註實際使用者。
- 資料修正語法寫進記憶時是否要做 SQL 檢查（見決策 11）。
- 請 DBA 建表、之後另開專用帳號。

## 變更紀錄

| 日期 | 內容 |
|---|---|
| 2026-11-20 | 初版：11 項討論定案；完成骨架 |
| 2026-11-21 | 完成查詢工具 `kb_search`、`kb_get`；線上版不回寫 `HIT_COUNT`；客製查詢併入 `kb_get(type=custom)` |
| 2026-11-21 | 新增 `kb_contribute`（決策 16）：從對話窗新增或更新 FAQ／SOP／Task／公告，立刻生效、記錄填寫人、權重較低、不覆蓋舊內容。決策 8 隨之修改 |
| 2026-11-21 | 執行 `IDENTITY_SOURCE` 補丁；伺服器切換成寫入資料庫，啟動腳本預設改為 `mssql`。使用紀錄表的第 1、2 筆是切換後的驗證呼叫（使用者 unknown、用戶端 `mcp 0.1.0`） |
| 2026-11-21 | 在子平電腦試用部署（連接埠 8780）並接到 Claude Code；發現資料表已建立，建表語法補上 `IDENTITY_SOURCE` 的 `none` 補丁；FAQ_web 知識庫架構頁加入線上知識庫 |
| 2026-11-21 | 完成 `skill_list`、`skill_get` 與常駐說明；線上 skill 由 `online_skills.json` 指定，取得時自動加上工具對照表 |
| 2026-11-21 | 完成寫入工具 `remember`、`kb_flag`；重複判斷改成只比內容，其餘相似的存成候選；`memory_db.insert_memory` 加選填參數 `created_by` |
