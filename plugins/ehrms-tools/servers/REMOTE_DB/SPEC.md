# ehrms-custom-mcp 開發規格

MCP 名稱：`ehrms-custom-mcp`（程式目錄 `servers/REMOTE_DB`）。

讓 AI 在維運時直接查詢客戶資料庫，省去「執行 SQL → 複製結果 → 貼給 AI」的步驟。每次查詢都須由工程師核准 SELECT 語法。

> 狀態：**第一階段 MVP**（2026-10-29）。明文傳輸、無驗證，**只供內網測試**，不可用於客戶環境。

## 背景與目標

- 現況：工程師用 Splashtop／AnyDesk 連到客戶機器，在 SSMS 執行 AI 給的 SQL，再把結果貼回 AI；一個問題常要來回多輪
- 目標：AI 產生 SELECT → 工程師在 Claude Code 核准 → 直接在客戶 DB 執行 → 結果回到 AI
- 連線模式比照 Splashtop：客戶端與工程師端都**只主動往外連**到中繼伺服器，客戶不需開 inbound port。原則是「Splashtop 能連，這個工具就要能連」

## 架構

```
工程師電腦                        中繼機器                  客戶機器
[Claude Code]
   │ MCP（stdio）
[mcp_server.py] ──WebSocket──► [relay.py] ◄──WebSocket── [agent.py] ──► 客戶 MSSQL
                                 預設 port 8770            顯示配對碼
```

| 元件 | 放置位置 | 職責 |
|---|---|---|
| `relay.py` | 中繼機器（第一階段：內網機器） | 依配對碼配對 agent 與 MCP 端並轉送訊息，不存資料 |
| `agent_gui.py` → `EHRMS_Connect.exe` | 客戶機器 | 圖形介面：輸入 DB 帳密、產生配對碼、顯示查詢紀錄 |
| `agent.py` | 客戶機器 | 核心邏輯（SELECT 檢查、執行、客戶資訊、與 relay 通訊）；也可直接以命令列執行 |
| `mcp_server.py` | 工程師電腦 | 提供 AI 工具 `connect`、`query` |

## 使用流程

1. 工程師用 Splashtop 在客戶機器執行 `EHRMS_Connect.exe`，輸入資料庫名稱、帳號、密碼後登入。連上 DB 與 relay 後才顯示 6 碼數字配對碼
2. 工程師把配對碼告訴 AI，AI 呼叫 `connect(code, expected_sno)`，取得客戶資訊
3. **確認客戶**：AI 把資料庫名稱、SQL Server、主機、HRMS_COMPANY 公司清單列給工程師，確認是正確客戶後才開始查詢
4. AI 呼叫 `query(sql, reason)`，Claude Code 跳出核准提示並顯示完整 SQL，**由工程師審核**
5. 核准後經 relay 送到 agent 執行，結果以 Markdown 表格回到 AI

### 客戶資訊確認

客戶資料庫名稱通常為客戶統編。agent 連上 DB 後先讀取：

| 項目 | 來源 |
|---|---|
| 資料庫名稱 | `DB_NAME()` |
| SQL Server、版本 | `@@SERVERNAME`、`SERVERPROPERTY('ProductVersion')` |
| 主機 | `HOST_NAME()` |
| 公司清單 | `HRMS_COMPANY` 的 `COMPANY_CODE`、`COMPANY_CNAME`、`COMPANY_SNO`（統一編號）、`COMPANY_STATUS`（0 開啟／1 關閉） |

`connect` 的檢查：

- 資料庫名稱不是 8 碼數字時，提示要特別確認
- 有給 `expected_sno`（例如 Jira 單上的客戶統編）時，與資料庫名稱及所有公司統編比對；都不符就警告可能連錯客戶
- 讀取 HRMS_COMPANY 失敗時（例如非 EHRMS 資料庫），仍可連線，但會附上錯誤訊息
- 每次 `query` 都會比對資料庫名稱是否與 connect 時相同，不同就要求重新 connect；結果開頭標示客戶資料庫

## 通訊協定（JSON over WebSocket）

agent 保持長連線；MCP 端每次查詢開一條新連線。

