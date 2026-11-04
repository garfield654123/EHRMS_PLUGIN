# ehrms-custom-mcp 三項防護規格

狀態：**規劃中，尚未實作**（2026-11-04）。上位規格：`../SPEC.md`。架構圖與連線說明：`architecture.html`、`connection.html`。

| 防護 | 目標 | 位置 |
|---|---|---|
| 一、員工資料保護 | 敏感欄位不離開客戶機器 | 客戶端 agent |
| 二、LOG 紀錄 | 每筆查詢可追查，.171 與客戶資料庫各一份 | MCP 端、客戶端 agent |
| 三、資料庫帳號控管 | 由資料庫本身限制只能讀 | 客戶資料庫 |

建議實作順序：防護二 → 防護一第 1 階段 → 防護三 → 防護一第 2 階段。

## 實測依據

2026-11-04 在 LocalDB（SQL Server 2019，ODBC Driver 17）的 tempdb 建探測表實測，探測物件已刪除。**尚未在 SQL Server 2008、2012 與 `{SQL Server}` 驅動上驗證，實作前要補測。**

### 執行計畫（SHOWPLAN_XML）

| 查詢寫法 | 計畫的 `ColumnReference` 抓得到敏感欄位 | 計畫頂層輸出能對應結果欄位 |
|---|---|---|
| 直接輸出、`SELECT *` | 可以 | 不一定，順序可能不同 |
| 別名 `EMPLOYEE_IDC_NO AS a` | 可以 | 不行，計畫沒有別名，順序也不同 |
| `SUBSTRING`、字串相接、`CASE`、`MAX` | 可以 | 不行，只顯示 `Expr1002` |
| `UNION ALL` | 可以 | 不行，只顯示 `Union1004` |
| `WHERE`、`JOIN`、`ORDER BY` | 可以 | 不適用，`ORDER BY` 還會多一個輸出欄 |
| View、子查詢、CTE | 可以，會展開到底層表 | 不行，別名已消失 |

結論：

- **偵測可靠**：上述寫法都抓得到查詢用了哪個表的哪個欄位。
- **對應不可靠**：無法從計畫得知結果的第幾欄來自哪個欄位，所以不能單靠計畫遮罩某一欄。
- **改別名可以騙過欄位名稱比對**：`SELECT EMPLOYEE_NO AS EMPLOYEE_IDC_NO, EMPLOYEE_IDC_NO AS EMPLOYEE_NO` 會讓身分證號出現在名為 `EMPLOYEE_NO` 的欄位。因為計畫不含別名，也無法從計畫分辨。

### 帳號權限

測試帳號：`db_datareader`＋`GRANT SHOWPLAN`＋LOG 表 `GRANT INSERT`＋敏感欄位 `DENY SELECT`。

| 動作 | 結果 |
|---|---|
| 查一般欄位 | 允許 |
| 查被 DENY 的欄位（直接、別名、`SELECT *`、`WHERE`） | 拒絕 |
| 對被 DENY 的欄位跑 SHOWPLAN | 拒絕 |
| **經同擁有者的 View 查被 DENY 的欄位** | **允許，資料會出去** |
| INSERT LOG 表 | 允許 |
| UPDATE／DELETE LOG 表 | 拒絕 |
| UPDATE 業務表、CREATE TABLE | 拒絕 |

View 能繞過是 SQL Server 的所有權鏈結行為，所以 **DENY 不能取代防護一的計畫偵測**。

## 防護一：員工資料保護

### 第 1 階段：偵測即拒絕，加上值樣式遮罩

與原本「遮罩該欄後回傳」的構想不同，因為實測顯示無法可靠對應欄位。

執行順序（`agent.py` 的 `run_select`）：

1. `verify_plan` 通過只讀與資料庫範圍檢查。
2. 從計畫取出所有帶 `Table` 屬性的 `ColumnReference`，比對敏感欄位清單。
3. 有命中就整筆拒絕，不執行。回傳命中的欄位名稱，讓 AI 改寫查詢。
4. 沒命中才執行。回傳前對每個字串值套用值樣式遮罩。

