# -*- coding: utf-8 -*-
"""codegraph 核心邏輯：
- find_entry：完全走記憶層（HRMS_MEMORY 優先，anchors.json / cases.json 備援）
- trace / verify_call_path：唯讀 sqlite 呼叫圖索引（METHOD/EDGE）"""
import os, re, json, sqlite3

import memory_db as mem

DB_PATH = os.getenv("CODEGRAPH_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "codegraph.sqlite")
# DB 不可用時的本地備援（git 友善 json）
ANCHORS_JSON = os.getenv("CODEGRAPH_ANCHORS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "anchors.json")
CASES_JSON = os.getenv("CODEGRAPH_CASES") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cases.json")


def _load_anchors():
    """回傳 (anchors, synonyms, source)。
    優先讀共用 HRMS_MEMORY（source='db'），失敗或空表退 anchors.json（'json'/'db-empty'）。"""
    if mem.db_enabled():
        try:
            anchors, syns = mem.load_anchors_db()
            if anchors:
                return anchors, syns, "db"
            # DB 連得上但 ANCHOR 表是空的（尚未初始化）→ 退用本地 json，避免既有錨點失效
            if os.path.exists(ANCHORS_JSON):
                d = json.load(open(ANCHORS_JSON, encoding="utf-8"))
                anchors = [(a.get("domain", ""), a.get("triggers", ""), a.get("entry_path", ""),
                            a.get("entry_methods", ""), a.get("key_tables", ""), a.get("skill", ""))
                           for a in d.get("anchors", [])]
                syns = [(s.get("term", ""), s.get("maps_to", "")) for s in d.get("synonyms", [])]
                return anchors, syns, "db-empty"
            return anchors, syns, "db"
        except Exception:
            pass
    if os.path.exists(ANCHORS_JSON):
        d = json.load(open(ANCHORS_JSON, encoding="utf-8"))
        anchors = [(a.get("domain", ""), a.get("triggers", ""), a.get("entry_path", ""),
                    a.get("entry_methods", ""), a.get("key_tables", ""), a.get("skill", ""))
                   for a in d.get("anchors", [])]
        syns = [(s.get("term", ""), s.get("maps_to", "")) for s in d.get("synonyms", [])]
        return anchors, syns, "json"
    return [], [], "none"


def _load_cases():
    """回傳 [(issue, description, file)]。共用 HRMS_MEMORY 優先，退 cases.json。"""
    if mem.db_enabled():
        try:
            cases = mem.load_cases()
            if cases:
                return cases
        except Exception:
            pass
    if os.path.exists(CASES_JSON):
        try:
            d = json.load(open(CASES_JSON, encoding="utf-8"))
            return [(c.get("issue", ""), c.get("desc", ""), c.get("file", ""))
                    for c in d.get("cases", [])]
        except Exception:
            pass
    return []


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


def _index_sha():
    """讀取索引版本 SHA；sqlite 缺檔或無 META 時回 '?'（不建立空檔）。"""
    if not os.path.exists(DB_PATH):
        return '?'
    con = _con()
    try:
        return _sha(con.cursor())
    finally:
        con.close()


# ── 情節記憶：找相似的歷史查詢 ─────────────────────────────
def similar_episodes(question, top_k=3, threshold=0.34):
    """用中文 bigram 相似度（Dice + 包含度）比對歷史 episodes，回傳 [(score, episode)]。"""
    qs = _bigrams(question)
    if not qs:
        return []
    out = []
    for ep in mem.load_episodes():
        es = _bigrams(ep[1])
        if not es:
            continue
        inter = len(qs & es)
        if inter < 2:
            continue
        dice = 2.0 * inter / (len(qs) + len(es))
        contain = float(inter) / min(len(qs), len(es))
        score = max(dice, contain * 0.8)
        if ep[7]:  # 人工 review 通過（verified）→ 提升可信度
            score = min(1.0, score + 0.10)
        if score >= threshold:
            out.append((score, ep))
    out.sort(key=lambda x: -x[0])
    return out[:top_k]


# ── 語意知識：找相關的系統性知識片段 ───────────────────────
def similar_knowledge(question, top_k=3, threshold=0.30):
    """比對 KNOWLEDGE 表，回傳 [(score, knowledge)]。
    FACT 內容長、問題短，整段 Dice 會被稀釋，故 TOPIC 另計一路
    「問題覆蓋主題的比例」，與內容相似度取最大值。"""
    qs = _bigrams(question)
    if not qs:
        return []
    out = []
    for kn in mem.load_knowledge():
        ts = _bigrams(kn[1])                 # TOPIC
        ks = _bigrams(kn[1] + " " + kn[2])   # TOPIC + FACT
        if not ks:
            continue
        inter = len(qs & ks)
        topic_hit = len(qs & ts)
        if inter < 2 and topic_hit < 1:
            continue
        dice = 2.0 * inter / (len(qs) + len(ks))
        contain = float(inter) / min(len(qs), len(ks))
        topic_contain = float(topic_hit) / len(ts) if ts else 0.0
        score = max(dice, contain * 0.8, topic_contain)
        if kn[6]:  # 人工 review 通過（verified）→ 提升可信度
            score = min(1.0, score + 0.10)
        if score >= threshold:
            out.append((score, kn))
    out.sort(key=lambda x: -x[0])
    return out[:top_k]


# ── ① 找入口 ──────────────────────────────────────────────
def find_entry(description, top_k=3):
    anchors, syns, src = _load_anchors()
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
    # ② CASE：bigram + IDF（df>50 當雜訊丟）— 來源 HRMS_MEMORY('case') 或 cases.json
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
    if src == "json" and mem.db_enabled():
        out.append("\n> ⚠️ 共用記憶 DB 連線失敗，本次使用本地 anchors.json 備援")
    elif src == "db-empty":
        out.append("\n> ⚠️ 共用 DB 的 ANCHOR 表尚未初始化（空表），使用本地 anchors.json；"
                   "請執行 memory_tables.sql 的種子資料段落")

    # ⓪ 情節記憶：之前查過類似問題，直接給確認過的入口
    eps = []
    if mem.db_enabled():
        try:
            eps = similar_episodes(description)
        except Exception:
            eps = []
    if eps:
        out.append("\n## ⚡ 記憶命中：之前查過類似問題")
        for sc, (eid, q, ep, em, sha, hits, last, ver) in eps:
            m = f" :: `{em}`" if em else ""
            badge = " ✅已人工驗證" if ver else ""
            out.append(f"- (相似 {sc:.2f})「{q}」→ `{ep}`{m}（確認 {hits} 次，最近 {last}）{badge}")

    # 相關的系統性知識片段（不一定對應程式入口）
    kns = []
    if mem.db_enabled():
        try:
            kns = similar_knowledge(description)
        except Exception:
            kns = []
    if kns:
        out.append("\n## 📌 相關系統知識")
        for sc, (kid, topic, fact, rp, hits, last, ver) in kns:
            ref = f"（相關程式：`{rp}`）" if rp else ""
            badge = " ✅已人工驗證" if ver else ""
            out.append(f"- 【{topic}】{fact}{ref}{badge}")

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
    if eps and eps[0][0] >= 0.5:
        sc, (eid, q, ep, em, sha, hits, last, ver) = eps[0]
        m = f"，從函式 `{em}` 開始" if em else ""
        v = "、已人工驗證" if ver else ""
        out.append(f"之前確認過幾乎相同的問題 → 直接進 `{ep}`{m}（記憶相似 {sc:.2f}、確認 {hits} 次{v}）。")
    elif A:
        top = A[0]
        c = "（歷史前例佐證，信心高）" if _base(top[2]) in conv else ""
        out.append(f"這是【{top[1]}】問題 → 進 `{top[2]}` {c}")
        if top[3]:
            out.append(f"建議從函式 `{top[3]}` 開始；可用 `trace` 追呼叫鏈。")
    elif C:
        out.append(f"錨點未命中；最可能檔案 `{_base(C[0][0])}`（僅歷史前例，信心較低，建議人工確認）")
    else:
        out.append("無明確命中。建議改用關鍵字搜尋，或為此領域補一筆 ANCHOR。")
    if mem.db_enabled():
        out.append("\n▶ 追完程式鏈、確認實際入口後，請呼叫 `remember` 沉澱記憶（下次直接命中）；"
                   "若過程中確認了系統性知識（排程方式、執行時機、手動/自動等），用 `remember_fact` 記錄。")
    out.append(f"\n_index @ {_index_sha()}_")
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


# ── 回饋迴路：把查對的「敘述領域→入口」沉澱成 ANCHOR ──────────
def learn(domain, triggers, entry_path, entry_methods="", key_tables="", note=""):
    """新增/更新一個領域錨點（同名覆蓋）。優先寫共用 MSSQL 表，失敗退 anchors.json。"""
    db_err = ""
    if mem.db_enabled():
        try:
            total = mem.save_anchor(domain, triggers, entry_path, entry_methods, key_tables, note)
            return (f"✅ 已學習領域【{domain}】→ `{entry_path}`（寫入共用記憶 DB，全團隊生效）\n"
                    f"觸發詞：{triggers}\n"
                    f"目前共 {total} 個領域。")
        except Exception as e:
            db_err = f"\n⚠️ 共用記憶 DB 寫入失敗（{e}），已退回本地 anchors.json。"
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
            f"⚠️ 寫入的是執行中副本的 anchors.json；要**永久保留**請把它 commit 進 plugin 原始碼。"
            + db_err)


# ── 情節記憶：記住「這次的問題 → 確認的入口」──────────────────
def remember(question, entry_path, entry_method=""):
    """把一次查對的問題與確認入口寫進共用 EPISODE 表，下次 find_entry 直接命中。"""
    if not mem.db_enabled():
        return ("⚠️ 未設定 MSSQL_* 環境變數，情節記憶功能停用。\n"
                "領域級的知識仍可用 `learn` 寫入本地 anchors.json。")
    sha = _index_sha()
    try:
        eid, created = mem.add_episode(question, entry_path, entry_method, sha)
        if created:
            m = f" :: `{entry_method}`" if entry_method else ""
            return (f"✅ 已記住（記憶 ID {eid}）：「{question}」→ `{entry_path}`{m}\n"
                    f"之後全團隊查類似問題時，find_entry 會直接提示這個入口。")
        return f"✅ 相同記憶已存在（ID {eid}），確認次數 +1（信心累積）。"
    except Exception as e:
        return (f"⚠️ 記憶寫入失敗：{e}\n"
                f"請確認 HRMS_MEMORY 資料表已建立，且帳號有 INSERT 權限。")


# ── 語意知識：記住系統性知識片段（不一定對應程式入口）──────────
def remember_fact(topic, fact, related_path=""):
    """把確認過的系統性知識寫進共用 KNOWLEDGE 表，之後 find_entry 會一併帶出。"""
    if not mem.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，系統知識記憶功能停用。"
    try:
        kid, created = mem.add_knowledge(topic, fact, related_path)
        if created:
            ref = f"（相關程式：`{related_path}`）" if related_path else ""
            return (f"✅ 已記住系統知識（記憶 ID {kid}）【{topic}】{ref}\n{fact}\n"
                    f"之後全團隊問到相關主題時，find_entry 會一併帶出這則知識。")
        return f"✅ 相同知識已存在（ID {kid}），確認次數 +1（信心累積）。"
    except Exception as e:
        return (f"⚠️ 知識寫入失敗：{e}\n"
                f"請確認 HRMS_MEMORY 資料表已建立，且帳號有 INSERT 權限。")