| 方向 | 訊息 |
|---|---|
| agent → relay（連線後第一則） | `{"role": "agent", "code": "ABC123", "info": {客戶資訊}}` |
| MCP → relay（連線後第一則） | `{"role": "client", "code": "ABC123"}`；connect 時另帶 `"probe": true`，只取客戶資訊 |
| relay → MCP | 配對成功 `{"ok": true, "info": {...}}`（probe 到此結束）；找不到 agent `{"error": "..."}` 後關閉 |
| MCP → relay → agent | `{"id": "<uuid>", "sql": "SELECT ..."}` |
| agent → relay → MCP | 成功 `{"id", "columns": [...], "rows": [[...]], "truncated": bool}`；失敗 `{"id", "error": "..."}` |

所有值在 agent 端轉成字串，NULL 保留為 `null`。配對碼為 6 碼數字（`secrets` 產生），畫面以「123 456」分段顯示。

## 客戶端圖形介面（EHRMS_Connect.exe）

| 欄位 | 預設 | 說明 |
|---|---|---|
| 帳號 | `ehrms` | 可修改 |
| 密碼 | 空白 | 只存在記憶體；登入失敗或中斷連線後清空 |
| DB 主機 | `.`（本機） | 記在 exe 同目錄的 `agent.ini` |
| 資料庫名稱 | 空白 | 下拉選單，可按「偵測」或留空登入自動偵測；也可直接輸入。通常為客戶統編 |
| 中繼伺服器 | `wss://ehrms-relay.shares.zrok.io` | 記在 `agent.ini` |

- 按「登入」或 Enter：在背景連 DB、讀取客戶資訊，再連 relay；全部成功才切換到配對碼畫面
- 資料庫偵測（`agent.detect_databases`）：以帳密連 `master`，列出 `sys.databases` 中可存取（`HAS_DBACCESS`）、線上的使用者資料庫，並檢查各庫是否有 `dbo.HRMS_COMPANY`、取第一筆 `COMPANY_CNAME`。排序為 EHRMS 庫優先、8 碼統編名稱優先；選項顯示「統編　公司名」，非 EHRMS 庫標「（非 EHRMS）」
  - 留空登入時：只有一個 EHRMS 庫就直接登入；多個時列出讓使用者確認；沒有 EHRMS 庫時請使用者選擇或手動輸入
  - 客戶名稱一律從客戶自己的 `HRMS_COMPANY` 讀取，**不可**把公司內的客戶名單（如 Custom_compare）打包進 exe，以免客戶機器上出現其他客戶資料
- wss 連線另外載入內附的 certifi 根憑證：舊版 Windows 的系統根憑證過舊，連 zrok 會出現 `CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate`（甲山林實測遇到）
- 客戶機器 DNS 解析不到中繼網址（`[Errno 11002] getaddrinfo failed`，石梅化學 45049886 實測遇到）時，自動改連 `agent.ini` 的 `relay_ips`（預設 zrok 公開節點 `52.223.10.144,35.71.189.214`，畫面不顯示），TLS 驗證與 Host 仍用原網址，不必改客戶 hosts 檔；連上的 IP 在重連時優先使用。需要經 Proxy 才能上網的機器仍無法連線。zrok 節點 IP 若變更，改 `agent.ini` 即可，不必重新打包
- 配對碼畫面顯示配對碼（右側「複製」按鈕，複製不含空白的 6 碼）、資料庫、表單資料庫、公司清單，以及每筆收到的查詢（含目標資料庫）與結果筆數
- 「中斷連線」或 relay 斷線：回到登入畫面並顯示原因
- 密碼以 `PWD={...}` 包住，含 `;`、`}` 等特殊字元也能正確傳遞

打包（產出 `dist/EHRMS_Connect.exe`，約 9 MB；build、dist、spec 不進版控）：

**一定要用 Python 3.8 打包。** 客戶機器常是 Windows Server 2012／2008 R2 等舊版系統，Python 3.9 以後不支援；用 3.12 打包的 exe 在客戶機器上會出現 `Error loading Python DLL ... python312.dll / LoadLibrary: 找不到指定的程序`。PyInstaller 也固定使用 5.13.2。