值樣式遮罩用來接住清單漏掉的欄位與自由文字：

| 類型 | 樣式 | 取代為 |
|---|---|---|
| 身分證號、居留證號 | `(?<![0-9A-Za-z])[A-Z][0-9A-Z]\d{8}(?![0-9A-Za-z])` | `[證號已遮罩]` |
| 手機 | `(?<!\d)09\d{2}-?\d{3}-?\d{3}(?!\d)` | `[電話已遮罩]` |
| Email | `[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+` | `[Email已遮罩]` |

不能用 `\b` 或 `\w`：Python 把中文也算成文字字元，「身分證A123456789號」會比對不到，Email 也會連前面的中文一起取代。上表樣式已用中文相鄰、員工編號 `A11748`、單號與表單編號測過。

值樣式遮罩抓不到姓名與文字描述的事由，所以自由文字欄位仍列入敏感清單。

### 敏感欄位清單

依欄位名稱比對，不限資料表，同名的客製表也會命中。清單放在 `sensitive_columns.json`，打包進 exe。`agent.ini` 只能新增，不能移除。

| 類別 | `HRMS_EMPLOYEE` 欄位 |
|---|---|
| 證號 | `EMPLOYEE_IDC_NO`、`EMPLOYEE_PASSPORT_NO`、`EMPLOYEE_ABIDE_NO`、`EMPLOYEE_WORKID_NO`、`EMPLOYEE_TAX_NO`、`EMPLOYEE_INSURANCE_NO` |
| 姓名 | `EMPLOYEE_CNAME`、`EMPLOYEE_LASTNAME`、`EMPLOYEE_FIRSTNAME`、`EMPLOYEE_PASSPORT_NAME`、`EMPLOYEE_URGENCY_CONTACT` |
| 個人屬性 | `EMPLOYEE_BIRTHDAY`、`EMPLOYEE_LIVE_AREA`、`EMPLOYEE_HEIGHT`、`EMPLOYEE_WEIGHT`、`EMPLOYEE_BLOOD`、`EMPLOYEE_MARRY_STATUS`、`EMPLOYEE_IS_OFFENSE`、`EMPLOYEE_FAMILY_TYPE`、`CRIPPLES_IDENTITY_CLASS_ID`、`CRIPPLES_IDENTITY_ID` |
| 電話、Email | `EMPLOYEE_OFFICE_TEL_1`、`EMPLOYEE_OFFICE_TEL_2`、`EMPLOYEE_EMAIL_1`、`EMPLOYEE_EMAIL_2`、`EMPLOYEE_PERSONAL_EMAIL`、`EMPLOYEE_LIVE_TEL`、`EMPLOYEE_CONTACT_TEL`、`EMPLOYEE_CONTACT_TEL_1`、`EMPLOYEE_URGENCY_TEL` |
| 地址 | `EMPLOYEE_LIVE_ADDRESS_AREA`、`EMPLOYEE_LIVE_ADDRESS`、`EMPLOYEE_LIVE_POSTCODE`、`EMPLOYEE_LIVE_EN_ADDRESS`、`EMPLOYEE_CONTACT_ADDRESS_AREA`、`EMPLOYEE_CONTACT_ADDRESS`、`EMPLOYEE_CONTACT_POSTCODE`、`EMPLOYEE_CONTACT_EN_ADDRESS`、`EMPLOYEE_URGENCY_ADDRESS_AREA`、`EMPLOYEE_URGENCY_ADDRESS`、`EMPLOYEE_URGENCY_POSTCODE`、`EMPLOYEE_URGENCY_EN_ADDRESS` |
| 圖檔路徑 | `EMPLOYEE_PICTURE_1` ～ `EMPLOYEE_PICTURE_9` |
| 薪資、職等 | `EMPLOYEE_TEST_SALARY`、`GRADE_ID`、`LEVEL_ID` |
| 自由文字 | `EMPLOYEE_REMARK` |

其他資料表已知要列入：`HRMS_ASK_LEAVE.ASK_LEAVE_MEMO`（請假備註）。

