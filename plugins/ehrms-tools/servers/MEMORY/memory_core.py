# -*- coding: utf-8 -*-
"""ehrms-memory 核心邏輯

兩型記憶（MEM_TYPE）：
- System   ：系統使用層知識（客服視角）——操作順序、前置條件、功能行為
- Engineer ：程式入口與邏輯要點（維運視角）——功能→入口對應、排查筆記

四條迴路在本模組的落點：
- 寫入   remember：server 端確定性去重（高相似同結論→強化不新增；supersedes→訂正）
- 強化   recall  ：命中即回寫 HIT_COUNT / LAST_HIT_AT
- 訂正   remember(supersedes=舊ID)：插新筆取代舊筆，讀取端排除被取代者
- 固化   memory-curate skill 定期去蕪存菁（讀走 DB MCP、寫走本模組的 supersede）
"""
import os
import re
import sqlite3
from difflib import SequenceMatcher

import memory_db as db

KINDS = ("System", "Engineer")
RECALL_THRESHOLD = 0.30   # 檢索：低於此分不回傳
DUP_THRESHOLD = 0.75      # 寫入去重：高於此分視為等價記憶


# ── 文字比對（中文 bigram ＋ 英數 token ＋ 片語加權）────────────

def _bigrams(t):
    g = set()
    for run in re.findall(r'[一-鿿]+', t):
        if len(run) == 1:
            g.add(run)
        for i in range(len(run) - 1):
            g.add(run[i:i + 2])
    for tok in re.findall(r'[A-Za-z0-9_]{2,}', t.lower()):
        g.add(tok)
    return g


def _phrase_bonus(a, b):
    """最長共同連續片語 ≥4 字加權（高鑑別度訊號，防長句稀釋分數）。"""
    m = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    if m.size < 4:
        return 0.0
    return min(0.30, 0.10 + 0.03 * (m.size - 4))


def _kw_list(s):
    return [k.strip() for k in (s or "").split(",") if k.strip()]


def _base(p):
    return (p or "").replace("\\", "/").split("/")[-1].lower()


# ── 有效集合：排除 rejected 與被取代者 ─────────────────────────

def active_rows(rows=None):
    rows = rows if rows is not None else db.load_all()
    superseded = set()
    for r in rows:
        if r["review"] == "rejected":
            continue  # 取代者本身被 reject 時，舊版自動復活
        if r["supersedes"]:
            superseded.add(r["supersedes"])
        for x in r["meta"].get("supersedes_all", []):
            try:
                superseded.add(int(x))
            except (TypeError, ValueError):
                pass
    return [r for r in rows
            if r["review"] != "rejected" and r["id"] not in superseded]


# ── 檢索評分 ─────────────────────────────────────────────────

def _score(question, qs, row):
    """相關性分數：關鍵字（人工濃縮訊號，權重最高）/ 主題覆蓋 / 內容 bigram 三路取最大。"""
    q_lower = question.lower()
    kw = _kw_list(row["keywords"])
    kw_hits = sum(1 for k in kw if k.lower() in q_lower)
    kw_score = min(0.85, 0.40 + 0.15 * (kw_hits - 1)) if kw_hits else 0.0

    ts = _bigrams(row["topic"])
    cs = _bigrams(row["topic"] + " " + row["content"])
    inter = len(qs & cs)
    dice = 2.0 * inter / (len(qs) + len(cs)) if cs else 0.0
    contain = inter / min(len(qs), len(cs)) if cs and min(len(qs), len(cs)) else 0.0
    topic_contain = len(qs & ts) / len(ts) if ts else 0.0

    score = max(dice, contain * 0.8, topic_contain, kw_score)
    score += _phrase_bonus(question, row["topic"] + " " + row["content"])
    if row["review"] == "verified":
        score = min(1.0, score + 0.10)
    return score


