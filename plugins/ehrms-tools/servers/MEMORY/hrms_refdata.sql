/* =====================================================================
   HRMS_FAQ / HRMS_BULLETIN / HRMS_TASK：人工維護的高精度參考資料
   （請 DBA 於 EHRMS 共用 DB 執行；本檔可重複執行，物件存在即跳過）

   設計：
   - 三表皆由人工在 DB 端直接維護（INSERT/UPDATE），與 AI 自動累積、
     需要去重／supersede／審核狀態的 HRMS_MEMORY、HRMS_JIRA 性質不同：
       HRMS_FAQ      = 常見問題集（原本就由人工維護）
       HRMS_BULLETIN = 公告（已知問題／版本異動／維護公告等）
       HRMS_TASK     = FAE 修改客戶資料用的罐頭語法（SQL 範本＋風險等級＋注意事項）
   - 沒有 REVIEW_STATUS：內容本來就是人工先審過才寫入，不需要 pending/verified 分級
   - 用 ENABLED（BIT）下架過時資料，不必刪列；HRMS_BULLETIN 另有生效／失效日
   - MCP 帳號**只讀**三個 ACTIVE VIEW，本檔沒有任何提供給 MCP 的寫入權限
   - CREATED_BY/UPDATED_BY 為維護者帳號（人工填寫，非 MCP 自動產生）

   權限原則：
   - 沿用現有 MCP 查詢帳號，不新建登入
   - 僅授予三個 ACTIVE VIEW 的 SELECT；底層表不開放任何 INSERT/UPDATE/DELETE 給 MCP 帳號
   ===================================================================== */

------------------------------------------------------------------
-- ① HRMS_FAQ：常見問題集
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_FAQ') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_FAQ (
    ID             INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSFAQ PRIMARY KEY,

    QUESTION       NVARCHAR(500)  NOT NULL,   -- 常見問題敘述（檢索錨）
    ANSWER         NVARCHAR(MAX)  NOT NULL,   -- 解答內容
    CATEGORY       NVARCHAR(100)  NULL,       -- 功能分類（自由文字，如：出勤/薪資/系統操作）
    KEYWORDS       NVARCHAR(300)  NULL,       -- 逗號分隔 3~8 個，人工填寫強化檢索

    ENABLED        BIT            NOT NULL
        CONSTRAINT DF_HRMSFAQ_ENABLED DEFAULT 1,   -- 下架舊 FAQ 免刪列
    SOURCE_JIRA_KEY NVARCHAR(50)  NULL,       -- 若源自某張單，可選溯源

    CREATED_BY     NVARCHAR(100)  NOT NULL,   -- 維護者帳號
    CREATED_AT     DATETIME2(0)   NOT NULL
        CONSTRAINT DF_HRMSFAQ_CRE DEFAULT SYSDATETIME(),
    UPDATED_BY     NVARCHAR(100)  NULL,
    UPDATED_AT     DATETIME2(0)   NULL,

    META           NVARCHAR(MAX)  NULL        -- JSON 擴充欄（預留）
);
END
GO

IF OBJECT_ID(N'dbo.vwHRMS_FAQ_ACTIVE') IS NULL
    EXEC(N'
CREATE VIEW dbo.vwHRMS_FAQ_ACTIVE AS
SELECT * FROM dbo.HRMS_FAQ WHERE ENABLED = 1');
GO

------------------------------------------------------------------
-- ② HRMS_BULLETIN：公告（已知問題／版本異動／維護公告）
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_BULLETIN') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_BULLETIN (
    ID             INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSBULLETIN PRIMARY KEY,

    TITLE          NVARCHAR(200)  NOT NULL,   -- 公告標題（檢索錨）
    CONTENT        NVARCHAR(MAX)  NOT NULL,   -- 公告內容
    CATEGORY       NVARCHAR(50)   NULL,       -- 自由文字（如：已知問題/版本異動/維護公告）
    KEYWORDS       NVARCHAR(300)  NULL,

    EFFECTIVE_DATE DATE           NULL,       -- 生效日（NULL＝即時生效）
    EXPIRE_DATE    DATE           NULL,       -- 失效日（NULL＝長期有效）
    ENABLED        BIT            NOT NULL
        CONSTRAINT DF_HRMSBULLETIN_ENABLED DEFAULT 1,

    CREATED_BY     NVARCHAR(100)  NOT NULL,
    CREATED_AT     DATETIME2(0)   NOT NULL
        CONSTRAINT DF_HRMSBULLETIN_CRE DEFAULT SYSDATETIME(),
    UPDATED_BY     NVARCHAR(100)  NULL,
    UPDATED_AT     DATETIME2(0)   NULL,

    META           NVARCHAR(MAX)  NULL
);
END
GO