```bash
py -3.8 -m pip install "pyinstaller==5.13.2" "websockets<14" pyodbc certifi
py -3.8 -m PyInstaller --noconfirm --onefile --noconsole --name EHRMS_Connect --icon icon.ico --add-data "icon.ico;." agent_gui.py
```

`--icon` 設定 exe 檔案圖示；`--add-data` 把同一個 `icon.ico` 放進 exe，執行時由 `sys._MEIPASS` 讀出當作視窗與工作列圖示。`icon.ico`（16～256 多尺寸、透明背景）與預覽用 `icon.png` 由兔子原圖轉成，放在本目錄。

因此 `agent.py`、`agent_gui.py` 必須維持 Python 3.8 相容（例如不可用 `asyncio.to_thread`、`str.removeprefix`、`match`）。relay 與 MCP 端不受此限制。

單檔 exe 每次啟動要先解壓縮，開啟視窗約需數秒。

## MCP 工具

| 工具 | 參數 | 說明 |
|---|---|---|
| `connect` | `code`、`expected_sno`（選填） | 以配對碼連線並回傳客戶資訊供確認；配對碼與客戶資訊存在 MCP 程序記憶體中 |
| `query` | `sql`、`reason`、`database`（選填） | 執行單一 SELECT。`reason` 讓工程師核准時判斷用途。`database`：`main`（預設，客戶主資料庫＝統編）或 `wf`（表單資料庫＝統編_WF）。逾時 60 秒 |

### 表單資料庫（統編_WF）

- agent 登入主庫後查 `sys.databases` 是否有 `DB_NAME() + '_WF'`（線上且可存取），結果放在客戶資訊 `wf_database`，connect 時列出
- 查詢訊息帶 `"db": "main" | "wf"`；agent 只接受這兩個值，各自用主庫或 _WF 的連線字串執行，無法切到其他客戶的資料庫
- 偵測資料庫時，主庫存在的 `統編_WF` 不另列選項

### 連線維持、重連與時間上限

- **閒置上限**：**1 小時內沒有任何查詢**就自動中斷（`agent_gui.IDLE_LIMIT`），每收到一筆查詢重新計時（`agent.serve` 的 `on_query`）。畫面顯示閒置倒數；到時回到登入畫面並顯示原因
- 1 小時內只要持續有查詢就不會斷（經 zrok 實測閒置 5 分鐘後查詢正常）
- **keepalive**：agent 與 relay 每 20 秒送一次 ping，**60 秒**內沒有回應才判定斷線。websockets 預設只等 20 秒，甲山林實測時連上約 20 秒就斷線，因此兩端都放寬到 60 秒
- **自動重連**：連上後中途斷線（網路、zrok 或 relay 重啟），agent 每 5 秒以同一配對碼重連，MCP 端不需重新 connect；斷線原因與重連都顯示在畫面的查詢紀錄。第一次就連不上 relay 則直接顯示錯誤
- relay 重新連上同一配對碼時以新連線取代舊連線

### 客戶端警示與稽核紀錄

- 登入與配對碼畫面頂端以紅字顯示：「此工具僅提供查詢語法，不會更動資料庫資料，並會記錄查詢語法以供稽核」
- 目前查詢語法只顯示在客戶端畫面，**不寫本機檔案**；集中寫入資料庫的稽核表待與主管確認後實作（階段 2）。在那之前，紅字中「會記錄查詢語法以供稽核」尚未有持久化的紀錄

## agent 端限制（不依賴人工審核的最後防線）

