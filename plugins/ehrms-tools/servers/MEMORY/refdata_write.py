# -*- coding: utf-8 -*-
"""HRMS_FAQ / HRMS_TASK / HRMS_FAQ_SOP 的 AI 寫入口（faq_log／task_log／sop_log）

共同規則：
- 寫入者一律為 created_by_ai()（如 ziping.zhou(AI)），查詢時標示 🤖 AI 撰寫
- refs（依據）必填，內容尾端附「（AI 撰寫；依據：…）」
- 同表高相似（≥ memory_core.DUP_THRESHOLD）擋下並回傳既有筆，不重複新增
- 帶 id 為更新：只能改 AI 寫的筆，人工資料不可覆蓋
- module／category 須為表內已有的值，不符時回傳候選
"""
import re

import jira_db
import memory_core as mc
import memory_db as mdb
import refdata_db as db

CONF = {
    "faq": {"table": "HRMS_FAQ", "id": "FAQ_ID", "author": "RESPONDENT",
            "touch": "UPDATE_DATE", "label": "FAQ"},
    "task": {"table": "HRMS_TASK", "id": "task_id", "author": "author",
             "touch": "update_date", "label": "Task",
             "now_cols": ("update_date",)},  # NOT NULL 且無預設值
    "sop": {"table": "HRMS_FAQ_SOP", "id": "sop_id", "author": "author",
            "touch": "modified_date", "label": "SOP"},
}

_FOOTER_RE = re.compile(r"\s*（AI 撰寫；依據：.*）\s*$")
_JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9_]*-\d+\b")

SOP_REQUIRED = "## 適用情境"
SOP_BODY = ("## 前置確認", "## 處理方式", "## 需要客戶補的資料")

# 客戶端可能仍是 SQL Server 2008：2012 之後的語法一律不收
_SQL_FORBIDDEN = re.compile(
    r"\b(STRING_AGG|STRING_SPLIT|OPENJSON|JSON_VALUE|JSON_QUERY|JSON_MODIFY|ISJSON|"
    r"TRIM|CONCAT_WS|CONCAT|TRANSLATE|IIF|FORMAT|TRY_CONVERT|TRY_CAST|TRY_PARSE|PARSE|"
    r"EOMONTH|CHOOSE|DATEFROMPARTS|DATETIMEFROMPARTS|LAG|LEAD|FIRST_VALUE|LAST_VALUE)\s*\(",
    re.I)
_SQL_DANGER = re.compile(r"\b(TRUNCATE\s+TABLE|DROP\s+(TABLE|DATABASE|PROCEDURE|VIEW|FUNCTION)|"
                         r"ALTER\s+TABLE|SP_EXECUTESQL)\b|\bEXEC(UTE)?\s*\(", re.I)
_SQL_DML = re.compile(r"\b(UPDATE|DELETE|INSERT)\b", re.I)
# 一個 UPDATE／DELETE 敘述到下一個分號或下一個以敘述關鍵字開頭的行為止
_SQL_STMT_END = re.compile(
    r";|\n\s*(UPDATE|DELETE|INSERT|SELECT|COMMIT|ROLLBACK|BEGIN|GO|IF|DECLARE|PRINT|END|SET\s+IDENTITY_INSERT)\b",
    re.I)


def _strip_sql_comments(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"N?'(?:[^']|'')*'", "''", sql)  # 字串常值內的字不算語法


def check_sql(sql):
    """回傳錯誤清單（空表示通過）。"""
    code = _strip_sql_comments(sql)
    errs = []
    bad = sorted({m.group(1).upper() for m in _SQL_FORBIDDEN.finditer(code)})
    if bad:
        errs.append(f"使用了 SQL Server 2008 不支援的函式：{', '.join(bad)}")
    if _SQL_DANGER.search(code):
        errs.append("含 TRUNCATE／DROP／ALTER TABLE／動態 SQL，不可作為罐頭語法")
    for m in re.finditer(r"\b(UPDATE|DELETE)\b", code, re.I):
        rest = code[m.end():]
        end = _SQL_STMT_END.search(rest)
        stmt = rest[:end.start()] if end else rest
        if not re.search(r"\bWHERE\b", stmt, re.I):
            errs.append(f"{m.group(1).upper()} 敘述缺少 WHERE：{(m.group(0) + stmt).strip()[:60]}")
    if _SQL_DML.search(code):
        if not re.search(r"\bBEGIN\s+TRAN", code, re.I):
            errs.append("有異動資料的敘述，須包在 BEGIN TRAN 內")
        if re.search(r"\bCOMMIT\b", code, re.I):
            errs.append("COMMIT 須註解掉（-- COMMIT），確認筆數無誤後由人工執行")
    return errs


