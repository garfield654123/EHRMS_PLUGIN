/* =====================================================================
   HRMS_CUSTOM：客製分支檔案層級深度分析（客製類型判定＋邏輯說明）
   （請 DBA 於 EHRMS 共用 DB 執行；本檔可重複執行，物件存在即跳過）

   背景：
   - 與 HRMS_CUSTOM_SA（SA 規格文件索引）分工：本表以「檔案」為單位，
     SA 規格書是增補來源（找得到才填 SA_DOC_PATH），不是必要依據——
     即使規格書遺失/從未存在，也要能靠程式碼本身（含跟標準版比對）
     正確判斷是否客製、客製了什麼。
   - 兩階段掃描產生本表資料：
     階段1（便宜、全面）：ZZ_ 前綴／非標準 ProgID 規則，篩出候選客製檔案清單
     階段2（貴、深入）：對候選檔案深讀＋跟標準版（STD_BASELINE 分支）語意比對，
       判定 CUSTOM_TYPE、寫詳細 DESCRIPTION，並嘗試比對 HRMS_CUSTOM_SA 找對應規格書
   - CUSTOM_TYPE 三分類（比照 custom_compare 第1層判讀，多一種 version_lag）：
       standard    = 標準客製（有標準版對應，邏輯確實不同）
       pure        = 純客製（該客戶專屬功能，通常在 plugin 目錄下，無標準版對應；有少數例外）
       version_lag = 非真客製，只是標準版舊拷貝、版本落後（假陽性，篩選候選時常見）
   - STD_BASELINE：本次比對用的標準版分支（EHRMS_GIT，如 '202607_000'）。
     ⚠️ 此基準每半年會更新一次，寫在資料列而非寫死在程式邏輯裡，
     半年後換版，舊資料仍可追溯是用哪個基準比對出來的，需要時才重掃。
   - SA_DOC_PATH：找到對應規格書時填 HRMS_CUSTOM_SA.DOC_PATH，並同步回填該筆
     HRMS_CUSTOM_SA.MAPPED_PATHS（雙向關聯，兩表互相對得起來）。

   設計：
   - 一次性建檔性質，非高頻寫入表，不採 HRMS_MEMORY 的 append-only/supersede 設計，
     直接以 (BRANCH_NAME, CUSTOM_PATH) 唯一鍵 upsert，支援重跑覆蓋更新

   權限原則：
   - 沿用現有 MCP 查詢帳號，不新建登入
   - 僅授予 SELECT / INSERT / UPDATE（無 DELETE，比照其他 HRMS_* 表）
   ===================================================================== */

------------------------------------------------------------------
-- ① 客製檔案深度分析表
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_CUSTOM') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_CUSTOM (
    ID              INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMSCUSTOM PRIMARY KEY,

    -- ── 客戶識別 ─────────────────────────────────────────
    COMPANY_SNO     NVARCHAR(20)   NOT NULL,   -- 公司統編
    BRANCH_NAME     NVARCHAR(200)  NOT NULL,   -- CUSTOM_GIT 分支名稱

    -- ── 檔案識別 ─────────────────────────────────────────
    CUSTOM_PATH     NVARCHAR(500)  NOT NULL,   -- 客製版檔案路徑（CUSTOM_GIT 內）
    STANDARD_PATH   NVARCHAR(500)  NULL,       -- 標準版對應路徑；純客製無標準對應則留空

    -- ── 分類與詳細內容 ───────────────────────────────────
    CUSTOM_TYPE     VARCHAR(20)    NOT NULL
        CONSTRAINT CK_HRMSCUSTOM_TYPE CHECK (CUSTOM_TYPE IN ('standard','pure','version_lag')),
    DESCRIPTION     NVARCHAR(MAX)  NULL,       -- 詳細記錄客製了什麼邏輯；version_lag 可留空或記落後說明

    -- ── 與 SA 規格書的關聯（增補，非必要來源）───────────────
    SA_DOC_PATH     NVARCHAR(500)  NULL,       -- 對應 HRMS_CUSTOM_SA.DOC_PATH；找不到留空

    -- ── 比對基準 ─────────────────────────────────────────
    STD_BASELINE    NVARCHAR(50)   NOT NULL,   -- 本次比對用的標準版分支，如 '202607_000'

    -- ── 來源與新鮮度 ─────────────────────────────────────
    SOURCE          VARCHAR(30)    NOT NULL CONSTRAINT DF_HRMSCUSTOM_SRC DEFAULT 'deep_scan',
    SCANNED_AT      DATETIME2(0)   NOT NULL CONSTRAINT DF_HRMSCUSTOM_SCAN DEFAULT SYSDATETIME(),
    BRANCH_COMMIT   NVARCHAR(50)   NULL,       -- 掃描當下的分支 commit SHA

    CONSTRAINT UQ_HRMSCUSTOM UNIQUE (BRANCH_NAME, CUSTOM_PATH)  -- 支援 upsert 重跑
);

CREATE NONCLUSTERED INDEX IX_HRMSCUSTOM_SNO    ON dbo.HRMS_CUSTOM (COMPANY_SNO);
CREATE NONCLUSTERED INDEX IX_HRMSCUSTOM_BRANCH ON dbo.HRMS_CUSTOM (BRANCH_NAME);
CREATE NONCLUSTERED INDEX IX_HRMSCUSTOM_TYPE   ON dbo.HRMS_CUSTOM (CUSTOM_TYPE);
END
GO

------------------------------------------------------------------
-- ② 最小權限（★ 執行前把 <MCP帳號> 換成實際 database user）
------------------------------------------------------------------
-- GRANT SELECT, INSERT, UPDATE ON dbo.HRMS_CUSTOM TO [<MCP帳號>];
-- DENY DELETE ON dbo.HRMS_CUSTOM TO [<MCP帳號>];
