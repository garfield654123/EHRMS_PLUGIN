# -*- coding: utf-8 -*-
"""HRMS_FAQ / HRMS_FAQ_BULLETIN / HRMS_TASK / HRMS_FAQ_SOP 共用查詢邏輯

內容以人工在 DB 端維護為主（人工檢核過的高精度資料）：
- FAQ／Task／SOP 另有 AI 寫入口（refdata_write），寫入者為「使用者(AI)」，查詢時標示 🤖 AI 撰寫
- 原表雖有 REVIEW_STATUS 欄位，但目前皆為 pending，故查詢時不以審核狀態過濾；引用時不可當作已審核資料
- 評分器直接重用 memory_core（bigram＋關鍵字比對，作法同 jira_core 對 mc._score 的重用）

task 檢索固定附加提醒：罐頭語法仍須人工確認參數與影響範圍，AI 不可自動執行 SQL。

version_bulletins 不做相似度篩選：版更公告筆數少、內容常只有一句話，用相似度容易漏掉，
所以整批列出交給 AI 比對症狀。
"""
import re

import memory_core as mc
import refdata_db as db

LOOKUP_THRESHOLD = 0.30

TABLE_CONF = {
    "faq": {"topic_field": "question", "content_fields": ("answer",)},
    "bulletin": {"topic_field": "title", "content_fields": ("content",)},
    "task": {"topic_field": "task_name", "content_fields": ("scenario",)},
    "sop": {"topic_field": "title", "content_fields": ("description", "content")},
}

LABELS = {"faq": "FAQ", "bulletin": "公告", "task": "罐頭語法", "sop": "SOP"}

AI_TAG = " 🤖 AI 撰寫"
AI_TASK_WARNING = "  ⚠️ AI 撰寫的語法，尚未經人工實際執行驗證，套用前須逐行確認。"

TASK_DISCLAIMER = ("⚠️ 以上為人工整理之罐頭語法範本，仍須人工確認參數與實際影響範圍後才可執行；"
                    "AI 不可自動執行此 SQL。")

# 版更相關的公告類型（TYPE 欄位目前中英文寫法並存）
VERSION_TYPES = {"版本更新", "version update", "規格說明"}
VERSION_MAX_CHARS = 2500
VERSION_HINT = ("比對方式：症狀、功能、錯誤訊息對得上才算已知版更問題；對得上時說明公告的更新程式、"
                "修正版號或適用版本，公告有客戶名單就核對客戶在不在名單內，並提醒確認客戶目前版本"
                "（不要自行斷定客戶已更新或未更新）。")


def is_ai(r):
    return (r.get("author") or "").endswith("(AI)")


def _fmt(table_key, r, score=None):
    head = f"- **[ID {r['id']}]**"
    if score is not None:
        head += f"（相似 {score:.2f}）"
    if is_ai(r):
        head += AI_TAG
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
    if table_key == "sop":
        head += f" {r['title']}"
        if r.get("category"):
            head += f"（{r['category']}）"
        return [head, r["content"].strip()]
    # task
    head += f" {r['task_name']}（風險：{r.get('risk_level') or '中'}）"
    lines = [head]
    if is_ai(r):
        lines.append(AI_TASK_WARNING)
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
    """共用查詢：query 與各表 topic/content 做 bigram＋關鍵字相似度比對，
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


def version_bulletins():
    """列出全部有效的版更相關公告（新到舊），供比對使用者回報的問題是不是已知版更問題。"""
    if not db.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，此功能停用。"
    try:
        rows = db.load_all("bulletin")
    except Exception as e:
        return f"⚠️ 公告 DB 連線失敗：{e}"

    rows = [r for r in rows if (r.get("category") or "").strip() in VERSION_TYPES]
    if not rows:
        return "目前沒有有效的版更公告。"
    rows.sort(key=lambda r: r.get("created") or "", reverse=True)

    out = [f"# 版更公告（共 {len(rows)} 筆，HRMS_FAQ_BULLETIN，新到舊）", VERSION_HINT]
    for r in rows:
        # 公告內的圖片是 FAQ_web 的站內路徑，AI 讀不到，換成標記即可
        content = re.sub(r"!\[[^\]]*\]\([^)]*\)", "[圖片]", r.get("content") or "").strip()
        if len(content) > VERSION_MAX_CHARS:
            content = content[:VERSION_MAX_CHARS] + "…（內容過長已截斷）"
        out.append(f"\n## 公告 #{r['id']}｜{r['title']}（{r.get('category')}，{r.get('created')}）\n{content}")
    return "\n".join(out)
