/* =====================================================================
   FAQ / 公告 / 罐頭語法：人工維護的參考資料（說明文件，本檔不含任何 DDL）

   【重要】本檔原本會建立 HRMS_FAQ / HRMS_BULLETIN / HRMS_TASK 三表與三個
   ACTIVE VIEW。實際 DB 已有既存的同名／近似名稱資料表且結構不同，
   因此改為「MCP 直接查詢原表」，不建立任何 view，也不再建立資料表。
   請勿依舊版內容建表，否則會與既有資料表結構衝突。

   MCP（refdata_db.py）實際查詢的原表與欄位對映：

   faq_lookup       <- dbo.HRMS_FAQ
       FAQ_ID        -> ID
       QUESTION      -> 檢索錨
       ANSWER        -> 解答
       MODULE        -> 分類 (CATEGORY)
       SUB_CATEGORY  -> 關鍵字 (KEYWORDS)
       過濾條件：無（REVIEW_STATUS 目前皆為 pending，不以審核狀態過濾）

   bulletin_lookup  <- dbo.HRMS_FAQ_BULLETIN
       ID / TITLE / CONTENT
       TYPE          -> 分類 (CATEGORY)
       過濾條件：IS_DELETE = 0 AND SHOW = 1（未刪除且顯示中；SHOW=0 代表不顯示）

   task_lookup      <- dbo.HRMS_TASK
       task_id       -> ID
       task_name     -> 情境名稱
       task_description -> 適用情境 (SCENARIO)
       syntax        -> SQL 範本
       notes         -> 注意事項
       module + category -> 關鍵字
       （原表無風險等級欄位，顯示為「未分級」）
       ntext 欄位以 CONVERT(NVARCHAR(MAX), ...) 讀取
       過濾條件：無

   原則：
   - 三表由人工／既有維護流程在 DB 端維護；MCP 只做 SELECT，沒有任何寫入功能。
   - 沿用現有 MCP 查詢帳號，僅需對上述三張原表有 SELECT 權限：
       -- GRANT SELECT ON dbo.HRMS_FAQ          TO [<MCP帳號>];
       -- GRANT SELECT ON dbo.HRMS_FAQ_BULLETIN TO [<MCP帳號>];
       -- GRANT SELECT ON dbo.HRMS_TASK         TO [<MCP帳號>];
   - 對映邏輯的權威來源是 refdata_db.py 內的 TABLES 設定；
     原表結構變動時，請改該處，並同步更新本說明。
   ===================================================================== */