**清單還不完整。** 目前只逐欄檢視過 `HRMS_EMPLOYEE`。薪資、銀行帳號、眷屬、教育經歷與 `ZZ_` 客製表都要再盤點。

不列入，排查時需要：`EMPLOYEE_ID`、`EMPLOYEE_NO`、部門、職務、在職狀態、到離職日、班別與假勤欄位。`EMPLOYEE_CARD_NO` 待決定。

使用方式的改變：

- 用 `EMPLOYEE_NO` 或 `EMPLOYEE_ID` 找人，不能用姓名或身分證號。
- 工單只有姓名時，先請客戶提供員工編號。
- `SELECT *` 查 `HRMS_EMPLOYEE` 會被拒絕，要列出需要的欄位。

### 第 2 階段：安全形式才遮罩回傳（選做）

只有第 1 階段不夠用時才做。需要 SQL 解析器確認查詢沒有改名，建議先評估 `sqlglot` 是否支援 Python 3.8 與 T-SQL。

查詢用到敏感欄位時，必須完全符合下列條件才遮罩回傳，否則維持拒絕：

- 單一 SELECT，沒有 CTE、子查詢、`UNION`。
- `FROM` 只有基底資料表，計畫中的物件與 `FROM` 一致，沒有 View。
- 所有選取項目都是單純欄位或 `*`，**沒有任何別名**，敏感欄位不用於運算。
- `WHERE`、`JOIN`、`ORDER BY`、`GROUP BY` 沒有用到敏感欄位。

符合時，結果欄名就是來源欄名，可以依名稱遮罩：

| 類別 | 回傳 |
|---|---|
| 證號 | 只留後 2 碼：`********89` |
| 姓名 | 只留第一字：`王○○` |
| 其他 | `***` |

驗收必須包含前面的欄位互換案例。

### 限制

- 清單外的欄位不受第 1 步保護，只有值樣式遮罩。
- 非敏感欄位的查詢結果仍會經過 zrok 與 AI。
- 自由文字以外的業務資料（例如請假日期）不遮罩。

## 防護二：LOG 紀錄

每筆查詢都寫入 `HRMS_CUSTOM_MCP_LOG`。只記語法與筆數，**不存查詢結果**。使用狀況與成效從這張表分析，不另建評估表，也不要求工程師在對話結束時填寫：工程師可能得到結論就關掉對話，紀錄要在查詢當下就寫入。

| 項目 | .171（172.19.5.171） | 客戶資料庫 |
|---|---|---|
| 狀態 | **資料表已建立（2026-11-04 確認 27 欄、4 索引、0 筆），程式尚未寫入** | 未建立，待決定是否要做 |
| 位置 | `EHRMS-LAB-DB`／`EHRDB_MULTI_IE` | 登入時選的資料庫，不隨切換改變 |
| 寫入者 | `mcp_server.py` | agent |
| 用途 | 我方集中稽核、分析使用狀況 | 客戶自行查核；繞過 MCP 直連 relay 的查詢也會留下 |
| 能否竄改 | 專用帳號只能 INSERT。現有 MCP 帳號是 sysadmin，DENY 對它無效，所以要另開帳號 | 客戶 db_owner 可以改，只能當參考 |

### 資料表

定義見 `../hrms_custom_mcp_log.sql`。只新增不更新，同一筆查詢有兩列：`RECEIVED` 與結果，以 `REQUEST_ID` 串接；同一次連線以 `SESSION_ID` 串接。

| 分類 | 欄位 |
|---|---|
| 事件 | `ID`、`SESSION_ID`、`REQUEST_ID`、`EVENT_TYPE`（`CONNECT`／`QUERY`）、`STATUS`（`RECEIVED`／`EXECUTED`／`REJECTED`／`FAILED`／`TIMEOUT`） |
| 誰、為什麼 | `USER_EMAIL`、`USER_WINDOWS`、`USER_HOST`、`JIRA_KEY`、`REASON` |
| 查哪個客戶 | `CUSTOMER_SNO`、`CUSTOMER_NAME`、`PAIR_CODE`、`TARGET_DB`、`DB_SERVER`、`AGENT_HOST` |
| 查了什麼 | `SQL_TEXT`、`SQL_HASH`、`ROW_COUNT`、`TRUNCATED`、`RESULT_BYTES`、`SENSITIVE_HITS`、`ERROR_MSG`、`ELAPSED_MS` |
| 版本與時間 | `MCP_VERSION`、`EVENT_TIME`、`CREATED_AT` |

