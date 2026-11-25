/* =====================================================================
   線上知識庫 MCP：使用紀錄、候選、疑慮標記、使用者對應 四張表
   位置：與 HRMS_MEMORY 同一個資料庫
   （請 DBA 執行；本檔可重複執行，物件存在即跳過）

   依據：memory_review/討論清單_線上知識庫MCP.md（2026-11-20 定案）

   共同設計：
   - 只新增不修改：線上知識庫 MCP 的帳號只需要 SELECT 與 INSERT
   - 候選與疑慮標記的「處理結果」欄位（STATUS、RESOLVED_*）不給 MCP 帳號，
     只由人或清理 agent 用另一個帳號更新（比照 HRMS_MEMORY 的 REVIEW_* 欄位）
   - 時間一律用資 料庫主機的本地時間（SYSDATETIME），與 HRMS_MEMORY、HRMS_JIRA 一致
   - 不修改任何既有資料表。remember 新增的「用途標記」存在 HRMS_MEMORY.META
     既有的 JSON 欄位（例：{"purpose":"reply"}），不需要 ALTER TABLE

   四張表：
   ① HRMS_KB_USAGE_LOG   每次工具呼叫一筆
   ② HRMS_KB_CANDIDATE   remember 撞到「高度相似但結論不同」時，新內容存在這裡，不進正式記憶
   ③ HRMS_KB_FLAG        疑慮標記：某筆知識可能有錯
   ④ HRMS_KB_USER_BINDING 電腦位址對應到哪位使用者（首次使用確認信箱後記住）

   2026-11-25 新增第五張（同日已建立）：
   ⑤ HRMS_MEMORY_NOTE     工作筆記：知識庫以外查到的新結論與使用者的修正，審核後才進 HRMS_MEMORY
   ===================================================================== */