| 項目 | 規則 |
|---|---|
| 只讀判斷（主要） | `SET SHOWPLAN_XML ON` 下送出整個批次，**只編譯不執行**；SQL Server 回報的每個 `StatementType` 都必須是 `SELECT` 或 `SELECT WITHOUT QUERY`，其餘（含不認得的類型）整批拒絕；編譯失敗也拒絕 |
| 資料庫範圍 | 執行計畫中每個物件的 `Database` 只能是本客戶主庫或其 _WF；`master`（含 `sys.databases`）、其他客戶的庫一律拒絕 |
| 額外禁止 | SHOWPLAN 仍判為 SELECT 的 `openrowset opendatasource openquery openxml`（讀伺服器檔案／外部伺服器）與鎖定 hint `tablock tablockx xlock updlock holdlock paglock serializable repeatableread`；直接比對原始語法，出現在字串中也擋 |
| 交易保險 | 連線關閉自動 commit，執行後一律 `rollback` 再關閉 |
| 筆數 | 最多 500 筆，超過標示 `truncated` |
| 逾時 | 連線 10 秒、查詢 30 秒 |
| 驅動 | `{SQL Server}`（Windows 內建，免安裝，支援 MSSQL 2008；已實測可讀取 SHOWPLAN 輸出） |
| 帳密 | 只在 agent 記憶體中，不經 relay、MCP 或 AI。命令列版帳號留空則使用 Windows 驗證 |
| 權限 | 帳號需有 SHOWPLAN 權限（db_owner 已有）；日後改用唯讀帳號時要 `GRANT SHOWPLAN` |

### 為什麼改用 SHOWPLAN（2026-10-29 安全檢查）

原本以正規表示式去除註解與字串後比對黑名單，在 LocalDB 實測可被繞過並**實際刪改資料**：

| 繞過方式 | 範例 |
|---|---|
| 字串內的 `--`、`/*` 被當成註解，藏住後面的語句 | `SELECT '--' AS a DELETE FROM t COMMIT` |
| `[...]`、`"..."` 識別字內的單引號誤導字串判斷 | `SELECT 1 AS [x'] DELETE FROM t COMMIT SELECT 1 AS [y']` |
| 黑名單漏網，且 T-SQL 多語句不需分號 | `... UPDATETEXT ... COMMIT`、`KILL` |

pyodbc 預設不自動 commit 只擋住沒有 `COMMIT` 的情況。改用 SHOWPLAN 後，以實際使用的 `{SQL Server}` 驅動＋Python 3.8 對 LocalDB 測 34 個案例（上述繞過、UPDATE／DELETE、DDL、GRANT、USE、EXEC、IF 分支、WAITFOR、SET、交易、SELECT INTO、跨庫、OPENROWSET、鎖定 hint 與正常查詢），全部符合預期且資料未被改動。LocalDB 為 SQL Server 2019，客戶端 2008 尚未實測；白名單只放行兩種 SELECT，類型名稱若有差異只會誤擋、不會誤放行。

### 仍未處理

- **relay 沒有驗證**：任何人知道 zrok 網址並猜中 6 碼配對碼，就能不經 MCP 與人工核准直接送查詢（SHOWPLAN 確保只能讀，但仍可讀取客戶資料）。需加上 MCP 端 token 與配對碼嘗試次數限制
- 最根本的防線仍是請客戶為本工具建立只有 `db_datareader`（＋`SHOWPLAN`）權限的帳號

## 安裝與測試（第一階段）

中繼機器：

```bash
py -m pip install websockets
py relay.py --port 8770
```

客戶機器（測試時用另一台內網電腦，連 Lab DB）：

```bash
py -m pip install websockets pyodbc
py agent.py --relay ws://中繼機器IP:8770
```

工程師電腦：

```bash
py -m pip install mcp websockets
claude mcp add ehrms-custom-mcp --scope user -e REMOTE_DB_RELAY=ws://中繼機器IP:8770 '--' py -3 -B C:\D\EHRMS_PLUGIN\plugins\ehrms-tools\servers\REMOTE_DB\mcp_server.py
```

在 PowerShell 執行時，`--` 要加引號，否則會被 PowerShell 吃掉。更換 relay 位址時，先 `claude mcp remove ehrms-custom-mcp -s user` 再重新 add。

強制每次查詢都要核准，在 `~/.claude/settings.json` 加入：

```json
{ "permissions": { "ask": ["mcp__ehrms-custom-mcp__query"] } }
```

第一階段刻意**不**加入 plugin 的 `mcp.claude.json`，避免所有插件使用者都載入尚在測試的工具。

## 暫時對外通道：zrok（測試用）

在還沒有公開中繼主機之前，用 zrok 把工程師電腦上的 relay 公開，讓客戶機器連得到：