def _with_footer(text, refs):
    return _FOOTER_RE.sub("", text.rstrip()) + f"\n\n（AI 撰寫；依據：{refs.strip()}）"


def _check_choice(value, candidates, label):
    if value in candidates:
        return None
    return f"⚠️ {label}「{value}」不是既有值，請改用其中之一：{'、'.join(candidates)}"


def _check_len(fields):
    for label, value, limit in fields:
        if value and len(value) > limit:
            return f"⚠️ {label} 超過 {limit} 字（目前 {len(value)} 字）。"
    return None


def _dup(key, topic, content, keywords, skip_id=None):
    """同表最相似的一筆（≥ DUP_THRESHOLD 才回傳）。"""
    best_sc, best = 0.0, None
    for r in db.load_all(key):
        if skip_id is not None and r["id"] == skip_id:
            continue
        if key == "faq":
            row = {"topic": r["question"], "content": r["answer"], "keywords": r["keywords"]}
        elif key == "task":
            row = {"topic": r["task_name"], "content": r["scenario"], "keywords": r["keywords"]}
        else:
            row = {"topic": r["title"], "content": r["description"] + " " + r["content"],
                   "keywords": r["keywords"]}
        sc = mc._dup_sim(topic, content, keywords, row)
        if sc > best_sc:
            best_sc, best = sc, r
    return (best_sc, best) if best_sc >= mc.DUP_THRESHOLD else (0.0, None)


def _write(key, values, dup_text, row_id=None):
    """共用寫入流程：查重 → 新增或更新（只限 AI 寫的筆）。"""
    conf = CONF[key]
    label = conf["label"]
    if not db.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，此功能停用。"
    try:
        if row_id is not None:
            author = db.author_of(conf["table"], conf["id"], conf["author"], row_id)
            if author is None:
                return f"⚠️ 找不到 {label} ID {row_id}。"
            if not author.endswith("(AI)"):
                return (f"⚠️ {label} ID {row_id} 是人工資料（{author or '未填寫者'}），AI 不可修改；"
                        f"請在回覆中提出建議，由人工更新。")
        sc, hit = _dup(key, *dup_text, skip_id=row_id)
        if hit:
            who = "AI 撰寫" if (hit.get("author") or "").endswith("(AI)") else "人工"
            hint = (f"若要補充，帶 `id={hit['id']}` 更新該筆。" if who == "AI 撰寫"
                    else "該筆為人工資料，AI 不可修改；如有出入請在回覆中提出。")
            return (f"♻️ 已有相似的 {label} **ID {hit['id']}**（{who}，相似 {sc:.2f}），未新增。\n{hint}")
        values[conf["author"]] = mdb.created_by_ai()
        if row_id is not None:
            db.update_row(conf["table"], conf["id"], row_id, values, conf["touch"])
            return f"✅ 已更新 {label} ID {row_id}（寫入者：{values[conf['author']]}）。"
        new_id = db.insert_row(conf["table"], values, conf.get("now_cols", ()))
        return f"✅ 已新增 {label} ID {new_id}（寫入者：{values[conf['author']]}）。"
    except Exception as e:
        return f"⚠️ {label} 寫入失敗：{e}"


def _parse_id(row_id):
    if row_id in (None, ""):
        return None, None
    try:
        return int(row_id), None
    except (TypeError, ValueError):
        return None, f"⚠️ id 須為數字：{row_id}"