------------------------------------------------------------------
-- ① 使用紀錄：一次工具呼叫一筆
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_KB_USAGE_LOG') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_KB_USAGE_LOG (
    ID              BIGINT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSKBLOG PRIMARY KEY,

    -- ── 時間 ─────────────────────────────────────────────
    EVENT_TIME      DATETIME2(3)   NOT NULL,   -- 呼叫當下（伺服器填入）
    CREATED_AT      DATETIME2(3)   NOT NULL    -- 寫入資料庫的時間；斷線補送時會晚於 EVENT_TIME
        CONSTRAINT DF_HRMSKBLOG_CREATED DEFAULT SYSDATETIME(),

    -- ── 誰 ───────────────────────────────────────────────
    USER_NAME       NVARCHAR(100)  NOT NULL,   -- Jira 帳號（如 ziping.zhou）；排程填排程名稱
    USER_EMAIL      NVARCHAR(200)  NULL,
    IDENTITY_SOURCE VARCHAR(20)    NOT NULL    -- jira_list=Jira 名單確認 / sso=公司帳號登入 / service=排程或網站自己的身分 / none=尚未確認身分（被擋下的呼叫，USER_NAME 填 unknown）
        CONSTRAINT CK_HRMSKBLOG_IDSRC CHECK (IDENTITY_SOURCE IN ('jira_list','sso','service','none')),
    CLIENT_ADDR     VARCHAR(64)    NULL,       -- 連線的電腦位址

    -- ── 哪一段使用 ───────────────────────────────────────
    SESSION_ID      VARCHAR(36)    NOT NULL,   -- 同一次連線共用
    CALL_SEQ        INT            NOT NULL,   -- 這次連線中的第幾次呼叫
    ORIGIN          VARCHAR(20)    NOT NULL    -- chat=真人對話 / schedule=排程 / web=FAQ_web 網站
        CONSTRAINT CK_HRMSKBLOG_ORIGIN CHECK (ORIGIN IN ('chat','schedule','web')),
    CLIENT_NAME     NVARCHAR(100)  NULL,       -- 用戶端程式名稱與版本（用戶端自己回報）

    -- ── 做了什麼 ─────────────────────────────────────────
    TOOL_NAME       VARCHAR(50)    NOT NULL,   -- 例：kb_search、kb_get、remember、kb_flag、skill_get
    ARGUMENTS       NVARCHAR(MAX)  NULL,       -- 工具參數（JSON）
    QUERY_TEXT      NVARCHAR(2000) NULL,       -- 從參數取出的查詢文字，方便直接統計
    JIRA_KEY        NVARCHAR(50)   NULL,       -- 對話有提到 Jira 單才有值

    -- ── 結果 ─────────────────────────────────────────────
    RESULT_REFS     NVARCHAR(MAX)  NULL,       -- 回傳的知識（JSON 陣列：類型、編號、分數）；不存全文
    RESULT_COUNT    INT            NULL,
    NO_RESULT       BIT            NULL,       -- 查詢類：1=查無資料
    WRITE_OUTCOME   VARCHAR(20)    NULL        -- 寫入類才有值
        CONSTRAINT CK_HRMSKBLOG_WRITE CHECK (WRITE_OUTCOME IS NULL OR WRITE_OUTCOME IN
            ('created',      -- 已新增正式記憶
             'duplicate',    -- 已有等價記憶，未新增
             'candidate',    -- 與既有記憶結論不同，存成候選
             'flagged',      -- 新增了一筆疑慮標記
             'rejected',     -- 欄位檢查沒過（缺必填、SQL 檢查不通過等）
             'failed')),     -- 寫入時發生錯誤
    WRITE_REF       NVARCHAR(50)   NULL,       -- 寫入結果指向哪一筆，例：memory#231、candidate#12、flag#7

    -- ── 其他 ─────────────────────────────────────────────
    ELAPSED_MS      INT            NULL,
    ERROR_MSG       NVARCHAR(1000) NULL,
    SERVER_VERSION  VARCHAR(20)    NOT NULL,   -- 線上知識庫 MCP 的版本
    SEARCH_VERSION  VARCHAR(20)    NULL        -- 檢索方式版本（之後加入 RAG 時用來區分前後資料）
);

CREATE INDEX IX_HRMSKBLOG_TIME    ON dbo.HRMS_KB_USAGE_LOG (EVENT_TIME);
CREATE INDEX IX_HRMSKBLOG_SESSION ON dbo.HRMS_KB_USAGE_LOG (SESSION_ID, CALL_SEQ);
CREATE INDEX IX_HRMSKBLOG_USER    ON dbo.HRMS_KB_USAGE_LOG (USER_NAME, EVENT_TIME);
CREATE INDEX IX_HRMSKBLOG_TOOL    ON dbo.HRMS_KB_USAGE_LOG (TOOL_NAME, EVENT_TIME);
CREATE INDEX IX_HRMSKBLOG_JIRA    ON dbo.HRMS_KB_USAGE_LOG (JIRA_KEY)
    WHERE JIRA_KEY IS NOT NULL;
END
GO

