/* =====================================================================
   HRMS_CUSTOM_MCP_LOG：ehrms-custom-mcp 操作紀錄表
   位置：172.19.5.171（EHRMS-LAB-DB）／EHRDB_MULTI_IE
   （請 DBA 執行；本檔可重複執行，物件存在即跳過）

   設計：
   - 只新增不修改：同一筆查詢有 RECEIVED 與結果兩列，以 REQUEST_ID 串接；
     同一次連線以 SESSION_ID 串接
   - 只記語法與筆數，不存查詢結果與資料庫密碼
   - 寫入者是工程師電腦上的 mcp_server.py，所以只記得到經過 MCP 的動作；
     客戶在 exe 畫面切換資料庫，要到下一次 connect 才會留下 CONNECT 列

   權限原則：
   - 另開專用帳號，只給 INSERT。現有 MCP 帳號是 sysadmin，DENY 對它無效
   ===================================================================== */

IF OBJECT_ID(N'dbo.HRMS_CUSTOM_MCP_LOG') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_CUSTOM_MCP_LOG (
    ID             BIGINT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSCUSTOMMCPLOG PRIMARY KEY,

    -- ── 事件 ─────────────────────────────────────────────
    SESSION_ID     VARCHAR(36)    NOT NULL,   -- 一次連線一個（MCP 端 connect 時產生）
    REQUEST_ID     VARCHAR(36)    NULL,       -- 一筆查詢一個（查詢訊息的 id）
    EVENT_TYPE     VARCHAR(20)    NOT NULL    -- CONNECT=配對成功 / QUERY=查詢
        CONSTRAINT CK_HRMSCUSTOMMCPLOG_EVENT CHECK (EVENT_TYPE IN ('CONNECT','QUERY')),
    STATUS         VARCHAR(20)    NOT NULL    -- RECEIVED=已送出 / EXECUTED=成功 / REJECTED=被擋 / FAILED=失敗 / TIMEOUT=逾時
        CONSTRAINT CK_HRMSCUSTOMMCPLOG_STATUS CHECK (STATUS IN ('RECEIVED','EXECUTED','REJECTED','FAILED','TIMEOUT')),

    -- ── 誰、為什麼 ───────────────────────────────────────
    USER_EMAIL     NVARCHAR(200)  NOT NULL,   -- Claude 帳號 Email；relay 加上驗證前為自行回報
    USER_WINDOWS   NVARCHAR(100)  NULL,       -- 工程師電腦的 Windows 帳號，輔助比對
    USER_HOST      NVARCHAR(128)  NULL,       -- 工程師電腦名稱
    JIRA_KEY       NVARCHAR(50)   NULL,       -- 對應工單
    REASON         NVARCHAR(1000) NULL,       -- 用途說明

    -- ── 查哪個客戶 ───────────────────────────────────────
    CUSTOMER_SNO   VARCHAR(20)    NULL,       -- 客戶統編
    CUSTOMER_NAME  NVARCHAR(200)  NULL,
    PAIR_CODE      VARCHAR(10)    NOT NULL,
    TARGET_DB      NVARCHAR(128)  NOT NULL,   -- 實際查詢的資料庫
    DB_SERVER      NVARCHAR(128)  NULL,
    AGENT_HOST     NVARCHAR(128)  NULL,       -- 客戶機器名稱

    -- ── 查了什麼 ─────────────────────────────────────────
    SQL_TEXT       NVARCHAR(MAX)  NULL,       -- 查詢語法
    SQL_HASH       CHAR(64)       NULL,       -- SQL_TEXT 的 SHA-256，日後與客戶端紀錄比對
    ROW_COUNT      INT            NULL,       -- 回傳筆數
    TRUNCATED      BIT            NULL,       -- 是否超過 500 筆被截斷
    RESULT_BYTES   INT            NULL,       -- 回傳資料量，評估外傳規模
    SENSITIVE_HITS NVARCHAR(1000) NULL,       -- 命中的敏感欄位（防護一上線後才有值）
    ERROR_MSG      NVARCHAR(1000) NULL,       -- 拒絕或失敗原因
    ELAPSED_MS     INT            NULL,       -- 從送出到收到結果

    -- ── 版本與時間 ───────────────────────────────────────
    MCP_VERSION    VARCHAR(20)    NULL,
    EVENT_TIME     DATETIME2(3)   NOT NULL,   -- 事件時間（UTC，由 MCP 端填入）
    CREATED_AT     DATETIME2(3)   NOT NULL    -- 寫入時間（UTC，由資料庫填入）
        CONSTRAINT DF_HRMSCUSTOMMCPLOG_CREATED DEFAULT SYSUTCDATETIME()
);

CREATE INDEX IX_HRMSCUSTOMMCPLOG_TIME ON dbo.HRMS_CUSTOM_MCP_LOG (EVENT_TIME);
CREATE INDEX IX_HRMSCUSTOMMCPLOG_SESSION ON dbo.HRMS_CUSTOM_MCP_LOG (SESSION_ID);
CREATE INDEX IX_HRMSCUSTOMMCPLOG_CUSTOMER ON dbo.HRMS_CUSTOM_MCP_LOG (CUSTOMER_SNO, EVENT_TIME);
CREATE INDEX IX_HRMSCUSTOMMCPLOG_USER ON dbo.HRMS_CUSTOM_MCP_LOG (USER_EMAIL, EVENT_TIME);
END
GO

------------------------------------------------------------------
-- 權限（請 DBA 依實際帳號調整後執行）
------------------------------------------------------------------
-- GRANT INSERT ON dbo.HRMS_CUSTOM_MCP_LOG TO [<LOG專用帳號>];
-- DENY UPDATE, DELETE ON dbo.HRMS_CUSTOM_MCP_LOG TO [<LOG專用帳號>];