def _fmt_row(sc, r):
    lines = []
    head = f"- **[ID {r['id']}]**（相似 {sc:.2f}）【{r['topic']}】"
    if r["type"] == "Engineer" and r["entry_path"]:
        m = f" :: `{r['entry_method']}`" if r["entry_method"] else ""
        head += f" → `{r['entry_path']}`{m}"
    lines.append(head)
    lines.append(f"  {r['content']}")
    prov = [r["created_by"], r["created_at"]]
    if r["ref_key"]:
        prov.append(r["ref_key"])
    if r["func_path"]:
        prov.append(f"功能：{r['func_path']}")
    if r["hits"]:
        prov.append(f"引用 {r['hits']} 次")
    if r["review"] == "verified":
        prov.append("✅verified")
    lines.append(f"  <sub>{'・'.join(prov)}</sub>")
    return lines


def recall(question, kind=None, top_k=3):
    """檢索共用記憶。回傳 System（先懂行為）與 Engineer（再看程式）兩組。"""
    if not db.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，記憶功能停用。"
    if kind and kind not in KINDS:
        return f"⚠️ kind 只能是 {' / '.join(KINDS)}。"
    try:
        rows = active_rows()
    except Exception as e:
        return f"⚠️ 記憶 DB 連線失敗：{e}"
    qs = _bigrams(question)
    if not qs:
        return "⚠️ 問句過短，無法比對。"

    hits = {"System": [], "Engineer": []}
    for r in rows:
        if kind and r["type"] != kind:
            continue
        sc = _score(question, qs, r)
        if sc >= RECALL_THRESHOLD:
            hits[r["type"]].append((sc, r))
    for k in hits:
        hits[k].sort(key=lambda x: -x[0])
        hits[k] = hits[k][:top_k]

    out = [f"# 記憶檢索：{question}"]
    returned_ids = []
    if hits["System"]:
        out.append("\n## 🧭 系統知識（System）")
        for sc, r in hits["System"]:
            out.extend(_fmt_row(sc, r))
            returned_ids.append(r["id"])
    if hits["Engineer"]:
        out.append("\n## 🔧 程式入口（Engineer）")
        for sc, r in hits["Engineer"]:
            out.extend(_fmt_row(sc, r))
            returned_ids.append(r["id"])
    if not returned_ids:
        return f"# 記憶檢索：{question}\n\n（無記憶命中）"

    try:
        db.bump_hits(returned_ids)
    except Exception:
        pass  # 強化迴路失敗不影響檢索結果
    return "\n".join(out)


# ── 寫入管線 ─────────────────────────────────────────────────

def _dup_sim(topic, content, keywords, row):
    """寫入去重相似度：內容 bigram＋片語，與關鍵字 Jaccard 取最大。"""
    a = topic + " " + content
    b = row["topic"] + " " + row["content"]
    A, B = _bigrams(a), _bigrams(b)
    sim = 0.0
    if A and B:
        inter = len(A & B)
        sim = 2.0 * inter / (len(A) + len(B)) + _phrase_bonus(a, b)
    ka, kb = set(k.lower() for k in _kw_list(keywords)), set(k.lower() for k in _kw_list(row["keywords"]))
    if ka and kb:
        sim = max(sim, len(ka & kb) / len(ka | kb))
    return min(1.0, sim)


def _codegraph_sha():
    """Engineer 記憶寫入當下的 codegraph 索引版本（判斷過時用；讀不到就空）。"""
    path = os.getenv("CODEGRAPH_DB", "")
    if not path or not os.path.exists(path):
        return ""
    try:
        con = sqlite3.connect(path)
        try:
            r = con.execute("SELECT META_VALUE FROM HRMS_CODE_INDEX_META "
                            "WHERE META_KEY='LAST_GIT_SHA'").fetchone()
            return (r[0][:12] if r and r[0] else "")
        finally:
            con.close()
    except Exception:
        return ""


def _parse_supersedes(s):
    ids = []
    for part in str(s).replace("，", ",").split(","):
        part = part.strip()
        if part:
            ids.append(int(part))  # 非數字讓 ValueError 往上拋
    return ids


