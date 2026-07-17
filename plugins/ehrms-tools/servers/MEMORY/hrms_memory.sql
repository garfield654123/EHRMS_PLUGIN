/* =====================================================================
   ehrms-memory：團隊共用記憶表 HRMS_MEMORY（v2 重新設計）
   （請 DBA 於 EHRMS 共用 DB 執行；本檔可重複執行，物件存在即跳過）

   設計：
   - 兩型記憶 MEM_TYPE：
       System   = 系統使用層知識（客服視角：操作順序、前置條件、功能行為）
       Engineer = 程式入口與邏輯要點（維運視角：功能→入口、排查筆記）
   - append-only：內容永不 UPDATE/DELETE；
       訂正 = 插入新記錄並以 SUPERSEDES_ID 指向被取代的舊記錄，
       讀取端（vwHRMS_MEMORY_ACTIVE）自動排除被取代者
   - 記錄者 CREATED_BY 與 Jira 帳號一致（如 ziping.zhou），由 MCP 填入
   - HIT_COUNT/LAST_HIT_AT 由 recall 命中回寫（使用即強化），預設 0
   - REVIEW_STATUS 為人工抽查保險絲（pending/verified/rejected），
     三個 REVIEW_* 欄位不授予 MCP 帳號——僅由 DBA 或高權限工具維護

   權限原則：
   - 沿用現有 MCP 查詢帳號，不新建登入
   - 僅授予 SELECT / INSERT ＋ HIT_COUNT,LAST_HIT_AT 兩欄 UPDATE
   - 明確 DENY DELETE

   舊表（v1）處置：
   - v1 版 HRMS_MEMORY 已改名為 HRMS_MEMORY_V1_BAK（2026-07-16），
     騰出 HRMS_MEMORY 名稱給本 v2 表；程式不再讀寫 v1 表。
   - v1 的 anchor/synonym 資料已移至 plugin 內 git 版控的 anchors.json；
     episode/fact 存量僅 10 筆，如需保留請人工挑選後改寫入本表
     （episode→Engineer、fact→依內容判 System/Engineer，TOPIC 必填）。
   - HRMS_MEMORY_V1_BAK 確認不再需要後由 DBA 歸檔或 DROP。
   ===================================================================== */

------------------------------------------------------------------
-- ① 記憶表
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_MEMORY') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_MEMORY (
    ID             INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSMEM PRIMARY KEY,

    -- ── 記憶本體 ─────────────────────────────────────────
    MEM_TYPE       VARCHAR(10)    NOT NULL
        CONSTRAINT CK_HRMSMEM_TYPE CHECK (MEM_TYPE IN ('System','Engineer')),
    TOPIC          NVARCHAR(100)  NOT NULL,   -- 主題（聚合與取代比對的錨），必填
    CONTENT        NVARCHAR(2000) NOT NULL,   -- System=知識敘述；Engineer=入口說明＋邏輯要點
    KEYWORDS       NVARCHAR(300)  NULL,       -- 逗號分隔 3~8 個，remember 時由 AI 提供
    FUNC_PATH      NVARCHAR(300)  NULL,       -- UI 功能位置（選單路徑），兩型皆可用
    ENTRY_PATH     NVARCHAR(500)  NULL,       -- 程式入口（Engineer 必填，見 CK_HRMSMEM_ENTRY）
    ENTRY_METHOD   NVARCHAR(300)  NULL,

    -- ── 訂正/取代（一級公民）─────────────────────────────
    SUPERSEDES_ID  INT            NULL        -- 本筆取代哪一筆（訂正=插新筆帶此欄）
        CONSTRAINT FK_HRMSMEM_SUPERSEDES REFERENCES dbo.HRMS_MEMORY(ID),

    -- ── 溯源（provenance）────────────────────────────────
    REF_KEY        NVARCHAR(50)   NULL,       -- 來源案件（EHRMSONE-XXXXX）
    SOURCE         NVARCHAR(50)   NULL,       -- 產生途徑（skill 名 / 'curate'）
    INDEX_SHA      VARCHAR(12)    NULL,       -- Engineer 記憶當下的 codegraph 索引版本
    CREATED_BY     NVARCHAR(100)  NOT NULL,   -- Jira 帳號（ziping.zhou），非 Windows 帳號
    CREATED_AT     DATETIME2(0)   NOT NULL CONSTRAINT DF_HRMSMEM_CRE DEFAULT SYSDATETIME(),

    -- ── 強化環（recall 命中回寫；MCP 唯二可 UPDATE 的欄位）──
    HIT_COUNT      INT            NOT NULL CONSTRAINT DF_HRMSMEM_HIT DEFAULT 0,
    LAST_HIT_AT    DATETIME2(0)   NULL,

    -- ── 人工抽查（保險絲，非訂正必經之路）──────────────────
    REVIEW_STATUS  VARCHAR(10)    NOT NULL
        CONSTRAINT DF_HRMSMEM_REVIEW DEFAULT 'pending'
        CONSTRAINT CK_HRMSMEM_REVIEW CHECK (REVIEW_STATUS IN ('pending','verified','rejected')),
    REVIEWED_BY    NVARCHAR(100)  NULL,
    REVIEWED_AT    DATETIME2(0)   NULL,

    META           NVARCHAR(MAX)  NULL,       -- JSON 擴充欄（如 supersedes_all=[多筆取代]）

    -- 型別完整性：Engineer 記憶必須有程式入口，System 不強制
    CONSTRAINT CK_HRMSMEM_ENTRY
        CHECK (MEM_TYPE = 'System' OR ENTRY_PATH IS NOT NULL)
);

