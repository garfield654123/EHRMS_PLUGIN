# EHRMS 知識庫使用說明（ehrms-tools）

處理 EHRMS 問題（Jira 維運單、規則詢問、除錯）時，依本說明查知識與寫回。

工作流程依 skill：處理維運單 → `crisis-triage`；修改程式、上版 → `bug-fix`。

## 知識來源

| 想知道 | 來源 | 工具 |
|---|---|---|
| 系統為什麼這樣（原則、大規則、選單、程式在哪個資料夾） | Wiki | `wiki_lookup` |
| 程式入口、細節、例外、過往排查結論 | 團隊記憶 | `recall` |
| 過往相似案件的根因與解法 | Jira 結案紀錄 | `jira_lookup` |
| 怎麼回覆客戶（人工整理） | FAQ | `faq_lookup` |
| 已知問題、版本異動 | 公告 | `bulletin_lookup` |
| 客戶需求怎麼處理：前置確認、操作路徑或該套哪個 Task、要請客戶補什麼 | SOP | `sop_lookup` |
| 修改客戶資料的 SQL 範本 | Task 罐頭語法 | `task_lookup` |
| 資料表、欄位、關聯 | 資料字典 | DB MCP `search_tables`、`get_table_columns` |
| Jira 單內容 | Jira | Jira MCP `get_issue` |

## 依需求決定查詢順序

先讀單（描述、最新留言、附件），判斷本次需求，**維運單不等於 bug**：

| 需求 | 查詢 |
|---|---|
| 案件摘要／進度 | 只讀單，整理訴求、進度、下一步 |
| 資料查詢／調整／解鎖 | `sop_lookup`（前置確認與該套哪個 Task）→ `task_lookup` |
| 操作／規則詢問 | `wiki_lookup` → `faq_lookup`（或 `sop_lookup` 取操作路徑）→ `recall` |
| 疑似異常 | `recall`＋`jira_lookup`（並行）→ `wiki_lookup` 確認原則 → 資料不足時 `sop_lookup` 列出要請客戶補的資料 → 仍不足才讀程式 |

`wiki_lookup` 先用 query 搜尋，需要原文再用 `page`＋`section`（例：`R7`、`程式地圖`、`上半`）。

## 來源互相矛盾時

有程式碼證據的記憶 ＞ Wiki ＞ FAQ／公告 ＞ 其他。記憶與 Wiki 不符時，以有證據者為準，並在回覆中提醒使用者 Wiki 可能需要修正（由 Wiki 管理者處理）。

## 寫回

- 只記錄**已確認**的結論；推測、尚未驗證的不寫。
- 案件原因或處理依據確認後，用 `jira_log`（先以 `jira_lookup(單號)` 查重，結論改變才帶 `supersedes`）。
- 跨單可通用的系統行為或程式入口，用 `remember`。已有 Wiki 頁的主題只寫 Engineer 型（程式細節），不再寫 System 型。

## 必守限制

- **公司 DB 沒有客戶資料**：維運單不可用 DB MCP 查客戶的公司、員工、假勤、薪資資料；需要佐證時提供 SQL 請客戶端執行。DB MCP 只用來查結構。
- `task_lookup` 的 SQL 只是範本，須人工確認參數與影響範圍，不可自動執行。
- 查單或分析完成後，不自動改程式、執行資料更新或送出 Jira 評論；需要時先取得使用者同意。