------------------------------------------------------------------
-- ② 候選：與既有記憶「高度相似但結論不同」的新內容
--    不進 HRMS_MEMORY、不會被查詢到；等人或清理 agent 處理
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_KB_CANDIDATE') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_KB_CANDIDATE (
    ID                 INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSKBCAND PRIMARY KEY,

    -- ── 原本要寫進記憶的內容（欄位比照 HRMS_MEMORY）────────
    MEM_TYPE           VARCHAR(10)    NOT NULL
        CONSTRAINT CK_HRMSKBCAND_TYPE CHECK (MEM_TYPE IN ('System','Engineer')),
    PURPOSE            VARCHAR(20)    NULL       -- 用途標記
        CONSTRAINT CK_HRMSKBCAND_PURPOSE CHECK (PURPOSE IS NULL OR PURPOSE IN
            ('reply',      -- 可回覆客戶
             'procedure',  -- 處理步驟
             'data_fix',   -- 資料修正語法
             'general')),  -- 一般知識
    TOPIC              NVARCHAR(100)  NOT NULL,
    CONTENT            NVARCHAR(2000) NOT NULL,
    KEYWORDS           NVARCHAR(300)  NULL,
    FUNC_PATH          NVARCHAR(300)  NULL,
    ENTRY_PATH         NVARCHAR(500)  NULL,
    ENTRY_METHOD       NVARCHAR(300)  NULL,
    REF_KEY            NVARCHAR(50)   NULL,      -- 來源 Jira 單號
    SOURCE             NVARCHAR(50)   NULL,
    CREATED_BY         NVARCHAR(100)  NOT NULL,  -- 如 ziping.zhou(AI)
    CREATED_AT         DATETIME2(0)   NOT NULL
        CONSTRAINT DF_HRMSKBCAND_CRE DEFAULT SYSDATETIME(),

    -- ── 為什麼變成候選 ───────────────────────────────────
    CONFLICT_MEMORY_ID INT            NOT NULL   -- 撞到哪一筆既有記憶
        CONSTRAINT FK_HRMSKBCAND_MEMORY REFERENCES dbo.HRMS_MEMORY(ID),
    SIMILARITY         DECIMAL(4,3)   NULL,      -- 當時算出的相似度（0–1）
    USAGE_LOG_ID       BIGINT         NULL,      -- 對應的使用紀錄

    -- ── 處理結果（MCP 帳號不可寫，只由人或清理 agent 更新）──
    STATUS             VARCHAR(10)    NOT NULL
        CONSTRAINT DF_HRMSKBCAND_STATUS DEFAULT 'open'
        CONSTRAINT CK_HRMSKBCAND_STATUS CHECK (STATUS IN
            ('open',       -- 待處理
             'accepted',   -- 採用：已寫成正式記憶（見 RESULT_MEMORY_ID）
             'rejected')), -- 不採用
    RESOLVED_BY        NVARCHAR(100)  NULL,
    RESOLVED_AT        DATETIME2(0)   NULL,
    RESULT_MEMORY_ID   INT            NULL,      -- 採用後產生的正式記憶
    RESOLVE_NOTE       NVARCHAR(1000) NULL
);

CREATE INDEX IX_HRMSKBCAND_STATUS   ON dbo.HRMS_KB_CANDIDATE (STATUS, CREATED_AT);
CREATE INDEX IX_HRMSKBCAND_CONFLICT ON dbo.HRMS_KB_CANDIDATE (CONFLICT_MEMORY_ID);
END
GO