IF OBJECT_ID(N'dbo.vwHRMS_BULLETIN_ACTIVE') IS NULL
    EXEC(N'
CREATE VIEW dbo.vwHRMS_BULLETIN_ACTIVE AS
SELECT * FROM dbo.HRMS_BULLETIN
WHERE ENABLED = 1
  AND (EFFECTIVE_DATE IS NULL OR EFFECTIVE_DATE <= CAST(GETDATE() AS DATE))
  AND (EXPIRE_DATE    IS NULL OR EXPIRE_DATE    >= CAST(GETDATE() AS DATE))');
GO

------------------------------------------------------------------
-- ③ HRMS_TASK：FAE 修改客戶資料用的罐頭語法
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_TASK') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_TASK (
    ID             INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSTASK PRIMARY KEY,

    TASK_NAME      NVARCHAR(200)  NOT NULL,   -- 情境名稱（檢索錨）
    SCENARIO       NVARCHAR(1000) NULL,       -- 適用情境／觸發條件敘述
    SQL_TEMPLATE   NVARCHAR(MAX)  NOT NULL,   -- 罐頭 SQL（含 @參數 佔位符）
    PARAMS         NVARCHAR(500)  NULL,       -- 參數說明，如 @EMP_NO=員工編號
    AFFECTED_TABLE NVARCHAR(200)  NULL,       -- 影響資料表（風險評估用）
    RISK_LEVEL     VARCHAR(10)    NOT NULL
        CONSTRAINT DF_HRMSTASK_RISK DEFAULT N'中'
        CONSTRAINT CK_HRMSTASK_RISK CHECK (RISK_LEVEL IN (N'低', N'中', N'高')),
    PRECAUTIONS    NVARCHAR(1000) NULL,       -- 執行前注意事項／前置檢查
    KEYWORDS       NVARCHAR(300)  NULL,

    ENABLED        BIT            NOT NULL
        CONSTRAINT DF_HRMSTASK_ENABLED DEFAULT 1,
    SOURCE_JIRA_KEY NVARCHAR(50)  NULL,

    CREATED_BY     NVARCHAR(100)  NOT NULL,
    CREATED_AT     DATETIME2(0)   NOT NULL
        CONSTRAINT DF_HRMSTASK_CRE DEFAULT SYSDATETIME(),
    UPDATED_BY     NVARCHAR(100)  NULL,
    UPDATED_AT     DATETIME2(0)   NULL,

    META           NVARCHAR(MAX)  NULL
);
END
GO

IF OBJECT_ID(N'dbo.vwHRMS_TASK_ACTIVE') IS NULL
    EXEC(N'
CREATE VIEW dbo.vwHRMS_TASK_ACTIVE AS
SELECT * FROM dbo.HRMS_TASK WHERE ENABLED = 1');
GO

------------------------------------------------------------------
-- ④ 最小權限（★ 執行前把 <MCP帳號> 換成實際 database user）
--    三表皆人工維護，MCP 帳號只讀三個 ACTIVE VIEW，不開放底層表任何寫入權限
------------------------------------------------------------------
-- GRANT SELECT ON dbo.vwHRMS_FAQ_ACTIVE      TO [<MCP帳號>];
-- GRANT SELECT ON dbo.vwHRMS_BULLETIN_ACTIVE TO [<MCP帳號>];
-- GRANT SELECT ON dbo.vwHRMS_TASK_ACTIVE     TO [<MCP帳號>];

------------------------------------------------------------------
-- ⑤ 人工維護操作範例（DBA 或有權限人員執行；MCP 無此權限）
------------------------------------------------------------------
-- 新增一筆 FAQ：
-- INSERT INTO dbo.HRMS_FAQ (QUESTION, ANSWER, CATEGORY, KEYWORDS, CREATED_BY)
-- VALUES (N'問題敘述', N'解答內容', N'分類', N'關鍵字1,關鍵字2', N'ziping.zhou');
--
-- 下架一筆公告（不刪列）：
-- UPDATE dbo.HRMS_BULLETIN SET ENABLED = 0, UPDATED_BY = N'ziping.zhou', UPDATED_AT = SYSDATETIME()
-- WHERE ID = <公告ID>;
--
-- 新增一筆罐頭語法：
-- INSERT INTO dbo.HRMS_TASK
--   (TASK_NAME, SCENARIO, SQL_TEMPLATE, PARAMS, AFFECTED_TABLE, RISK_LEVEL, PRECAUTIONS, KEYWORDS, CREATED_BY)
-- VALUES
--   (N'情境名稱', N'適用情境敘述', N'UPDATE ... WHERE EMP_NO=@EMP_NO',
--    N'@EMP_NO=員工編號', N'受影響的表', N'中', N'執行前注意事項', N'關鍵字1,關鍵字2', N'ziping.zhou');