```
客戶 exe ──wss://ehrms-relay.shares.zrok.io──► zrok ──► 工程師電腦 relay（127.0.0.1:8770）◄── MCP（ws://127.0.0.1:8770）
```

- relay 改用 **8770**：8765 是 Gmajor 卡拉 OK 後端使用的 port
- zrok 沿用 Gmajor 安裝的 `%LOCALAPPDATA%\Programs\zrok\zrok2.exe`（v2.0.7），帳號已 enable
- 固定名稱已建立（只需一次）：`zrok2.exe create name -n public ehrms-relay`
- 啟動 relay 與分享：`powershell -ExecutionPolicy Bypass -File start-relay.ps1`。分享上次沒有正常關閉而佔用名稱時，腳本會先刪除舊分享
- relay 只聽 127.0.0.1，外部連線一律經過 zrok
- 已實測：Python 3.12 與 3.8 的 agent 都能經 zrok wss 連上 relay、完成查詢

⚠ 限制：

- 目前沒有任何驗證，知道網址與 6 碼配對碼就能查詢；只適合短期測試
- zrok 公開分享會在 zrok 的公開節點解開 HTTPS，客戶的 SQL 與查詢結果會經過第三方伺服器。用於真實客戶資料前應取得主管或資安同意
- 客戶機器若根憑證太舊，wss 可能出現憑證錯誤

## 後續階段

| 階段 | 內容 | 前置條件 |
|---|---|---|
| 1 ✅ | MVP：relay／agent／MCP 跑通，agent 端 SELECT 檢查 | — |
| 2 | 查詢紀錄寫入 172.19.5.171（與 EHRMS-jira-mcp 同一資料庫，另開專用帳號） | 171 建表與 INSERT 權限 |
| 3 ✅ | 客戶端圖形介面並打包成單一 exe（`EHRMS_Connect.exe`） | — |
| 4 | 資安：WSS＋正式憑證、工程師端 token、配對碼時效與嘗試次數限制、只允許內網 IP 的 MCP 端、Proxy 支援、斷線重連 | 資安與網管同意 |
| 5 | relay 對外開放（公司 DMZ 對外 443 對應，或核准的對外通道服務），實際客戶試用 | 公開網域、憑證、網管設定 |
| 6 | MCP 加入 plugin 正式發佈 | 階段 4、5 完成 |

使用方式已寫成 `skills/ehrms-custom-mcp/SKILL.md`；`context.md` 與 `crisis-triage` 已加上「使用者提供配對碼時可改用 ehrms-custom-mcp」的例外，沒有配對碼時仍維持提供 SQL 請客戶端執行。
| 之後 | 主管／客戶端核准、敏感欄位遮罩 | 視需求 |

### 階段 2 紀錄表草案

```sql
CREATE TABLE HRMS_REMOTE_QUERY_LOG (
    LOG_ID       INT IDENTITY(1,1) PRIMARY KEY,
    REQUEST_ID   VARCHAR(36)    NOT NULL,
    PAIR_CODE    VARCHAR(10)    NOT NULL,
    JIRA_KEY     VARCHAR(30)    NULL,
    REQUESTER    VARCHAR(100)   NOT NULL,
    REASON       NVARCHAR(1000) NULL,
    SQL_TEXT     NVARCHAR(MAX)  NOT NULL,
    STATUS       VARCHAR(20)    NOT NULL,  -- EXECUTED / FAILED / TIMEOUT
    ROW_COUNT    INT            NULL,
    ERROR_MSG    NVARCHAR(1000) NULL,
    CREATE_TIME  DATETIME       NOT NULL DEFAULT GETDATE()
)
```

只記錄語法與筆數，**不存查詢結果**，避免紀錄表本身成為個資外洩點。

## 第一階段已知限制

- ws 明文傳輸，任何人知道配對碼與 relay 位址就能查詢
- relay 重啟後 agent 不會自動重連，需重新執行 agent
- `connect` 的配對碼只存在 MCP 程序中，Claude Code 重啟後需重新 connect
- 尚未實際連客戶或 Lab DB 驗證；目前以假資料完成 relay ↔ agent ↔ MCP 端對端測試
