/* =====================================================================
   CodeGraph 記憶功能：單表 HRMS_MEMORY + 最小權限 + 種子資料
   （請 DBA 於 EHRMS 共用 DB 執行）

   設計：
   - 單表 append-only：內容永不 UPDATE/DELETE；修正=插入新版本，
     程式讀取端同主題取最新一筆
   - MEM_TYPE：episode（問題→入口）/ fact（系統知識）/ anchor（領域錨點）
     / synonym（同義詞）/ case（保留給未來自動匯入的歷史前例，目前不使用）
   - REVIEW_STATUS：pending（預設）/ verified（人工確認，提升可信度）
     / rejected（人工判錯，從檢索剔除）
     ★ review 三欄不授予 MCP 帳號——僅由之後的獨立驗證工具
       （較高權限帳號）或 DBA 維護，MCP 無法自己給記憶蓋章

   權限原則：
   - 沿用現有 MCP 查詢帳號，不新建登入
   - 僅授予本表 SELECT / INSERT ＋ 命中統計兩欄的 UPDATE
   - 明確 DENY DELETE；帳號其餘權限維持不變（唯讀）
   ===================================================================== */

------------------------------------------------------------------
-- ① 記憶表（單表）
------------------------------------------------------------------
IF OBJECT_ID(N'dbo.HRMS_MEMORY') IS NULL
BEGIN
CREATE TABLE dbo.HRMS_MEMORY (
    ID            INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_HRMS_MEMORY PRIMARY KEY,
    MEM_TYPE      VARCHAR(20)    NOT NULL
        CONSTRAINT CK_HRMS_MEMORY_TYPE
        CHECK (MEM_TYPE IN ('episode','fact','anchor','synonym','case')),
    TOPIC         NVARCHAR(200)  NULL,      -- 領域/主題/同義詞的詞
    CONTENT       NVARCHAR(2000) NOT NULL,  -- 問題原文/知識敘述/觸發詞/對應詞
    ENTRY_PATH    NVARCHAR(500)  NULL,      -- 程式入口（fact 可空）
    ENTRY_METHOD  NVARCHAR(500)  NULL,      -- 入口函式
    META          NVARCHAR(MAX)  NULL,      -- JSON：key_tables/skill/note/index_sha...
    HIT_COUNT     INT            NOT NULL CONSTRAINT DF_HRMS_MEMORY_HIT DEFAULT 1,
    REVIEW_STATUS VARCHAR(10)    NOT NULL
        CONSTRAINT DF_HRMS_MEMORY_REVIEW DEFAULT 'pending'
        CONSTRAINT CK_HRMS_MEMORY_REVIEW
        CHECK (REVIEW_STATUS IN ('pending','verified','rejected')),
    REVIEWED_BY   NVARCHAR(100)  NULL,      -- 人工 review 者
    REVIEWED_AT   DATETIME2(0)   NULL,
    CREATED_BY    NVARCHAR(100)  NULL,      -- 誰記的（Windows 帳號）
    CREATED_AT    DATETIME2(0)   NOT NULL CONSTRAINT DF_HRMS_MEMORY_CRE DEFAULT SYSDATETIME(),
    LAST_HIT_AT   DATETIME2(0)   NOT NULL CONSTRAINT DF_HRMS_MEMORY_HITAT DEFAULT SYSDATETIME()
);
CREATE NONCLUSTERED INDEX IX_HRMS_MEMORY_TYPE ON dbo.HRMS_MEMORY (MEM_TYPE) INCLUDE (TOPIC);
END
GO

------------------------------------------------------------------
-- ② 最小權限：授予「現有 MCP 查詢帳號」，其餘權限不動
--    ★ 執行前請把 <MCP帳號> 換成實際的 database user 名稱
------------------------------------------------------------------
GRANT SELECT, INSERT ON dbo.HRMS_MEMORY TO [<MCP帳號>];
-- UPDATE 只開放命中統計兩欄：記憶內容與 review 欄位寫入後 MCP 不可竄改
GRANT UPDATE (HIT_COUNT, LAST_HIT_AT) ON dbo.HRMS_MEMORY TO [<MCP帳號>];
-- 明確 DENY DELETE（DENY 優先於任何 GRANT，作為保險絲）
DENY DELETE ON dbo.HRMS_MEMORY TO [<MCP帳號>];
GO

