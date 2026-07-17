---
name: weekly-report
description: This skill should be used when the user asks to "產出週會報告", "整理本週jira單", "幫我做週報", "generate weekly report", "週會資料整理", or wants to compile Jira tickets into a weekly meeting table. Fetches Jira tickets where Engineer = ziping.zhou for the current week and generates a structured meeting report row.
version: 0.1.0
---

# EHRMS 週會報告產生器

從 Jira 撈取本週維運單，協助整理成週會報告表格中的個人欄位。

## 報告格式

週會報告為一張表格，每人一行，欄位如下：

| 欄位 | 說明 |
|------|------|
| 員工 | 姓名（Ziping） |
| 維運單（Bug/查詢/改/資料/需求/環境） | 各類型票數，由使用者確認分類 |
| 總數(本週) | 本週 Engineer 為我的 Jira 單總數 |
| 模組分類 | 依 Jira 標題關鍵字對應模組，格式如 `M1:7、育嬰留停:3` |
| 建議 | 使用者手動填入 |
| 前線需釐清 | 使用者手動填入 |
| 分享議題 | 使用者手動填入 |
| AI使用率 | 使用者手動填入（百分比） |
| <7天內 / >7天（In Progress） | 目前狀態為 In Progress 的單，依建立日期區分天數 |

## 執行流程

### 步驟 1：拉取本週 Jira 單

> ⚠️ **精簡模式（必須遵守）**：蒐集資料時一律使用 `get_issue_summary` 取代完整的 `get_issue`，
> 或在 `search_issues` 後僅萃取必要欄位（key、summary、status、created），
> 避免回傳資料量過大導致 token 超出限制。

使用 Jira MCP 查詢：
- Engineer（customfield_13907）= ziping.zhou@104.com.tw
- created 範圍：**上禮拜二 ~ 本週三**（因為週會固定在週三舉行）
  - 計算方式：今天（週三）往回推 8 天 = 上禮拜二
  - 例如今天是 2026-04-15（三），start = 2026-04-07（上週二）
- 排序：created DESC

```
project = EHRMSONE AND "customfield_13907" = "ziping.zhou@104.com.tw" AND created >= "YYYY-MM-DD" ORDER BY created DESC
```

> 若使用者有指定日期範圍，以使用者指定為準。

### 步驟 2：列出所有票並請使用者分類

將每張票列出，格式如下：

```
[序號] EHRMSONE-XXXXX | 狀態 | 建立日期 | 標題
```

請使用者針對每張票指定類型：Bug / 查詢 / 改 / 資料 / 需求 / 環境

可以批次確認，例如：「1,3,5 是查詢，2 是 Bug，4 是改」

### 步驟 3：自動計算

1. **總數**：票的總筆數
2. **各類型票數**：依使用者確認的分類加總
3. **In Progress <7天 / >7天**：
   - 篩選狀態為 In Progress 的票
   - 計算今天 - created 的天數
   - < 7天：天數 < 7
   - ≥ 7天：天數 >= 7
4. **模組分類**：依 `references/ziping-modules.md` 中的關鍵字規則對應，列出每個模組的票數

### 步驟 4：詢問手動欄位

逐一詢問：
- AI使用率（例如：20%）
- 建議（可留空）
- 前線需釐清（可留空）
- 分享議題（可留空）

### 步驟 5：輸出報告列

以 Markdown 表格輸出 Ziping 的完整報告列，方便複製貼上：

```
| Ziping | {Bug} | {查詢} | {改} | {資料} | {需求} | {環境} | {AI%} | {模組分類} | {建議} | {前線需釐清} | {分享議題} | {<7天} | {>7天} | 0 | 0 |
```

## 注意事項

- 今天日期以系統提供的 currentDate 為準
- 預設統計區間：**上禮拜二 ~ 本週三**（週會在週三開）
  - 若今天是週三：start = 今天 - 8天，end = 今天
  - 若今天不是週三（臨時查詢）：start = 最近的上週二，end = 最近的週三
- 若使用者在詢問前已提供部分資訊（如 AI使用率），直接套用不再重複詢問
- 模組分類若無法自動判斷，列出「無法分類」的票請使用者手動指定
- 最後兩欄（第二組 <7天 / >7天）固定為 0 / 0，如有特殊情況使用者自行說明

## 相關資源

- **`references/ziping-modules.md`** - Ziping 的模組關鍵字對應規則