CREATE NONCLUSTERED INDEX IX_HRMSMEM_TYPE_TOPIC ON dbo.HRMS_MEMORY (MEM_TYPE, TOPIC);
CREATE NONCLUSTERED INDEX IX_HRMSMEM_SUPERSEDES ON dbo.HRMS_MEMORY (SUPERSEDES_ID)
    WHERE SUPERSEDES_ID IS NOT NULL;
CREATE NONCLUSTERED INDEX IX_HRMSMEM_REF        ON dbo.HRMS_MEMORY (REF_KEY)
    WHERE REF_KEY IS NOT NULL;
END
GO

------------------------------------------------------------------
-- ② 有效記憶 View（人工/報表查詢用；排除 rejected 與被取代者）
--    注意：MCP 端另在程式內計算有效集合（含 META.supersedes_all 多筆取代），
--    本 View 涵蓋主鏈（SUPERSEDES_ID），供 DBA/去蕪存菁快速檢視。
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.vwHRMS_MEMORY_ACTIVE') IS NULL
    EXEC(N'
CREATE VIEW dbo.vwHRMS_MEMORY_ACTIVE AS
SELECT m.*
FROM dbo.HRMS_MEMORY m
WHERE m.REVIEW_STATUS <> ''rejected''
  AND NOT EXISTS (SELECT 1 FROM dbo.HRMS_MEMORY n
                  WHERE n.SUPERSEDES_ID = m.ID
                    AND n.REVIEW_STATUS <> ''rejected'')');
GO

------------------------------------------------------------------
-- ③ 最小權限（★ 執行前把 <MCP帳號> 換成實際 database user）
------------------------------------------------------------------
-- GRANT SELECT, INSERT ON dbo.HRMS_MEMORY TO [<MCP帳號>];
-- GRANT SELECT ON dbo.vwHRMS_MEMORY_ACTIVE TO [<MCP帳號>];
-- UPDATE 只開放命中統計兩欄：內容與 REVIEW_* 寫入後 MCP 不可竄改
-- GRANT UPDATE (HIT_COUNT, LAST_HIT_AT) ON dbo.HRMS_MEMORY TO [<MCP帳號>];
-- 明確 DENY DELETE（DENY 優先於任何 GRANT，作為保險絲）
-- DENY DELETE ON dbo.HRMS_MEMORY TO [<MCP帳號>];