------------------------------------------------------------------
-- ③ 種子資料：把既有 anchors.json 的內容一次性匯入（僅空表時執行）
------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM dbo.HRMS_MEMORY WHERE MEM_TYPE = 'anchor')
BEGIN
    INSERT INTO dbo.HRMS_MEMORY (MEM_TYPE, TOPIC, CONTENT, ENTRY_PATH, ENTRY_METHOD, META, CREATED_BY) VALUES
    ('anchor', N'假勤計算', N'假勤,加班,請假,時數,用餐,休息,彈性工時,彈性,班別,誤餐,遲到,曠職,出勤',
     N'VB/EHRMS/Personnel/Cls/clsAttendance.cls', N'GetAttendance_DataOP_Cal,AddAskLeave,fixLeaveWorktime',
     N'{"key_tables":"FLEXIBLE_MINUTES,IS_FLEXIBLE_LEAVE,DELAY_MEAL_WORKDAY","skill":"假勤計算","note":"假勤/加班/請假時數計算主體"}', N'seed'),
    ('anchor', N'薪資計算', N'薪資,計薪,加班費,勞退,二代健保,投保薪資,扣款,結薪',
     N'VB/EHRMS/Payroll/Cls/clsPAYSalaryCacul.cls', N'Save_Cal,CalOverTime,CalSecondHealth',
     N'{"key_tables":"","skill":"薪資計算","note":"月薪資計算主體"}', N'seed'),
    ('anchor', N'通知寄送', N'通知信,通知,提醒,寄送,寄信,排程,到期通知,發信',
     N'VB/EHRMS/AuotEngine/ClsAutorun_A.cls', N'runNotify_r,doNotify',
     N'{"key_tables":"HRMS_NOTIFY_REFERENCE","skill":"","note":"AutoEngine 排程通知主體"}', N'seed'),
    ('anchor', N'刷卡異常通知', N'刷卡異常,刷卡,卡鐘,打卡,超時出勤',
     N'VB/EHRMS/AuotEngine/ClsAutorun_A.cls', N'doNotify_CARD_DATA_MATCH,doNotify_CARD_DATA_MATCH_OT',
     N'{"key_tables":"HRMS_NOTIFY_REFERENCE","skill":"","note":"SNR29 刷卡異常通知"}', N'seed'),
    ('anchor', N'保險加退保', N'勞保,健保,職災,加保,退保,停保,投保,眷屬',
     N'VB/EHRMS/Payroll_Insurance/Cls/clsPAYInsurance.cls', N'',
     N'{"key_tables":"HRMS_EMPLOYEE_LABOR,HRMS_EMPLOYEE_HEALTH","skill":"","note":"各類保險加退保"}', N'seed'),
    ('anchor', N'育嬰留停設定', N'育嬰留停,育嬰,留停設定,適用對象',
     N'EHRMS/Personnel/PELSystemSetting/PELSysSet_Parameter/Parameter_Stop_Parental_index.asp', N'clsParameter',
     N'{"key_tables":"HRMS_STOP_PARENTAL_SET","skill":"","note":"育嬰留停設定檔頁面"}', N'seed'),
    ('anchor', N'訂餐福利', N'訂餐,餐廳,菜單,便當,叫餐,用餐刷卡,餐費',
     N'VB/EHRMS/Payroll/Cls/clsPayDineCard.cls', N'GetMealDataList,SaveMealParameter',
     N'{"key_tables":"","skill":"","note":"員工訂餐/用餐刷卡(與假勤用餐時段不同!)"}', N'seed'),
    ('anchor', N'權限管理', N'權限,後台管理權限,功能權限,角色,群組權限,授權,可使用功能,選單權限,登入權限,管理權限',
     N'VB/EHRMS/Authority/Cls/AuthorityItemClass.cls',
     N'LoadFuncItem,LoadMenu,LoadFunction（權限判斷）/ ClsAuthority_Group.GetGroupList,SaveSysGroup（權限設定SF0730）',
     N'{"key_tables":"HRMS_SYS_GROUP,HRMS_SYS_FUNCTION,HRMS_SYS_ITEM,HRMS_SYS_MEMBER","skill":"","note":"權限判斷=Authority.AuthorityItemClass + LoginChk.LoginRightChk；權限設定=SF0730 ClsAuthority_Group"}', N'seed');
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.HRMS_MEMORY WHERE MEM_TYPE = 'synonym')
BEGIN
    INSERT INTO dbo.HRMS_MEMORY (MEM_TYPE, TOPIC, CONTENT, CREATED_BY) VALUES
    ('synonym', N'打卡',   N'刷卡',     N'seed'),
    ('synonym', N'上下班', N'刷卡',     N'seed'),
    ('synonym', N'補休',   N'加班',     N'seed'),
    ('synonym', N'特休',   N'假勤',     N'seed'),
    ('synonym', N'留停',   N'留職停薪', N'seed'),
    ('synonym', N'卡鐘',   N'刷卡',     N'seed');
END
GO

------------------------------------------------------------------
-- ④ 參考：之後「記憶驗證工具」的 review 操作範例（由高權限帳號執行）
------------------------------------------------------------------
-- 人工確認記憶正確（提升可信度）：
--   UPDATE dbo.HRMS_MEMORY SET REVIEW_STATUS='verified',
--          REVIEWED_BY=N'reviewer', REVIEWED_AT=SYSDATETIME() WHERE ID = @id;
-- 人工判定記憶錯誤（從檢索剔除，資料保留供稽核）：
--   UPDATE dbo.HRMS_MEMORY SET REVIEW_STATUS='rejected',
--          REVIEWED_BY=N'reviewer', REVIEWED_AT=SYSDATETIME() WHERE ID = @id;
