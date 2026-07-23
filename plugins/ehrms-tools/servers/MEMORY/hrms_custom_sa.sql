/* =====================================================================
   HRMS_CUSTOM_SA：客製分支 SA 規格文件索引與深度分析結果
   （請 DBA 於 EHRMS 共用 DB 執行；本檔可重複執行，物件存在即跳過）

   背景：
   - CUSTOM_GIT 每個客製分支下的 SA/ 資料夾存放規格書/確認單/驗收單/安裝單，
     格式混雜（doc/docx/pdf/xls/xlsx）、版本亂（V1.0~V3 等多版共存）、
     只有真的做過版更比對的客戶才有人工整理過的資料，覆蓋率低。
   - 本表由 custom-sa-analyze skill（headless 批次）逐分支掃描寫入：
     所有 SA 文件先做「骨架分類」（DOC_TYPE/VERSION_LABEL/IS_LATEST），
     僅 DOC_TYPE=spec 且 IS_LATEST=1 的文件才進一步深度分析
     （讀取內容含圖片、摘要客製邏輯、與實際程式碼交叉驗證）。

   設計：
   - 一次性建檔性質（EHRMS 已成熟、新客製案件少），非高頻寫入表，
     不採 HRMS_MEMORY 的 append-only/supersede 設計，直接以
     (BRANCH_NAME, DOC_PATH) 唯一鍵 upsert，支援重跑覆蓋更新
   - ANALYZED 區分「只有骨架」vs「已深度分析」；MAPPING_STATUS 記錄
     規格內容是否已驗證對應到實際程式碼
   - 與 HRMS_CUSTOM（元件/檔案層級客製判斷表，另案建置）互補：
     HRMS_CUSTOM_SA 是文件索引，一份文件可能對應多個 HRMS_CUSTOM 元件

   權限原則：
   - 沿用現有 MCP 查詢帳號，不新建登入
   - 僅授予 SELECT / INSERT / UPDATE（無 DELETE，比照其他 HRMS_* 表）
   ===================================================================== */

------------------------------------------------------------------
-- ① SA 文件索引表
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_CUSTOM_SA') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_CUSTOM_SA (
    ID              INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSCUSTOMSA PRIMARY KEY,

    -- ── 客戶識別 ─────────────────────────────────────────
    TAX_ID          NVARCHAR(20)   NOT NULL,   -- 統編（branch 開頭）
    BRANCH_NAME     NVARCHAR(200)  NOT NULL,   -- CUSTOM_GIT 分支名稱

    -- ── 文件識別與骨架分類 ───────────────────────────────
    DOC_PATH        NVARCHAR(500)  NOT NULL,   -- SA 文件在分支內的相對路徑
    DOC_FORMAT      VARCHAR(10)    NOT NULL,   -- doc/docx/pdf/xls/xlsx
    DOC_TYPE        VARCHAR(20)    NULL
        CONSTRAINT CK_HRMSCUSTOMSA_TYPE CHECK (DOC_TYPE IN ('spec','confirm','accept','install','other')),
    VERSION_LABEL   NVARCHAR(50)   NULL,       -- 檔名擷取的版本字串（如 V2.1），擷取不到留空
    IS_LATEST       BIT            NULL,       -- 同一份規格版本家族中是否為最新版（判斷不出來留 NULL）

    -- ── 深度分析結果（僅 spec+IS_LATEST 文件會有值）────────
    ANALYZED        BIT            NOT NULL CONSTRAINT DF_HRMSCUSTOMSA_ANLZ DEFAULT 0,
    SUMMARY         NVARCHAR(MAX)  NULL,       -- 完整敘述式摘要，供人閱讀
    MAPPED_PATHS    NVARCHAR(1000) NULL,       -- 對應到的程式碼路徑，一行一筆（同 HRMS_JIRA.CHANGED_FILES 慣例）
    MAPPING_STATUS  VARCHAR(20)    NOT NULL
        CONSTRAINT DF_HRMSCUSTOMSA_MAP DEFAULT 'unmapped'
        CONSTRAINT CK_HRMSCUSTOMSA_MAP CHECK (MAPPING_STATUS IN ('unmapped','partial','mapped')),
    PARSE_ISSUE     NVARCHAR(500)  NULL,       -- 格式無法解析等例外註記

    -- ── 溯源 ─────────────────────────────────────────────
    CREATED_BY      NVARCHAR(100)  NOT NULL,   -- 執行批次的帳號
    SCANNED_AT      DATETIME2(0)   NOT NULL CONSTRAINT DF_HRMSCUSTOMSA_SCAN DEFAULT SYSDATETIME(),
    BRANCH_COMMIT   NVARCHAR(50)   NULL,       -- 掃描當下的分支 commit SHA

    CONSTRAINT UQ_HRMSCUSTOMSA UNIQUE (BRANCH_NAME, DOC_PATH)  -- 支援 upsert 重跑
);

CREATE NONCLUSTERED INDEX IX_HRMSCUSTOMSA_TAXID ON dbo.HRMS_CUSTOM_SA (TAX_ID);
CREATE NONCLUSTERED INDEX IX_HRMSCUSTOMSA_BRANCH ON dbo.HRMS_CUSTOM_SA (BRANCH_NAME);
END
GO

------------------------------------------------------------------
-- ② 最小權限（★ 執行前把 <MCP帳號> 換成實際 database user）
------------------------------------------------------------------
-- GRANT SELECT, INSERT, UPDATE ON dbo.HRMS_CUSTOM_SA TO [<MCP帳號>];
-- DENY DELETE ON dbo.HRMS_CUSTOM_SA TO [<MCP帳號>];