def faq_log(question, answer, module, refs, sub_category="", row_id=None):
    if not (question or "").strip() or not (answer or "").strip():
        return "⚠️ question 與 answer 為必填。"
    if not (refs or "").strip():
        return "⚠️ refs（依據）為必填，例：EHRMSONE-32599、記憶 #15、Wiki 打卡 R7。"
    row_id, err = _parse_id(row_id)
    err = err or _check_len([("sub_category", sub_category, 50)])
    if err:
        return err
    try:
        err = _check_choice(module, db.distinct_values("HRMS_FAQ", "MODULE"), "module")
    except Exception as e:
        return f"⚠️ FAQ DB 連線失敗：{e}"
    if err:
        return err
    values = {"QUESTION": question.strip(), "ANSWER": _with_footer(answer, refs),
              "MODULE": module, "SUB_CATEGORY": sub_category or None}
    return _write("faq", values, (question, answer, sub_category), row_id)


def task_log(task_name, task_description, syntax, notes, module, category, refs,
             related_faq_id=None, row_id=None):
    for label, v in (("task_name", task_name), ("task_description", task_description),
                     ("syntax", syntax), ("notes", notes)):
        if not (v or "").strip():
            return f"⚠️ {label} 為必填（notes 寫執行前要確認的事項與影響範圍）。"
    if not (refs or "").strip():
        return "⚠️ refs（依據）為必填，且須包含已寫入 HRMS_JIRA 的 Jira 單號。"
    row_id, err = _parse_id(row_id)
    err = err or _check_len([("task_name", task_name, 255), ("module", module, 50),
                             ("category", category, 50)])
    if err:
        return err
    sql_errs = check_sql(syntax)
    if sql_errs:
        return "⚠️ syntax 未通過檢查：\n" + "\n".join(f"- {e}" for e in sql_errs)
    try:
        err = (_check_choice(module, db.distinct_values("HRMS_TASK", "module"), "module")
               or _check_choice(category, db.distinct_values("HRMS_TASK", "category"), "category"))
        if err:
            return err
        logged = {r["key"] for r in jira_db.load_all()}
    except Exception as e:
        return f"⚠️ Task DB 連線失敗：{e}"
    keys = set(_JIRA_KEY_RE.findall(refs.upper()))
    if not keys & logged:
        return ("⚠️ refs 須包含已寫入 HRMS_JIRA 的 Jira 單號（AI 寫的語法要能追溯到實際案件）；"
                "請先用 jira_log 記錄該案件。")
    values = {"task_name": task_name.strip(), "task_description": task_description.strip(),
              "syntax": syntax.rstrip(), "notes": _with_footer(notes, refs),
              "module": module, "category": category,
              "related_faq_id": int(related_faq_id) if related_faq_id else None}
    return _write("task", values, (task_name, task_description, f"{module},{category}"), row_id)


def sop_log(title, content, module, refs, keywords="", description="", row_id=None):
    if not (title or "").strip() or not (content or "").strip():
        return "⚠️ title 與 content 為必填。"
    if not (refs or "").strip():
        return "⚠️ refs（依據）為必填，例：EHRMSONE-32599、記憶 #15、Task #548。"
    if SOP_REQUIRED not in content or not any(h in content for h in SOP_BODY):
        return (f"⚠️ content 須依 SOP 模板：要有「{SOP_REQUIRED}」，"
                f"並至少有 {'、'.join(SOP_BODY)} 其中一段（另可有 ## 處理後、## 相關）。")
    row_id, err = _parse_id(row_id)
    err = err or _check_len([("title", title, 200), ("module", module, 50),
                             ("keywords", keywords, 500), ("description", description, 500)])
    if err:
        return err
    try:
        modules = sorted(set(db.distinct_values("HRMS_FAQ_SOP", "module"))
                         | set(db.distinct_values("HRMS_FAQ", "MODULE")))
    except Exception as e:
        return f"⚠️ SOP DB 連線失敗：{e}"
    err = _check_choice(module, modules, "module")
    if err:
        return err
    values = {"title": title.strip(), "content": _with_footer(content, refs), "module": module,
              "keywords": keywords or None, "description": description or None}
    if row_id is None:
        values["is_active"] = 1
    return _write("sop", values, (title, (description or "") + " " + content, keywords), row_id)
