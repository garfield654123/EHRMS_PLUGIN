# EHRMS 專案結構

```
/EHRMS              - 前端 ASP 網頁及 JavaScript
  /Personnel        - 人事管理模組
  /Payroll          - 薪資管理模組
  /Payroll_cn       - 薪資管理模組（中文版）
  /Training         - 訓練管理模組
  /Benefit          - 福利管理模組
  /PromotePerform   - 升遷績效模組
  /Resource         - 資源管理模組
  /Remind           - 提醒功能模組
  /ehrPortal        - 員工入口網站（前端）
  /ehrPortalAdmin   - 管理入口網站
  /eHRSystemSetting - 系統設定模組
  /eReport          - 報表系統
  /Report           - 報表檔案（.rpt）
  /ajaxed           - AJAX 類別庫
  /function         - 共用函式庫
  /Library          - 共用程式庫（.lbi）
  /ERRORLOG         - 錯誤日誌目錄
  /Upload           - 上傳檔案目錄
  /Help             - 說明文件
  /MailService      - 郵件服務（.NET）

/VB                 - 後端 VB6 程式
  /EHRMS            - 主要系統 VB6 類別（.cls）及模組（.bas）
  /PORTAL           - 入口網站 VB6 類別及模組

/script             - 資料庫腳本
  /StoredProcedure  - Stored Procedure（.SQL）
  /View             - View 定義（.VIW）
  /Function         - User Defined Function（.UDF）
  /table            - Table Schema

/SQL                - SQL 腳本檔案

/NETSource          - .NET 專案原始碼
  /EHR_NET          - EHRMS .NET 專案
  /EmailSender      - 郵件發送服務
  /MailOAuthService - 郵件 OAuth 服務
```

## 資料庫表格

- `hrms_*` - 主要系統表格（約 925 個）
- `portal_*` - 入口網站表格（約 174 個）
- `ZZ_*` - 客製功能表格（非標準功能）
