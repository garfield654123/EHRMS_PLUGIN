---
name: memory-curate
description: 定期對團隊共用記憶表 HRMS_MEMORY 去蕪存菁：合併重複、訂正矛盾、淘汰過時與從未被引用的記憶。全程 append-only（用 supersede 表達取代，絕不 DELETE）。Use when the user asks to 整理記憶、清理記憶、記憶去蕪存菁、記憶維護、curate memory、檢視記憶品質.
---

# 記憶去蕪存菁（memory-curate）

對 `HRMS_MEMORY` 做定期品質維護。**讀**用 ehrms-database 的 `mssql_query`（可看全表含被取代/rejected），**寫**一律用 ehrms-memory 的 `remember`（supersede 機制），**絕對不下 UPDATE/DELETE 改記憶內容**——內容修改權不屬於 MCP 帳號，物理刪除與 rejected 標記只能由 DBA 執行。

## 步驟 1：撈全表與統計

```sql
-- 全貌
SELECT ID, MEM_TYPE, TOPIC, LEFT(CONTENT,100) AS PREVIEW, KEYWORDS,
       ENTRY_PATH, SUPERSEDES_ID, REF_KEY, INDEX_SHA,
       CREATED_BY, CONVERT(varchar(16), CREATED_AT, 120) AS CREATED_AT,
       HIT_COUNT, CONVERT(varchar(10), LAST_HIT_AT, 120) AS LAST_HIT,
       REVIEW_STATUS
FROM dbo.HRMS_MEMORY ORDER BY MEM_TYPE, TOPIC, ID;

-- 有效記憶（排除被取代者與 rejected）
SELECT * FROM dbo.vwHRMS_MEMORY_ACTIVE ORDER BY MEM_TYPE, TOPIC, ID;
```

注意：`META` 內 `supersedes_all` 可能記載額外被取代的 ID（多筆合併時），判斷有效性時要一併排除。

## 步驟 2：找四類候選（只看有效記憶）

1. **重複**：同 MEM_TYPE 下 TOPIC 相近、或 KEYWORDS 高度重疊、或 CONTENT 講同一件事的多筆。
2. **矛盾**：同主題但結論互斥（不同入口、不同行為描述）——需要查證哪個對（可用 ehrms-codegraph 的 `verify_call_path` 驗證入口是否存在）。
3. **過時**（僅 Engineer 型）：`INDEX_SHA` 落後目前 codegraph 索引版本很多、且內容含行號等易失效細節的——優先重驗。
4. **殭屍**：建立超過 3 個月且 `HIT_COUNT = 0`（從未被 recall 引用）——評估是否還有保留價值。

## 步驟 3：向使用者提出處置清單並確認

列出「群組 → 建議處置（合併/訂正/淘汰/保留）→ 理由」，**經使用者確認後才動手**。

## 步驟 4：執行（一律走 remember 的 supersede）

- **合併 N 筆重複** → 呼叫一次 `remember`：內容取各筆之精華重寫，`supersedes="ID1,ID2,ID3"`（逗號分隔全部被合併者），`source="curate"`。
- **訂正矛盾** → 查證後以正確結論 `remember(supersedes=錯誤筆ID)`。
- **淘汰殭屍/過時** → 若有替代結論，寫入新筆 supersede 舊筆；若純粹該刪（無替代內容），列入「待 DBA 標記 rejected」清單，不要用空殼記憶去 supersede。

## 步驟 5：輸出維護報告

- 處理了幾群、寫入了哪些新 ID、取代了哪些舊 ID
- 「待 DBA 處理」清單：需標 rejected 的 ID（附理由）、建議物理歸檔的範圍
- 表的健康度：有效筆數、兩型比例、平均 HIT_COUNT、零引用比例

## 禁止事項

- 禁止 `UPDATE`/`DELETE` 記憶內容（權限設計上也不允許，別嘗試繞過）
- 禁止在未經使用者確認前執行步驟 4
- 禁止用「猜的」內容合併——合併後的內容只能來自被合併記憶的原文與已驗證的事實
