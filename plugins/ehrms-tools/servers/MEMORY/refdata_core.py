# -*- coding: utf-8 -*-
"""HRMS_FAQ / HRMS_BULLETIN / HRMS_TASK 共用查詢邏輯（純查詢，AI 不寫入）

三表皆由人工在 DB 端直接維護（人工檢核過的高精度資料），與 AI 自動累積、
需要去重／supersede／審核狀態的 HRMS_MEMORY、HRMS_JIRA 性質不同：
- 沒有寫入工具，內容由人工直接 INSERT/UPDATE
- 沒有 REVIEW_STATUS：內容本來就是人工先審過才寫入
- 評分器直接重用 memory_core（bigram＋關鍵字比對，作法同 jira_core 對 mc._score 的重用）

task 檢索固定附加提醒：罐頭語法仍須人工確認參數與影響範圍，AI 不可自動執行 SQL。
"""
import memory_core as mc
import refdata_db as db

LOOKUP_THRESHOLD = 0.30

TABLE_CONF = {
    "faq": {"topic_field": "question", "content_fields": ("answer",)},
    "bulletin": {"topic_field": "title", "content_fields": ("content",)},
    "task": {"topic_field": "task_name", "content_fields": ("scenario",)},
}

LABELS = {"faq": "FAQ", "bulletin": "公告", "task": "罐頭語法"}

TASK_DISCLAIMER = ("⚠️ 以上為人工整理之罐頭語法範本，仍須人工確認參數與實際影響範圍後才可執行；"
                    "AI 不可自動執行此 SQL。")


def _fmt(table_key, r, score=None):
    head = f"- **[ID {r['id']}]**"
    if score is not None:
        head += f"（相似 {score:.2f}）"
    if table_key == "faq":
        head += f" {r['question']}"
        if r.get("category"):
            head += f"（{r['category']}）"
        return [head, f"  {r['answer']}"]
    if table_key == "bulletin":
        head += f" {r['title']}"
        if r.get("category"):
            head += f"（{r['category']}）"
        return [head, f"  {r['content']}"]
    # task
    head += f" {r['task_name']}（風險：{r.get('risk_level') or '中'}）"
    lines = [head]
    if r.get("scenario"):
        lines.append(f"  適用情境：{r['scenario']}")
    lines.append(f"  SQL 範本：\n```sql\n{r['sql_template']}\n```")
    if r.get("params"):
        lines.append(f"  參數：{r['params']}")
    if r.get("affected_table"):
        lines.append(f"  影響資料表：{r['affected_table']}")
    if r.get("precautions"):
        lines.append(f"  注意事項：{r['precautions']}")
    return lines


def lookup(table_key, query, category=None, top_k=5):
    """三表共用查詢：query 與各表 topic/content 做 bigram＋關鍵字相似度比對，
    回傳 top_k 筆（依相似度排序）。category 可選（限定分類）。"""
    if table_key not in TABLE_CONF:
        return f"⚠️ 未知的資料類型：{table_key}"
    if not db.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，此功能停用。"

    conf = TABLE_CONF[table_key]
    try:
        rows = db.load_all(table_key)
    except Exception as e:
        return f"⚠️ {LABELS[table_key]} DB 連線失敗：{e}"

    qs = mc._bigrams(query)
    if not qs:
        return "⚠️ 查詢敘述過短，無法比對。"

    scored = []
    for r in rows:
        if category and (r.get("category") or "").strip() != category.strip():
            continue
        topic = r.get(conf["topic_field"], "") or ""
        content = " ".join(r.get(f, "") or "" for f in conf["content_fields"])
        text_row = {"topic": topic, "content": content,
                    "keywords": r.get("keywords", ""), "review": ""}
        sc = mc._score(query, qs, text_row)
        if sc >= LOOKUP_THRESHOLD:
            scored.append((sc, r))
    scored.sort(key=lambda x: -x[0])

    label = LABELS[table_key]
    out = [f"# {label}檢索：{query}"]
    if not scored:
        out.append(f"\n（無相符{label}）")
        return "\n".join(out)

    top_k = int(top_k or 5)
    for sc, r in scored[:top_k]:
        out.extend(_fmt(table_key, r, sc))
    if table_key == "task":
        out.append(f"\n{TASK_DISCLAIMER}")
    return "\n".join(out)
