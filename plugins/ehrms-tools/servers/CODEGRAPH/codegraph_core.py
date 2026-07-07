# -*- coding: utf-8 -*-
"""codegraph 核心邏輯：find_entry / trace / verify_call_path（唯讀 sqlite）"""
import os, re, json, sqlite3

DB_PATH = os.getenv("CODEGRAPH_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "codegraph.sqlite")
# ANCHOR/SYNONYM 改用 git 友善的 json（source of truth），非 sqlite binary
ANCHORS_JSON = os.getenv("CODEGRAPH_ANCHORS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "anchors.json")


def _load_anchors(cur=None):
    """回傳 (anchors, synonyms)。優先讀 anchors.json，缺檔才退回 sqlite 表。"""
    if os.path.exists(ANCHORS_JSON):
        d = json.load(open(ANCHORS_JSON, encoding="utf-8"))
        anchors = [(a.get("domain", ""), a.get("triggers", ""), a.get("entry_path", ""),
                    a.get("entry_methods", ""), a.get("key_tables", ""), a.get("skill", ""))
                   for a in d.get("anchors", [])]
        syns = [(s.get("term", ""), s.get("maps_to", "")) for s in d.get("synonyms", [])]
        return anchors, syns
    if cur is not None:
        try:
            anchors = list(cur.execute(
                "SELECT DOMAIN,TRIGGERS,ENTRY_PATH,ENTRY_METHODS,KEY_TABLES,SKILL FROM HRMS_CODE_ANCHOR"))
            syns = list(cur.execute("SELECT TERM,MAPS_TO FROM HRMS_CODE_SYNONYM"))
            return anchors, syns
        except Exception:
            pass
    return [], []


def _con():
    return sqlite3.connect(DB_PATH)


def _bigrams(t):
    g = set()
    for run in re.findall(r'[一-鿿]+', t):
        if len(run) == 1:
            g.add(run)
        for i in range(len(run) - 1):
            g.add(run[i:i + 2])
    return g


def _base(p):
    return p.replace('\\', '/').split('/')[-1]


def _sha(cur):
    try:
        r = cur.execute("SELECT META_VALUE FROM HRMS_CODE_INDEX_META WHERE META_KEY='LAST_GIT_SHA'").fetchone()
        return r[0][:9] if r and r[0] else '?'
    except Exception:
        return '?'


# ── ① 找入口 ──────────────────────────────────────────────
def find_entry(description, top_k=3):
    con = _con(); cur = con.cursor()
    try:
        anchors, syns = _load_anchors(cur)
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
        score = {}
        for g in _bigrams(description):
            files = [r[0] for r in cur.execute(
                "SELECT DISTINCT FILE FROM HRMS_CODE_CASE WHERE DESCRIPTION LIKE ?", (f'%{g}%',))]
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
            out.append(f"這是【{top[1]}】問題 → 進 `{top[2]}` {c}")
            if top[3]:
                out.append(f"建議從函式 `{top[3]}` 開始；可用 `trace` 追呼叫鏈。")
        elif C:
            out.append(f"錨點未命中；最可能檔案 `{_base(C[0][0])}`（僅歷史前例，信心較低，建議人工確認）")
        else:
            out.append("無明確命中。建議改用關鍵字搜尋，或為此領域補一筆 ANCHOR。")
        out.append(f"\n_index @ {_sha(cur)}_")
        return "\n".join(out)
    finally:
        con.close()


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


# ── 回饋迴路：把查對的「敘述領域→入口」沉澱成 ANCHOR ──────────
def learn(domain, triggers, entry_path, entry_methods="", key_tables="", note=""):
    """新增/更新一個領域錨點到 anchors.json（越用越準）。"""
    data = {"anchors": [], "synonyms": []}
    if os.path.exists(ANCHORS_JSON):
        data = json.load(open(ANCHORS_JSON, encoding="utf-8"))
    data.setdefault("anchors", [])
    data.setdefault("synonyms", [])
    # 同名領域則覆蓋
    data["anchors"] = [a for a in data["anchors"] if a.get("domain") != domain]
    data["anchors"].append({
        "domain": domain, "triggers": triggers, "entry_path": entry_path,
        "entry_methods": entry_methods, "key_tables": key_tables, "skill": "", "note": note,
    })
    with open(ANCHORS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return (f"✅ 已學習領域【{domain}】→ `{entry_path}`\n"
            f"觸發詞：{triggers}\n"
            f"目前共 {len(data['anchors'])} 個領域。\n"
            f"⚠️ 寫入的是執行中副本的 anchors.json；要**永久保留**請把它 commit 進 plugin 原始碼。")
