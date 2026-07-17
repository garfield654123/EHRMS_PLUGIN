# -*- coding: utf-8 -*-
"""HRMS_JIRA 核心邏輯：Jira 維運單結案紀錄

一單一筆有效紀錄的案件檔案櫃（與 HRMS_MEMORY 的跨單泛化知識互補）：
- 寫入 jira_log：同 JIRA_KEY 已有有效紀錄 → 擋下並回傳既有內容（去重）；
  修正結論帶 supersedes=舊ID——僅允許同單號，append-only 永不 UPDATE/DELETE
- 查詢 jira_lookup：單號 → 精確查該單；問題敘述 → 關鍵字相似度檢索
  （評分器重用 memory_core 的中文 bigram＋關鍵字比對）
"""
import re

import jira_db as db
import memory_core as mc
import memory_db as mdb

KINDS = ("Crisis", "Story")
DEFAULT_PROJECT = "EHRMSONE"
LOOKUP_THRESHOLD = 0.30   # 敘述檢索：低於此分不回傳

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")


def normalize_key(s):
    """單號正規化：純數字補預設前綴 EHRMSONE-，統一大寫。非單號格式回 None。"""
    s = (s or "").strip().upper()
    if s.isdigit():
        return f"{DEFAULT_PROJECT}-{s}"
    if _KEY_RE.match(s):
        return s
    return None


def active_rows(rows=None):
    """有效集合：排除被 supersede 取代的舊版本。"""
    rows = rows if rows is not None else db.load_all()
    superseded = {r["supersedes"] for r in rows if r["supersedes"]}
    return [r for r in rows if r["id"] not in superseded]


def _fmt(r, score=None):
    head = f"- **[ID {r['id']}]** {r['key']}（{r['kind']}）【{r['title']}】"
    if score is not None:
        head += f"（相似 {score:.2f}）"
    lines = [head,
             f"  根因：{r['root_cause']}",
             f"  解法：{r['resolution']}"]
    if r["changed_files"]:
        lines.append(f"  修改：{r['changed_files']}")
    lines.append(f"  <sub>{r['created_by']}・{r['created_at']}</sub>")
    return lines


def log_case(jira_key, kind, title, root_cause, resolution,
             changed_files="", keywords="", supersedes=""):
    """寫入一筆結案紀錄（唯一寫入口）。
    - 同單號已有有效紀錄且未帶 supersedes → 擋下並回傳既有內容
    - supersedes=舊ID：修正結論——僅允許指向同一單號的最新有效紀錄"""
    if kind not in KINDS:
        return f"⚠️ kind 只能是 {' / '.join(KINDS)}（Crisis=維運單、Story=改程式/bug fix）。"
    if not (title and root_cause and resolution):
        return "⚠️ title、root_cause、resolution 為必填。"
    if kind == "Story" and not changed_files:
        return "⚠️ Story 紀錄必須提供 changed_files（修改了哪些程式）。"
    key = normalize_key(jira_key)
    if not key:
        return (f"⚠️ jira_key 格式錯誤：{jira_key}"
                f"（可給完整單號或純數字，純數字自動補 {DEFAULT_PROJECT}- 前綴）。")
    if not mdb.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，HRMS_JIRA 功能停用。"

    try:
        rows = db.load_all()
    except Exception as e:
        return f"⚠️ HRMS_JIRA DB 連線失敗：{e}"

    act = [r for r in active_rows(rows) if r["key"] == key]

    sup_id = None
    if supersedes:
        try:
            sup_id = int(str(supersedes).strip())
        except ValueError:
            return f"⚠️ supersedes 需為單一紀錄 ID：{supersedes}"
        target = next((r for r in rows if r["id"] == sup_id), None)
        if not target:
            return f"⚠️ supersedes 指到不存在的紀錄 ID：{sup_id}，請先用 jira_lookup 確認。"
        if target["key"] != key:
            return (f"⚠️ supersedes 只能指向同一單號的紀錄："
                    f"ID {sup_id} 屬於 {target['key']}，本筆是 {key}。")
        if not any(r["id"] == sup_id for r in act):
            return (f"⚠️ ID {sup_id} 已被更新版本取代，"
                    f"請 supersede 最新有效紀錄（用 jira_lookup 查 {key}）。")
    elif act:
        cur = act[0]
        return ("♻️ 此單已有結案紀錄，未寫入：\n"
                + "\n".join(_fmt(cur)) + "\n"
                + f"若本次是**修正結論** → 帶 `supersedes={cur['id']}` 重新呼叫；"
                + "內容相同則無需再寫。")

    try:
        new_id = db.insert_case(key, kind, title, root_cause, resolution,
                                changed_files, keywords, supersedes_id=sup_id)
    except Exception as e:
        return (f"⚠️ HRMS_JIRA 寫入失敗：{e}\n"
                f"請確認 HRMS_JIRA 資料表已建立，且帳號有 INSERT 權限。")

    out = [f"✅ 已記錄（{kind}，紀錄 ID **{new_id}**）{key}【{title}】"]
    if sup_id:
        out.append(f"↺ 已取代舊紀錄 ID {sup_id}（檢索不再回傳舊結論）")
    out.append(f"記錄者：{mdb.created_by()}")
    return "\n".join(out)


def lookup(query, kind=None, top_k=5):
    """查詢結案紀錄。query＝單號（EHRMSONE-XXXXX / 純數字）→ 精確查；
    query＝問題敘述 → 相似案件檢索（回 top_k）。"""
    if kind and kind not in KINDS:
        return f"⚠️ kind 只能是 {' / '.join(KINDS)}。"
    if not mdb.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，HRMS_JIRA 功能停用。"
    try:
        rows = active_rows()
    except Exception as e:
        return f"⚠️ HRMS_JIRA DB 連線失敗：{e}"

    key = normalize_key(query)
    if key:
        hits = [r for r in rows if r["key"] == key]
        if not hits:
            return f"# HRMS_JIRA：{key}\n\n（此單尚無結案紀錄）"
        out = [f"# HRMS_JIRA：{key}"]
        for r in hits:
            out.extend(_fmt(r))
        return "\n".join(out)

    qs = mc._bigrams(query)
    if not qs:
        return "⚠️ 查詢敘述過短，無法比對。"
    scored = []
    for r in rows:
        if kind and r["kind"] != kind:
            continue
        # 借用 memory_core 的評分器：title 當 topic、根因＋解法當 content
        text_row = {"topic": r["title"],
                    "content": r["root_cause"] + " " + r["resolution"],
                    "keywords": r["keywords"], "review": ""}
        sc = mc._score(query, qs, text_row)
        if sc >= LOOKUP_THRESHOLD:
            scored.append((sc, r))
    scored.sort(key=lambda x: -x[0])
    scored = scored[:int(top_k or 5)]
    if not scored:
        return f"# HRMS_JIRA 檢索：{query}\n\n（無相似案件）"
    out = [f"# HRMS_JIRA 檢索：{query}"]
    for sc, r in scored:
        out.extend(_fmt(r, sc))
    return "\n".join(out)
