/* =====================================================================
   HRMS_JIRA：Jira 維運單結案紀錄表
   （請 DBA 於 EHRMS 共用 DB 執行；本檔可重複執行，物件存在即跳過）

   設計：
   - 一單一筆有效紀錄：以 JIRA_KEY 聚合，append-only；
     修正結論 = 插入新記錄並以 SUPERSEDES_ID 指向被取代的舊記錄，
     讀取端（vwHRMS_JIRA_ACTIVE）自動排除被取代者
   - 兩型 ISSUE_KIND：
       Crisis = 維運單（客戶回報問題）
       Story  = 改程式與 bug fix（CHANGED_FILES 必填，由 MCP 程式端把關）
   - 與 HRMS_MEMORY 分工：本表＝單一案件的結案檔案（根因/解法/修改程式）；
     HRMS_MEMORY＝跨單可泛化的系統知識與程式入口。互補不取代。
   - 記錄者 CREATED_BY 與 Jira 帳號一致（如 ziping.zhou），由 MCP 填入
   - 無 HIT_COUNT / REVIEW_* 欄：案件紀錄可回 Jira 原單對證，
     不需要使用強化與人工抽查機制

   權限原則：
   - 沿用現有 MCP 查詢帳號，不新建登入
   - 僅授予 SELECT / INSERT；DENY UPDATE 與 DELETE（全表，比 HRMS_MEMORY 更嚴）

   備註：
   - 既有 HRMS_JIRA_FAQ_HISTORY / HRMS_JIRA_FAQ_EXCLUSION 為 2025-10
     舊實驗殘留（僅 1 筆資料），與本表無關；是否歸檔由團隊另行決定。
   ===================================================================== */

------------------------------------------------------------------
-- ① 結案紀錄表
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_JIRA') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_JIRA (
    ID             INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSJIRA PRIMARY KEY,

    -- ── 案件識別 ─────────────────────────────────────────
    JIRA_KEY       NVARCHAR(50)   NOT NULL,   -- Jira 單號（EHRMSONE-32543）
    ISSUE_KIND     VARCHAR(10)    NOT NULL    -- Crisis=維運單 / Story=改程式與 bug fix
        CONSTRAINT CK_HRMSJIRA_KIND CHECK (ISSUE_KIND IN ('Crisis','Story')),
    TITLE          NVARCHAR(200)  NOT NULL,   -- 一句話案件摘要（檢索錨）

    -- ── 結案知識本體 ─────────────────────────────────────
    ROOT_CAUSE     NVARCHAR(2000) NOT NULL,   -- 發生根因
    RESOLUTION     NVARCHAR(2000) NOT NULL,   -- 解決方式
    CHANGED_FILES  NVARCHAR(1000) NULL,       -- 修改程式：一行一筆「路徑 :: 函式」
                                              -- （Story 必填由 MCP 把關；查詢類 Crisis 可空）
    KEYWORDS       NVARCHAR(300)  NULL,       -- 逗號分隔 3~8 個，jira_log 時由 AI 提供

    -- ── 訂正/取代（同單修正結論）─────────────────────────
    SUPERSEDES_ID  INT            NULL        -- 本筆取代哪一筆（僅允許同 JIRA_KEY，MCP 把關）
        CONSTRAINT FK_HRMSJIRA_SUPERSEDES REFERENCES dbo.HRMS_JIRA(ID),

    -- ── 溯源 ─────────────────────────────────────────────
    CREATED_BY     NVARCHAR(100)  NOT NULL,   -- Jira 帳號（ziping.zhou），非 Windows 帳號
    CREATED_AT     DATETIME2(0)   NOT NULL
        CONSTRAINT DF_HRMSJIRA_CRE DEFAULT SYSDATETIME(),

    META           NVARCHAR(MAX)  NULL        -- JSON 擴充欄（預留，如來源 skill 名稱）
);

CREATE NONCLUSTERED INDEX IX_HRMSJIRA_KEY        ON dbo.HRMS_JIRA (JIRA_KEY);
CREATE NONCLUSTERED INDEX IX_HRMSJIRA_KIND       ON dbo.HRMS_JIRA (ISSUE_KIND);
CREATE NONCLUSTERED INDEX IX_HRMSJIRA_SUPERSEDES ON dbo.HRMS_JIRA (SUPERSEDES_ID)
    WHERE SUPERSEDES_ID IS NOT NULL;
END
GO

------------------------------------------------------------------
-- ② 有效紀錄 View（排除被取代者；人工/報表查詢用）
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.vwHRMS_JIRA_ACTIVE') IS NULL
    EXEC(N'
CREATE VIEW dbo.vwHRMS_JIRA_ACTIVE AS
SELECT j.*
FROM dbo.HRMS_JIRA j
WHERE NOT EXISTS (SELECT 1 FROM dbo.HRMS_JIRA n
                  WHERE n.SUPERSEDES_ID = j.ID)');
GO

------------------------------------------------------------------
-- ③ 最小權限（★ 執行前把 <MCP帳號> 換成實際 database user）
------------------------------------------------------------------
-- GRANT SELECT, INSERT ON dbo.HRMS_JIRA TO [<MCP帳號>];
-- GRANT SELECT ON dbo.vwHRMS_JIRA_ACTIVE TO [<MCP帳號>];
-- 全表 DENY UPDATE/DELETE：本表無命中統計欄，寫入後 MCP 不可竄改任何內容
-- DENY UPDATE ON dbo.HRMS_JIRA TO [<MCP帳號>];
-- DENY DELETE ON dbo.HRMS_JIRA TO [<MCP帳號>];