def remember(kind, topic, content, keywords="", func_path="", entry_path="",
             entry_method="", ref_key="", source="", supersedes="", force=False):
    """寫入一筆記憶（唯一寫入口）。
    - supersedes：訂正/歸納——新筆取代舊筆（可逗號分隔多筆），讀取端自動排除舊筆
    - 無 supersedes 時跑去重：同型別高相似且同結論 → 不新增、舊筆信心 +1
    - force=True 跳過去重（確認是新議題時用）"""
    if kind not in KINDS:
        return f"⚠️ kind 只能是 {' / '.join(KINDS)}（System=系統知識、Engineer=程式入口）。"
    if not topic or not content:
        return "⚠️ topic 與 content 為必填。"
    if kind == "Engineer" and not entry_path:
        return "⚠️ Engineer 記憶必須提供 entry_path（程式入口）。"
    if not db.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，記憶功能停用。"

    try:
        rows = db.load_all()
    except Exception as e:
        return f"⚠️ 記憶 DB 連線失敗：{e}"

    # ── 訂正/歸納：帶 supersedes 直接插入新版本 ──
    sup_ids = []
    if supersedes:
        try:
            sup_ids = _parse_supersedes(supersedes)
        except ValueError:
            return f"⚠️ supersedes 格式錯誤（需為記憶 ID，逗號分隔）：{supersedes}"
        known = {r["id"] for r in rows}
        missing = [i for i in sup_ids if i not in known]
        if missing:
            return f"⚠️ supersedes 指到不存在的記憶 ID：{missing}，請先用 recall 確認。"

    # ── 去重管線（無 supersedes 且未 force 時）──
    if not sup_ids and not force:
        best_sc, best = 0.0, None
        for r in active_rows(rows):
            if r["type"] != kind:
                continue  # 只在同型別內比：客服知識與維運筆記描述同一功能不算重複
            sc = _dup_sim(topic, content, keywords, r)
            if sc > best_sc:
                best_sc, best = sc, r
        if best and best_sc >= DUP_THRESHOLD:
            same_conclusion = (kind == "System"
                               or _base(entry_path) == _base(best["entry_path"]))
            if same_conclusion:
                try:
                    db.bump_hits([best["id"]])
                except Exception:
                    pass
                return (f"♻️ 已有等價記憶 **ID {best['id']}**（相似 {best_sc:.2f}），未新增；"
                        f"該筆信心 +1。\n"
                        f"若這其實是**訂正**（結論已改變），請帶 `supersedes={best['id']}` 重新呼叫；"
                        f"若確定是不同議題，帶 `force=true`。")
            return (f"⚠️ 發現高相似但**結論不同**的記憶 **ID {best['id']}**"
                    f"（相似 {best_sc:.2f}，入口 `{best['entry_path']}`）。\n"
                    f"若本筆是訂正 → 帶 `supersedes={best['id']}` 重新呼叫；"
                    f"若是不同議題 → 帶 `force=true`。")

    sha = _codegraph_sha() if kind == "Engineer" else ""
    meta = {"supersedes_all": sup_ids[1:]} if len(sup_ids) > 1 else None
    try:
        new_id = db.insert_memory(
            kind, topic, content, keywords, func_path, entry_path, entry_method,
            supersedes_id=sup_ids[0] if sup_ids else None,
            ref_key=ref_key, source=source, index_sha=sha, meta=meta)
    except Exception as e:
        return (f"⚠️ 記憶寫入失敗：{e}\n"
                f"請確認 HRMS_MEMORY 資料表已建立，且帳號有 INSERT 權限。")

    tag = "🔧 Engineer" if kind == "Engineer" else "🧭 System"
    out = [f"✅ 已記住（{tag}，記憶 ID **{new_id}**）【{topic}】"]
    if entry_path:
        m = f" :: `{entry_method}`" if entry_method else ""
        out.append(f"入口：`{entry_path}`{m}")
    if sup_ids:
        out.append(f"↺ 已取代舊記憶 {sup_ids}（檢索端不再回傳舊版）")
    out.append(f"記錄者：{db.created_by()}")
    return "\n".join(out)