------------------------------------------------------------------
-- ③ 疑慮標記：某筆知識可能有錯
--    一筆懷疑一列，不動原本的知識；查詢時帶出「有疑慮」與理由
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_KB_FLAG') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_KB_FLAG (
    ID              INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSKBFLAG PRIMARY KEY,

    -- ── 標記哪一筆 ───────────────────────────────────────
    TARGET_TYPE     VARCHAR(20)    NOT NULL
        CONSTRAINT CK_HRMSKBFLAG_TYPE CHECK (TARGET_TYPE IN
            ('memory','wiki','faq','sop','task','jira','bulletin')),
    TARGET_ID       NVARCHAR(200)  NOT NULL,   -- 各表的編號；Wiki 填「主題代碼#規則編號」，如 CARD#R7

    -- ── 為什麼懷疑 ───────────────────────────────────────
    REASON          NVARCHAR(1000) NOT NULL,
    EVIDENCE        NVARCHAR(1000) NULL,       -- 依據：程式位置、單號、其他知識的編號
    JIRA_KEY        NVARCHAR(50)   NULL,
    FLAG_SOURCE     VARCHAR(20)    NOT NULL    -- ai=使用者的 AI 主動標記 / auto_conflict=remember 撞到不同結論時伺服器自動標記 / human=人工標記
        CONSTRAINT CK_HRMSKBFLAG_SRC CHECK (FLAG_SOURCE IN ('ai','auto_conflict','human')),
    CANDIDATE_ID    INT            NULL        -- auto_conflict 時指向新的說法
        CONSTRAINT FK_HRMSKBFLAG_CAND REFERENCES dbo.HRMS_KB_CANDIDATE(ID),
    USAGE_LOG_ID    BIGINT         NULL,

    CREATED_BY      NVARCHAR(100)  NOT NULL,
    CREATED_AT      DATETIME2(0)   NOT NULL
        CONSTRAINT DF_HRMSKBFLAG_CRE DEFAULT SYSDATETIME(),

    -- ── 處理結果（MCP 帳號不可寫，只由人或清理 agent 更新）──
    STATUS          VARCHAR(10)    NOT NULL
        CONSTRAINT DF_HRMSKBFLAG_STATUS DEFAULT 'open'
        CONSTRAINT CK_HRMSKBFLAG_STATUS CHECK (STATUS IN
            ('open',        -- 待處理：查詢時顯示有疑慮
             'confirmed',   -- 確認原知識有錯（已另行取代或修正）
             'dismissed')), -- 確認原知識沒錯，解除標記
    RESOLVED_BY     NVARCHAR(100)  NULL,
    RESOLVED_AT     DATETIME2(0)   NULL,
    RESOLVE_NOTE    NVARCHAR(1000) NULL
);

CREATE INDEX IX_HRMSKBFLAG_TARGET ON dbo.HRMS_KB_FLAG (TARGET_TYPE, TARGET_ID, STATUS);
CREATE INDEX IX_HRMSKBFLAG_STATUS ON dbo.HRMS_KB_FLAG (STATUS, CREATED_AT);
END
GO

