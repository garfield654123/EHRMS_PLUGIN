# -*- coding: utf-8 -*-
"""codegraph 核心邏輯（純程式圖譜）：
- find_entry：敘述 → 領域錨點（anchors.json）＋歷史前例（cases.json）→ 入口候選
- trace / verify_call_path：唯讀 sqlite 呼叫圖索引（METHOD/EDGE）

本模組只回答「程式長什麼樣、誰呼叫誰」，不碰任何記憶——
記憶（recall/remember）由獨立的 ehrms-memory MCP 負責，流程順序由 skill 編排。
"""
import os
import re
import json
import sqlite3

DB_PATH = os.getenv("CODEGRAPH_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "codegraph.sqlite")
# 領域錨點：git 版控的路由層（低頻變動、隨 plugin 發版分發）
ANCHORS_JSON = os.getenv("CODEGRAPH_ANCHORS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "anchors.json")
CASES_JSON = os.getenv("CODEGRAPH_CASES") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cases.json")


def _load_anchors():
    """回傳 (anchors, synonyms)。anchors=[(domain, triggers, entry_path, entry_methods, key_tables, skill)]"""
    if not os.path.exists(ANCHORS_JSON):
        return [], []
    d = json.load(open(ANCHORS_JSON, encoding="utf-8"))
    anchors = [(a.get("domain", ""), a.get("triggers", ""), a.get("entry_path", ""),
                a.get("entry_methods", ""), a.get("key_tables", ""), a.get("skill", ""))
               for a in d.get("anchors", [])]
    syns = [(s.get("term", ""), s.get("maps_to", "")) for s in d.get("synonyms", [])]
    return anchors, syns


def _load_cases():
    """回傳 [(issue, description, file)]，git 挖掘的歷史前例（沒有 cases.json 就是空）。"""
    if not os.path.exists(CASES_JSON):
        return []
    try:
        d = json.load(open(CASES_JSON, encoding="utf-8"))
        return [(c.get("issue", ""), c.get("desc", ""), c.get("file", ""))
                for c in d.get("cases", [])]
    except Exception:
        return []


def _con():
    return sqlite3.connect(DB_PATH)


def _bigrams(t):
    """中文 bigram ＋ 英數 token（excel、SP/資料表名等高鑑別度詞彙）。"""
    g = set()
    for run in re.findall(r'[一-鿿]+', t):
        if len(run) == 1:
            g.add(run)
        for i in range(len(run) - 1):
            g.add(run[i:i + 2])
    for tok in re.findall(r'[A-Za-z0-9_]{2,}', t.lower()):
        g.add(tok)
    return g


def _base(p):
    return p.replace('\\', '/').split('/')[-1]


def _sha(cur):
    try:
        r = cur.execute("SELECT META_VALUE FROM HRMS_CODE_INDEX_META WHERE META_KEY='LAST_GIT_SHA'").fetchone()
        return r[0][:9] if r and r[0] else '?'
    except Exception:
        return '?'


def index_sha():
    """讀取索引版本 SHA；sqlite 缺檔或無 META 時回 '?'（不建立空檔）。"""
    if not os.path.exists(DB_PATH):
        return '?'
    con = _con()
    try:
        return _sha(con.cursor())
    finally:
        con.close()


# ── ① 找入口 ──────────────────────────────────────────────
def find_entry(description, top_k=3):
    anchors, syns = _load_anchors()
    text = description
    for term, mp in syns:
        if term and term in description:
            text += " " + mp
    # ① ANCHOR：命中觸發詞數
    A = []
    for d, trig, ep, em, kt, sk in anchors:
        hit = [t for t in (trig or '').split(",") if t and t in text]
        if hit:
            A.append((len(hit), d, ep, em, kt, sk))
    A.sort(key=lambda x: -x[0])
    # ② CASE：bigram + IDF（df>50 當雜訊丟）
    cases = _load_cases()
    score = {}
    for g in _bigrams(description):
        files = {f for _, d, f in cases if g in d}
        df = len(files)
        if df == 0 or df > 50:
            continue
        for f in files:
            score[f] = score.get(f, 0) + 1.0 / df
    C = sorted(score.items(), key=lambda x: -x[1])[:5]
    anchor_bases = {_base(a[2]): a[1] for a in A}
    conv = {_base(f) for f, _ in C if _base(f) in anchor_bases}

    out = [f"# 找入口：{description}"]
    if A:
        out.append("\n## ① 領域錨點")
        for i, (n, d, ep, em, kt, sk) in enumerate(A[:top_k]):
            star = " ★①②收斂" if _base(ep) in conv else ""
            out.append(f"- **{d}**（信心 {n}）{star} → `{ep}`")
            if i == 0:
                if em:
                    out.append(f"  - 入口函式：`{em}`")
                if kt:
                    out.append(f"  - 關鍵欄位/表：{kt}")
                if sk:
                    out.append(f"  - 領域知識 skill：{sk}")
    if C:
        out.append("\n## ② 歷史前例（git commit）")
        for f, sc in C[:4]:
            tag = " ←與①同" if _base(f) in anchor_bases else ""
            out.append(f"- {sc:.2f} `{_base(f)}`{tag}")

    out.append("\n## ▶ 結論")
    if A:
        top = A[0]
        c = "（歷史前例佐證，信心高）" if _base(top[2]) in conv else ""
        w = "（僅命中 1 個觸發詞，信心低，建議先驗證）" if top[0] <= 1 else ""
        out.append(f"這是【{top[1]}】問題 → 進 `{top[2]}` {c}{w}")
        if top[3]:
            out.append(f"建議從函式 `{top[3]}` 開始；可用 `trace` 追呼叫鏈。")
    elif C:
        out.append(f"錨點未命中；最可能檔案 `{_base(C[0][0])}`（僅歷史前例，信心較低，建議人工確認）")
    else:
        out.append("錨點未命中。建議改用關鍵字搜尋原始碼。")
    out.append(f"\n_index @ {index_sha()}_")
    return "\n".join(out)


# ── ② 追程式鏈 ────────────────────────────────────────────
def trace(entry, cls_hint=None, depth=2):
    con = _con(); cur = con.cursor()
    try:
        q = "SELECT CLS_PATH,START_LINE,END_LINE FROM HRMS_CODE_METHOD WHERE METHOD_NAME=?"
        p = [entry]
        if cls_hint:
            q += " AND CLS_PATH LIKE ?"; p.append(f"%{cls_hint}%")
        rows = cur.execute(q, p).fetchall()
        if not rows:
            return f"找不到函式 `{entry}`" + (f"（class 含 {cls_hint}）" if cls_hint else "")
        cls_path = rows[0][0]
        out = [f"# 程式鏈：{entry}  @ `{cls_path}`"]
        seen = set()

        def walk(mn, d, prefix):
            if d > depth or mn in seen:
                return
            seen.add(mn)
            r = cur.execute("SELECT START_LINE,END_LINE,SUMMARY_HEAD FROM HRMS_CODE_METHOD "
                            "WHERE METHOD_NAME=? AND CLS_PATH=? LIMIT 1", (mn, cls_path)).fetchone()
            info = f"({r[0]}~{r[1]}) {r[2] or ''}" if r else ""
            sps = [x[0] for x in cur.execute(
                "SELECT DISTINCT DST_PATH FROM HRMS_CODE_EDGE WHERE SRC_METHOD=? AND SRC_PATH=? AND EDGE_KIND='calls_sp'",
                (mn, cls_path))]
            sptag = ("  ⟶SP: " + ", ".join(sps[:5])) if sps else ""
            out.append(f"{prefix}{mn} {info}{sptag}")
            callees = [x[0] for x in cur.execute(
                "SELECT DISTINCT DST_PATH FROM HRMS_CODE_EDGE WHERE SRC_METHOD=? AND SRC_PATH=? AND EDGE_KIND='calls_method'",
                (mn, cls_path))]
            for c in callees:
                walk(c, d + 1, prefix + "    └─ ")

        walk(entry, 0, "")
        callers = [x[0] for x in cur.execute(
            "SELECT DISTINCT SRC_METHOD FROM HRMS_CODE_EDGE WHERE DST_PATH=? AND SRC_PATH=? AND EDGE_KIND='calls_method'",
            (entry, cls_path))]
        out.append(f"\n▲ 被呼叫(入口)：{', '.join(callers[:12]) or '(無 / 由排程或跨類別呼叫)'}")
        out.append(f"\n_index @ {_sha(cur)}_")
        return "\n".join(out)
    finally:
        con.close()


# ── ③ 驗證呼叫（反幻覺）──────────────────────────────────
def verify_call_path(src_method, dst):
    con = _con(); cur = con.cursor()
    try:
        rows = cur.execute(
            "SELECT SRC_PATH,EDGE_KIND,EVIDENCE,LINE_NO FROM HRMS_CODE_EDGE "
            "WHERE SRC_METHOD=? AND (DST_PATH=? OR DST_PATH LIKE ?)",
            (src_method, dst, f"%{dst}%")).fetchall()
        if rows:
            r = rows[0]
            lines = [f"✅ **verified**：`{src_method}` → `{dst}`（{r[1]}）"]
            if r[2]:
                lines.append(f"佐證：`{r[2][:150]}`" + (f"（L{r[3]}）" if r[3] else ""))
            lines.append(f"位置：`{r[0]}`")
            return "\n".join(lines)
        return (f"❓ **not_found**：索引查無 `{src_method}` → `{dst}` 的直接呼叫。\n"
                "⚠️ 這**不代表不存在**——可能是動態呼叫(變數組 SP 名)、跨類別呼叫，或索引未涵蓋。"
                "請人工於原始碼確認，切勿當作『一定沒有』。")
    finally:
        con.close()