統計查詢次數時要依 `STATUS` 計算，不能直接 `COUNT(*)`。

### 程式修改

**`mcp_server.py`（.171，先做）**

- `query` 增加選填參數 `jira_key`；`reason` 已有。
- `USER_EMAIL` 啟動時由 `claude auth status --json` 的 `email` 取得，取不到記 `UNKNOWN`；`USER_WINDOWS`、`USER_HOST` 由系統取得。
- `connect` 成功時產生 `SESSION_ID` 並寫 `CONNECT` 列；重新 `connect` 產生新的 `SESSION_ID`。
- 送出前寫 `RECEIVED`，收到結果後寫結果列。
- .171 連線字串來自環境變數 `REMOTE_DB_LOG_CONN`，不進版控。

**`agent.py`（客戶資料庫，待決定）**

- 查詢訊息增加 `user_email`、`reason`、`jira_key`。
- 登入時確認 LOG 表存在。不存在時嘗試建立，失敗就擋下登入並顯示原因。
- 在 `handle` 中，執行前寫 `RECEIVED`，結束後寫結果列。
- LOG 用**獨立連線**、參數化語法、autocommit 寫入，與查詢連線分開。查詢連線仍維持最後 `ROLLBACK`。
- LOG 固定寫在登入時選的資料庫，切換後由 `TARGET_DB` 標示實際查詢的資料庫。
- 不允許查詢 LOG 表本身，列入敏感清單比對。

**exe 紅字**：只寫 .171 時可維持現有文字；客戶資料庫也要寫時改成「此工具僅執行查詢語法，除寫入稽核紀錄表外不更動資料，查詢語法會記錄以供稽核」。

### 寫入失敗

| 失敗位置 | 行為 |
|---|---|
| .171 `RECEIVED` | 預設不送出查詢；連線方式待決定（見待決事項） |
| 客戶資料庫 `RECEIVED` | 不執行查詢，回傳錯誤 |
| 結果列 | 查詢結果照常回傳，並標示 LOG 未寫入 |

### 限制

- `SQL_TEXT` 可能含個資字面值，例如被拒絕的 `WHERE EMPLOYEE_IDC_NO = '...'`。要限制 .171 可查閱的人並訂保存期限。
- relay 加上驗證前，`USER_EMAIL` 是工程師電腦自行回報，不能當成已驗證的身分。
- 只有 .171 時，LOG 只記得到經過 MCP 的動作；繞過 MCP 直連 relay 的查詢不會留下紀錄。
- 客戶在 exe 畫面切換資料庫的時間點不會寫入 .171，要到下一次 `connect` 才留下 `CONNECT` 列。
- 看不出問題有沒有解決；連線結束時間只能看最後一筆。

## 防護三：資料庫帳號控管

請客戶建立工具專用帳號，由資料庫本身擋下寫入。

### 建立語法

相容 SQL Server 2008（不用 `ALTER ROLE ... ADD MEMBER`）。由客戶 DBA 或經客戶同意後執行，密碼由客戶決定。帳號名稱為暫定。

```sql
USE [master];
CREATE LOGIN [ehrms_mcp_reader] WITH PASSWORD = N'<客戶自訂>', CHECK_POLICY = ON;

USE [<統編>];
CREATE USER [ehrms_mcp_reader] FOR LOGIN [ehrms_mcp_reader];
EXEC sp_addrolemember N'db_datareader', N'ehrms_mcp_reader';
GRANT SHOWPLAN TO [ehrms_mcp_reader];

-- LOG 表由有權限的帳號先建立
GRANT INSERT ON dbo.HRMS_CUSTOM_MCP_LOG TO [ehrms_mcp_reader];

-- 選用：資料庫層再擋一次敏感欄位，每個欄位一行
DENY SELECT ON dbo.HRMS_EMPLOYEE (EMPLOYEE_IDC_NO) TO [ehrms_mcp_reader];

USE [<統編>_WF];
CREATE USER [ehrms_mcp_reader] FOR LOGIN [ehrms_mcp_reader];
EXEC sp_addrolemember N'db_datareader', N'ehrms_mcp_reader';
GRANT SHOWPLAN TO [ehrms_mcp_reader];
```