-- 待處理的疑慮標記（查詢結果要帶出的就是這些）
IF OBJECT_ID(N'dbo.vwHRMS_KB_FLAG_OPEN') IS NULL
    EXEC(N'
CREATE VIEW dbo.vwHRMS_KB_FLAG_OPEN AS
SELECT f.ID, f.TARGET_TYPE, f.TARGET_ID, f.REASON, f.EVIDENCE, f.JIRA_KEY,
       f.FLAG_SOURCE, f.CANDIDATE_ID, f.CREATED_BY, f.CREATED_AT
FROM dbo.HRMS_KB_FLAG f
WHERE f.STATUS = ''open''');
GO

------------------------------------------------------------------
-- ④ 使用者對應：電腦位址 → 使用者
--    首次使用確認信箱（比對 Jira 名單）後新增一列；位址換人或換位址就再新增一列，
--    以同一個 CLIENT_ADDR 最新的一列為準。接上公司帳號登入後這張表就不再需要
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_KB_USER_BINDING') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_KB_USER_BINDING (
    ID            INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSKBBIND PRIMARY KEY,
    CLIENT_ADDR   VARCHAR(64)    NOT NULL,
    USER_EMAIL    NVARCHAR(200)  NOT NULL,   -- 使用者確認的信箱
    USER_NAME     NVARCHAR(100)  NOT NULL,   -- 對到的 Jira 帳號
    JIRA_ACCOUNT_ID NVARCHAR(100) NULL,      -- Jira 名單上的帳號識別碼
    CONFIRMED_AT  DATETIME2(0)   NOT NULL
        CONSTRAINT DF_HRMSKBBIND_AT DEFAULT SYSDATETIME()
);

CREATE INDEX IX_HRMSKBBIND_ADDR ON dbo.HRMS_KB_USER_BINDING (CLIENT_ADDR, ID DESC);
END
GO

------------------------------------------------------------------
-- 補丁（2026-11-21）：IDENTITY_SOURCE 多一個值 none（尚未確認身分、被擋下的呼叫）
-- 2026-11-20 建立的表沒有這個值，被擋下的呼叫會寫不進使用紀錄；新建的表不受影響，重複執行也安全
------------------------------------------------------------------
IF EXISTS (SELECT 1 FROM sys.check_constraints
           WHERE name = N'CK_HRMSKBLOG_IDSRC' AND definition NOT LIKE N'%none%')
BEGIN
    ALTER TABLE dbo.HRMS_KB_USAGE_LOG DROP CONSTRAINT CK_HRMSKBLOG_IDSRC;
    ALTER TABLE dbo.HRMS_KB_USAGE_LOG ADD CONSTRAINT CK_HRMSKBLOG_IDSRC
        CHECK (IDENTITY_SOURCE IN ('jira_list','sso','service','none'));
END
GO

------------------------------------------------------------------
-- ⑤ 工作筆記（2026-11-25，SPEC 決策 17）：每次處理中「知識庫以外」得到的結論
--    要存：另外查出來的新結論（例如看程式碼得到的）、使用者對 AI 的修正
--    不存：結論完全來自知識庫 MCP 查詢結果的
--    不是正式記憶、不會被查詢工具查到；等審核 agent 確認後才寫入 HRMS_MEMORY
--    2026-11-25 已建立
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_MEMORY_NOTE') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_MEMORY_NOTE (
    ID               INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSMEMNOTE PRIMARY KEY,

    -- ── 筆記本體 ─────────────────────────────────────────
    NOTE_KIND        VARCHAR(20)    NOT NULL
        CONSTRAINT CK_HRMSMEMNOTE_KIND CHECK (NOTE_KIND IN
            ('finding',      -- 知識庫以外查出來的新結論（看程式碼等）
             'correction',   -- 使用者對 AI 的修正
             'process')),    -- 過程紀錄：做了什麼、查到什麼、卡在哪（2026-11-25 加，既有的表用下面的補丁）
    TOPIC            NVARCHAR(100)  NOT NULL,   -- 主題
    CONTENT          NVARCHAR(4000) NOT NULL,   -- 結論
    EVIDENCE         NVARCHAR(2000) NULL,       -- 依據：程式位置、使用者的原話
    KEYWORDS         NVARCHAR(300)  NULL,       -- 逗號分隔
    ENTRY_PATH       NVARCHAR(500)  NULL,       -- 程式入口（看程式碼得到的結論才有）
    ENTRY_METHOD     NVARCHAR(300)  NULL,

    -- ── 來源 ─────────────────────────────────────────────
    REF_KEY          NVARCHAR(50)   NULL,       -- 對話有提到 Jira 單才有值（決策 7）；欄位名沿用 HRMS_MEMORY
    ORIGIN           VARCHAR(20)    NOT NULL    -- chat=Claude Code 等對話 / web=FAQ_web AI 助理 / schedule=排程
        CONSTRAINT CK_HRMSMEMNOTE_ORIGIN CHECK (ORIGIN IN ('chat','web','schedule')),
    SESSION_ID       VARCHAR(36)    NULL,       -- 同一次連線，可對回使用紀錄
    USAGE_LOG_ID     BIGINT         NULL,       -- 對應的使用紀錄
    CREATED_BY       NVARCHAR(100)  NOT NULL,   -- 如 ziping.zhou(AI)
    CREATED_AT       DATETIME2(0)   NOT NULL
        CONSTRAINT DF_HRMSMEMNOTE_CRE DEFAULT SYSDATETIME(),

    -- ── 處理結果（MCP 帳號不可寫，只由人或審核 agent 更新）──
    STATUS           VARCHAR(10)    NOT NULL
        CONSTRAINT DF_HRMSMEMNOTE_STATUS DEFAULT 'open'
        CONSTRAINT CK_HRMSMEMNOTE_STATUS CHECK (STATUS IN
            ('open',       -- 待審核
             'promoted',   -- 結論正確，已寫成正式記憶（見 RESULT_MEMORY_ID）
             'rejected')), -- 不採用
    RESOLVED_BY      NVARCHAR(100)  NULL,
    RESOLVED_AT      DATETIME2(0)   NULL,
    RESULT_MEMORY_ID INT            NULL        -- 升級後產生的正式記憶
        CONSTRAINT FK_HRMSMEMNOTE_MEMORY REFERENCES dbo.HRMS_MEMORY(ID),
    RESOLVE_NOTE     NVARCHAR(1000) NULL
);

CREATE INDEX IX_HRMSMEMNOTE_STATUS ON dbo.HRMS_MEMORY_NOTE (STATUS, CREATED_AT);
CREATE INDEX IX_HRMSMEMNOTE_REF    ON dbo.HRMS_MEMORY_NOTE (REF_KEY)
    WHERE REF_KEY IS NOT NULL;
END
GO

------------------------------------------------------------------
-- 補丁（2026-11-25）：工作筆記的種類多一個值 process（過程紀錄，SPEC 決策 23）
-- 過程紀錄＝做了什麼、查到什麼、卡在哪，沒有結論也能寫；是 kb_note／note_log 沒給種類時的預設值。
-- 2026-11-25 已套用（子平執行，已查到限制內容含 process）。重複執行也安全。
-- 套用前寫入的過程紀錄是存成 finding、內容最前面加上「〔過程紀錄〕」（當時只有 note#5 一筆測試資料）；
--   程式仍保留這個相容寫法，給還沒套用補丁的環境用。
------------------------------------------------------------------
IF EXISTS (SELECT 1 FROM sys.check_constraints
           WHERE name = N'CK_HRMSMEMNOTE_KIND' AND definition NOT LIKE N'%process%')
BEGIN
    ALTER TABLE dbo.HRMS_MEMORY_NOTE DROP CONSTRAINT CK_HRMSMEMNOTE_KIND;
    ALTER TABLE dbo.HRMS_MEMORY_NOTE ADD CONSTRAINT CK_HRMSMEMNOTE_KIND
        CHECK (NOTE_KIND IN ('finding','correction','process'));
END
GO

------------------------------------------------------------------
-- 稽核查詢（只讀，SPEC 決策 23）：有查詢卻沒有寫工作筆記的連線
-- 寫過筆記＝呼叫過 kb_note，或 kb_search 帶 note（兩者的 WRITE_REF 都是 note#編號）。
-- 只查了知識庫、沒做其他事的連線本來就不用寫，所以這個比例只能看趨勢，不能當成漏寫率。
-- 本機 plugin（ehrms-memory）沒有使用紀錄，不在這裡面。
------------------------------------------------------------------
/*
-- ① 逐一連線：查詢次數、筆記數
SELECT  SESSION_ID, MIN(USER_NAME) AS USER_NAME, MIN(CLIENT_NAME) AS CLIENT_NAME,
        MIN(EVENT_TIME) AS FIRST_CALL, MAX(EVENT_TIME) AS LAST_CALL,
        SUM(CASE WHEN TOOL_NAME IN ('kb_search','kb_get') THEN 1 ELSE 0 END) AS LOOKUPS,
        SUM(CASE WHEN WRITE_REF LIKE 'note#%' THEN 1 ELSE 0 END)             AS NOTES,
        MAX(JIRA_KEY) AS JIRA_KEY
FROM    dbo.HRMS_KB_USAGE_LOG
WHERE   EVENT_TIME >= DATEADD(DAY, -30, SYSDATETIME())
GROUP BY SESSION_ID
HAVING  SUM(CASE WHEN TOOL_NAME IN ('kb_search','kb_get') THEN 1 ELSE 0 END) > 0
ORDER BY NOTES, LOOKUPS DESC;

-- ② 依使用者：有查詢的連線數、其中沒寫筆記的連線數
SELECT  USER_NAME, COUNT(*) AS SESSIONS, SUM(CASE WHEN NOTES = 0 THEN 1 ELSE 0 END) AS SESSIONS_WITHOUT_NOTE
FROM   (SELECT SESSION_ID, MIN(USER_NAME) AS USER_NAME,
               SUM(CASE WHEN WRITE_REF LIKE 'note#%' THEN 1 ELSE 0 END) AS NOTES
        FROM   dbo.HRMS_KB_USAGE_LOG
        WHERE  EVENT_TIME >= DATEADD(DAY, -30, SYSDATETIME())
        GROUP BY SESSION_ID
        HAVING SUM(CASE WHEN TOOL_NAME IN ('kb_search','kb_get') THEN 1 ELSE 0 END) > 0) s
GROUP BY USER_NAME
ORDER BY SESSIONS_WITHOUT_NOTE DESC;
*/

------------------------------------------------------------------
-- 權限（★ 執行前把帳號換成實際的 database user）
--
-- 目前決定先沿用現有帳號，所以下面先全部註解。
-- 之後另開專用帳號時照這份開：
--   <知識庫帳號>：線上知識庫 MCP 用，只新增不修改
--   <清理帳號>  ：人或清理 agent 用，可更新候選與疑慮標記的處理結果
------------------------------------------------------------------
-- GRANT SELECT, INSERT ON dbo.HRMS_KB_USAGE_LOG    TO [<知識庫帳號>];
-- GRANT SELECT, INSERT ON dbo.HRMS_KB_CANDIDATE    TO [<知識庫帳號>];
-- GRANT SELECT, INSERT ON dbo.HRMS_KB_FLAG         TO [<知識庫帳號>];
-- GRANT SELECT, INSERT ON dbo.HRMS_KB_USER_BINDING TO [<知識庫帳號>];
-- GRANT SELECT ON dbo.vwHRMS_KB_FLAG_OPEN          TO [<知識庫帳號>];
-- DENY UPDATE, DELETE ON dbo.HRMS_KB_USAGE_LOG     TO [<知識庫帳號>];
-- DENY UPDATE, DELETE ON dbo.HRMS_KB_CANDIDATE     TO [<知識庫帳號>];
-- DENY UPDATE, DELETE ON dbo.HRMS_KB_FLAG          TO [<知識庫帳號>];
-- DENY UPDATE, DELETE ON dbo.HRMS_KB_USER_BINDING  TO [<知識庫帳號>];
--
-- GRANT SELECT ON dbo.HRMS_KB_USAGE_LOG TO [<清理帳號>];
-- GRANT SELECT ON dbo.HRMS_KB_CANDIDATE TO [<清理帳號>];
-- GRANT SELECT ON dbo.HRMS_KB_FLAG      TO [<清理帳號>];
-- GRANT UPDATE (STATUS, RESOLVED_BY, RESOLVED_AT, RESULT_MEMORY_ID, RESOLVE_NOTE)
--     ON dbo.HRMS_KB_CANDIDATE TO [<清理帳號>];
-- GRANT UPDATE (STATUS, RESOLVED_BY, RESOLVED_AT, RESOLVE_NOTE)
--     ON dbo.HRMS_KB_FLAG TO [<清理帳號>];
--
-- 工作筆記（⑤）
-- GRANT SELECT, INSERT ON dbo.HRMS_MEMORY_NOTE TO [<知識庫帳號>];
-- DENY  UPDATE, DELETE ON dbo.HRMS_MEMORY_NOTE TO [<知識庫帳號>];
-- GRANT SELECT ON dbo.HRMS_MEMORY_NOTE TO [<清理帳號>];
-- GRANT UPDATE (STATUS, RESOLVED_BY, RESOLVED_AT, RESULT_MEMORY_ID, RESOLVE_NOTE)
--     ON dbo.HRMS_MEMORY_NOTE TO [<清理帳號>];
