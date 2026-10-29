# EHRMS 搜尋技巧與命名規則

## 檔案命名規則

### ASP 檔案
- 模式：`[模組]_[功能]_[動作].asp`
- 範例：`Attendance_DataOP_Export_Step1.asp`
  - `Attendance`：考勤模組
  - `DataOP`：資料作業
  - `Export`：匯出功能
  - `Step1`：步驟 1

### VB6 類別
- 模式：`cls[模組名稱].cls`
- 範例：`clsAttendance.cls`、`clsPayroll.cls`

### Stored Procedure
- 模式：`SP_[模組]_[功能]_[動作]`
- 範例：`SP_ATTENDANCE_CAL_HOURS`

## 常用搜尋關鍵字

### 功能類型
| 類型 | 關鍵字 |
|------|--------|
| 新增 | `Insert`, `Add`, `Create`, `New` |
| 修改 | `Update`, `Modify`, `Edit`, `Change` |
| 刪除 | `Delete`, `Remove`, `Drop` |
| 查詢 | `Select`, `Get`, `Query`, `Search`, `List` |

### 模組關鍵字
| 模組 | 關鍵字 |
|------|--------|
| 人事 | `Personnel`, `Employee`, `HRMS_EMPLOYEE` |
| 考勤 | `Attendance`, `Leave`, `OT`, `HRMS_ATTENDANCE` |
| 薪資 | `Payroll`, `Salary`, `HRMS_PAYROLL` |
| 訓練 | `Training`, `Course`, `HRMS_TRAINING` |
| 福利 | `Benefit`, `Welfare`, `HRMS_BENEFIT` |

## 搜尋範圍建議

### 快速搜尋（優先）
```
/EHRMS/[已知模組]/*.asp
/VB/EHRMS/cls[模組名稱].cls
/script/StoredProcedure/SP_[模組]*.SQL
```

### 廣泛搜尋（找不到時）
```
/EHRMS/**/*.asp
/VB/**/*.cls
/script/**/*.SQL
```

### 共用程式搜尋
```
/EHRMS/function/*.asp
/EHRMS/Library/*.lbi
/VB/EHRMS/mod*.bas
```