提供成 `setup_reader_account.sql`，由工程師交給客戶，不由 exe 自動建立。

### 登入時自檢

agent 登入時檢查目前帳號，結果放進客戶資訊。`connect` 列給工程師，exe 畫面也顯示。

```sql
SELECT IS_SRVROLEMEMBER('sysadmin'), IS_MEMBER('db_owner'),
       HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT'),
       HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE'),
       HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE'),
       HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'SHOWPLAN'),
       HAS_PERMS_BY_NAME('dbo.HRMS_CUSTOM_MCP_LOG', 'OBJECT', 'INSERT')
```

| 結果 | 行為 |
|---|---|
| 符合專用帳號權限 | 正常使用 |
| 權限過大（`sysadmin`、`db_owner` 或有寫入權限） | 第一階段顯示警告；第二階段拒絕登入，由 `agent.ini` 設定 `account_policy = warn` 或 `strict` |
| 缺 SHOWPLAN 或 LOG 表 INSERT | 拒絕登入，因為無法檢查或留下紀錄 |

預設帳號維持 `ehrms`，等客戶都建好專用帳號再調整。

## 驗收

先在 SQL Server 2008、2012 與 `{SQL Server}` 驅動補測實測依據的兩張表。

| 防護 | 案例 |
|---|---|
| 共通 | 原有 34 個只讀案例全部維持通過 |
| 防護一 | 實測表所有寫法都被拒絕，且 LOG 記到 `SENSITIVE_HITS`；欄位互換被拒絕；一般查詢不受影響；值樣式遮罩抓得到備註中的證號、手機、Email；不會誤遮 `A11748` 這種員工編號 |
| 防護二 | 成功、拒絕、失敗、逾時各自留下正確紀錄；兩邊 `REQUEST_ID` 對得起來；客戶資料庫寫不進去時查詢不執行；AI 送來的 SQL 無法寫入或查詢 LOG 表；繞過 MCP 直連 relay 仍留下客戶端紀錄 |
| 防護三 | 專用帳號下，實測表結果與預期相同；權限過大帳號顯示警告或被拒絕；缺權限時拒絕登入；建立語法在 2008 可執行 |

## 待決事項

| 問題 | 建議 | 由誰決定 |
|---|---|---|
| 敏感欄位預設拒絕，是否可接受 | 可以，第 2 階段視需要再做 | 主管 |
| 敏感清單範圍，含 `EMPLOYEE_CARD_NO`、`GRADE_ID`、`LEVEL_ID` | 依盤點結果確認 | 主管、維運 |
| 可否在客戶資料庫建 LOG 表與專用帳號 | 可以，但要先取得客戶同意 | 主管、客戶 |
| 客戶資料送進 AI 的同意方式 | 先確認合約與同意書 | 主管、資安、法務 |
| LOG 保存期限與 .171 查閱權限 | 只開放主管與稽核人員 | 主管 |
| 工程師在 HA 網路連客戶時，是否連得到 .171 | 要先實測。連不到就先存本機再補傳，或由其他主機代寫 | 工程師、網管 |
| 客戶資料庫多一張表，是否影響版更或結構比對工具 | 要先確認 | 維運 |
| 真實客戶是否暫停使用，直到防護完成 | 建議暫停 | 主管 |

## MAX 版

MariaDB 沒有 SHOWPLAN，防護一不能照搬。MAX 版要改用 SQL 解析器判斷欄位來源，另外設計。防護二、三的概念相同，但語法與權限要改寫。等 1.0 版驗證完成後再同步。
