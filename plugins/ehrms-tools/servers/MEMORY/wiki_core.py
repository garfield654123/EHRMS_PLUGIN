# -*- coding: utf-8 -*-
"""Wiki 查詢邏輯（純查詢，AI 不寫入）

頁面格式依 EHRMS_GIT docs/knowledge-wiki/Wiki頁面撰寫規範.md：
- YAML 頁首（topic_code、description、menu_paths…）
- 上半（給所有人）／下半（給工程）以 HTML 註解分隔
- 規則以 `### Rn：標題` 撰寫，並註明「依據：記憶 #…」
舊格式頁面（沒有 YAML 與規則編號）一樣能查，改以 `##`／`###` 章節計分。

不依賴 HRMS_WIKI 的擴充欄位（RULE_INDEX 等），規則與章節一律從 content 即時解析。
評分器重用 memory_core（作法同 refdata_core）。
"""
import re

import memory_core as mc
import memory_db as mdb
import wiki_db as db

LOOKUP_THRESHOLD = mc.RECALL_THRESHOLD
# 頁面已命中後，頁內規則用較低門檻列出（bigram 計分對自然語句偏嚴，
# 例：「請假後刷卡異常沒有消失」對 R7「已經請假…但不會消失」約 0.24）
UNIT_SHOW_THRESHOLD = 0.15
MAX_RULES_SHOWN = 3
SUMMARY_MAX = 200

_FRONT_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_HEAD_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
_RULE_RE = re.compile(r"^R(\d+)\s*[：:]\s*(.+)$")
_MEM_REF_RE = re.compile(r"記憶\s*((?:#\d+(?:\s*[–\-~～]\s*#?\d+)?[、,，\s]*)+)")
_MEM_ITEM_RE = re.compile(r"#(\d+)(?:\s*[–\-~～]\s*#?(\d+))?")
_HALF_UPPER = "上半"
_HALF_LOWER = "下半"


# ── 頁面解析 ─────────────────────────────────────────────────

def _front_matter(content):
    """回傳 (頁首 dict, 本文)。頁首只解析用得到的鍵：description、menu_paths。"""
    m = _FRONT_RE.match(content or "")
    if not m:
        return {}, content or ""
    meta, key = {}, None
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_]+)\s*:\s*(.*)$", line)
        if km:
            key, val = km.group(1), km.group(2).strip()
            meta[key] = val.strip("'\"") if val else []
            continue
        im = re.match(r"^\s*-\s*(.+)$", line)
        if im and key and isinstance(meta.get(key), list):
            meta[key].append(im.group(1).strip())
    return meta, content[m.end():]


def _memory_ids(text):
    """取出「記憶 #98、#110」「記憶 #92–#112」中的編號（範圍最多展開 50 個）。"""
    ids = []
    for grp in _MEM_REF_RE.findall(text or ""):
        for a, b in _MEM_ITEM_RE.findall(grp):
            lo = int(a)
            hi = int(b) if b else lo
            if hi < lo or hi - lo > 50:
                hi = lo
            for n in range(lo, hi + 1):
                if n not in ids:
                    ids.append(n)
    return ids


def _first_paragraph(text):
    for para in re.split(r"\n\s*\n", text or ""):
        p = para.strip()
        if p and not p.startswith(("#", "```", "|", "<!--", ">")):
            return " ".join(line.strip() for line in p.splitlines())
    return ""


def parse_page(content):
    """解析一頁 Wiki。

    回傳 dict：
      meta      YAML 頁首（description、menu_paths…）
      body      去除頁首後的全文
      halves    {"上半": str, "下半": str}（舊格式頁面沒有分隔註解時兩者皆為整頁）
      sections  [{level, title, text}]，text 含標題本身到下一個同級或更高層標題前
      rules     [{id: "R7", title, text, memory_ids}]
      summary   摘要（頁首 description，否則「這是什麼／摘要」段第一段）
    """
    meta, body = _front_matter(content)
    lines = body.splitlines()

    upper_at = lower_at = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("<!--"):
            if _HALF_UPPER in line and upper_at is None:
                upper_at = i
            elif _HALF_LOWER in line and lower_at is None:
                lower_at = i
    if lower_at is not None:
        halves = {_HALF_UPPER: "\n".join(lines[(upper_at or 0):lower_at]).strip(),
                  _HALF_LOWER: "\n".join(lines[lower_at:]).strip()}
    else:
        halves = {_HALF_UPPER: body.strip(), _HALF_LOWER: body.strip()}

    heads = []
    in_code = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = _HEAD_RE.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))

    sections, rules = [], []
    for idx, (i, level, title) in enumerate(heads):
        end = len(lines)
        for j, lv, _ in heads[idx + 1:]:
            if lv <= level:
                end = j
                break
        text = "\n".join(lines[i:end]).strip()
        sections.append({"level": level, "title": title, "text": text})
        rm = _RULE_RE.match(title) if level == 3 else None
        if rm:
            rules.append({"id": f"R{rm.group(1)}", "title": rm.group(2).strip(),
                          "text": text, "memory_ids": _memory_ids(text)})

    summary = meta.get("description") if isinstance(meta.get("description"), str) else ""
    if not summary:
        for s in sections:
            if s["level"] == 2 and s["title"] in ("這是什麼", "摘要"):
                summary = _first_paragraph(s["text"].split("\n", 1)[-1])
                break
    return {"meta": meta, "body": body, "halves": halves, "sections": sections,
            "rules": rules, "summary": summary or ""}


# ── 記憶編號換新 ─────────────────────────────────────────────

def resolve_memory_ids(ids, rows=None):
    """沿 HRMS_MEMORY 取代鏈把舊編號換成最新有效編號，只回傳有變動者 {舊: 新}。

    取代關係與 memory_core.active_rows 一致：被 reject 的取代者不算數。
    """
    if not ids:
        return {}
    try:
        rows = rows if rows is not None else mdb.load_all()
    except Exception:
        return {}
    succ = {}
    for r in rows:
        if r.get("review") == "rejected":
            continue
        targets = [r["supersedes"]] if r.get("supersedes") else []
        for x in (r.get("meta") or {}).get("supersedes_all", []):
            try:
                targets.append(int(x))
            except (TypeError, ValueError):
                pass
        for t in targets:
            succ[t] = r["id"]
    out = {}
    for i in ids:
        cur, seen = i, set()
        while cur in succ and cur not in seen and len(seen) < 20:
            seen.add(cur)
            cur = succ[cur]
        if cur != i:
            out[i] = cur
    return out


def _memory_note(ids, mapping):
    changed = [f"#{o} → #{mapping[o]}" for o in ids if o in mapping]
    return f"（依據已更新：{'、'.join(changed)}）" if changed else ""


# ── 查詢 ─────────────────────────────────────────────────────

def _visible(rows, customer_code):
    """未指定統編只回傳標準頁；指定時回傳標準頁＋該客戶的客製頁。"""
    cc = (customer_code or "").strip()
    out = []
    for r in rows:
        rc_ = (r.get("customer_code") or "").strip()
        if not rc_ or (cc and rc_ == cc):
            out.append(r)
    return out


def _find_page(rows, page):
    key = (page or "").strip()
    if not key:
        return None
    low = key.lower()
    for field in ("topic_code", "git_path", "title"):
        for r in rows:
            if (r.get(field) or "").strip().lower() == low:
                return r
    hits = [r for r in rows if low in (r.get("title") or "").lower()
            or low in (r.get("git_path") or "").lower()]
    return hits[0] if len(hits) == 1 else None


def _units(parsed):
    """計分單位：有規則就用規則，舊格式頁面用 ## 以下的章節。"""
    if parsed["rules"]:
        return [(r["id"] + " " + r["title"], r["text"], r) for r in parsed["rules"]]
    return [(s["title"], s["text"], None) for s in parsed["sections"]]


def _score(query, qs, topic, content, keywords=""):
    return mc._score(query, qs, {"topic": topic, "content": content,
                                 "keywords": keywords, "review": ""})


def _page_label(r):
    code = f"〔{r['topic_code']}〕" if r.get("topic_code") else ""
    cust = f"（客製 {r['customer_code']}）" if r.get("customer_code") else ""
    return f"{r['title']}{code}{cust}"


def _review_flag(r):
    n = int(r.get("open_review_count") or 0)
    return f"  ⚠ 此頁有 {n} 筆疑慮複查中" if n else ""


def _brief(text, limit=80):
    body = re.sub(r"^#{2,3}\s+.+\n?", "", text or "", count=1)
    body = re.sub(r"（依據：[^）]*）", "", body)
    line = _first_paragraph(body).replace("**", "")
    return line if len(line) <= limit else line[:limit] + "…"


def _search(rows, query, top_k):
    qs = mc._bigrams(query)
    if not qs:
        return "⚠️ 查詢敘述過短，無法比對。"
    scored = []
    for r in rows:
        parsed = parse_page(r.get("content", ""))
        unit_scores = []
        for topic, content, rule in _units(parsed):
            sc = _score(query, qs, topic, content)
            if sc >= UNIT_SHOW_THRESHOLD:
                unit_scores.append((sc, topic, content, rule))
        unit_scores.sort(key=lambda x: -x[0])
        page_sc = _score(query, qs, r.get("title", ""),
                         parsed["summary"] + " " + " ".join(u[0] for u in _units(parsed)),
                         r.get("keywords", ""))
        best = max([page_sc] + [u[0] for u in unit_scores if u[0] >= LOOKUP_THRESHOLD])
        if best >= LOOKUP_THRESHOLD:
            scored.append((best, r, parsed, unit_scores))
    scored.sort(key=lambda x: -x[0])

    out = [f"# Wiki 檢索：{query}"]
    if not scored:
        out.append("\n（無相符 Wiki）")
        return "\n".join(out)

    mem_rows = None
    for sc, r, parsed, units in scored[:int(top_k or 3)]:
        out.append(f"\n## {_page_label(r)}（相似 {sc:.2f}）")
        out.append(f"  路徑：`{r['git_path']}`")
        if parsed["summary"]:
            s = parsed["summary"]
            out.append(f"  摘要：{s if len(s) <= SUMMARY_MAX else s[:SUMMARY_MAX] + '…'}")
        if units:
            label = "命中規則" if parsed["rules"] else "命中章節"
            out.append(f"  {label}：")
            for usc, topic, content, rule in units[:MAX_RULES_SHOWN]:
                note = ""
                if rule and rule["memory_ids"]:
                    if mem_rows is None:
                        try:
                            mem_rows = mdb.load_all()
                        except Exception:
                            mem_rows = []
                    note = _memory_note(rule["memory_ids"],
                                        resolve_memory_ids(rule["memory_ids"], mem_rows))
                out.append(f"  - {topic}：{_brief(content)}{note}")
        menus = parsed["meta"].get("menu_paths")
        if isinstance(menus, list) and menus:
            out.append(f"  選單：{'、'.join(menus)}")
        flag = _review_flag(r)
        if flag:
            out.append(flag)
    _, ref, _, ref_units = scored[0]
    key = ref.get("topic_code") or ref.get("title")
    first = ref_units[0][3]["id"] if ref_units and ref_units[0][3] else "章節名"
    out.append(f"\n→ 讀原文：wiki_lookup(page=\"{key}\", section=\"{first}\")；"
               "section 也可用章節名或 上半／下半／全文")
    return "\n".join(out)


def _read_section(r, section):
    parsed = parse_page(r.get("content", ""))
    sec = (section or "").strip()
    head = f"# Wiki：{_page_label(r)}　`{r['git_path']}`"
    flag = _review_flag(r)

    if not sec:
        lines = [head]
        if flag:
            lines.append(flag)
        if parsed["summary"]:
            lines.append(f"\n摘要：{parsed['summary']}")
        if parsed["rules"]:
            lines.append("\n規則：")
            lines.extend(f"- {x['id']}：{x['title']}" for x in parsed["rules"])
        lines.append("\n章節：" + "、".join(s["title"] for s in parsed["sections"]
                                          if s["level"] == 2))
        lines.append("\n→ 指定 section 讀取原文（規則編號、章節名、上半／下半／全文）")
        return "\n".join(lines)

    def compose(text, note=""):
        parts = [head] + ([flag] if flag else []) + ["", text.strip()]
        if note:
            parts.append(note)
        return "\n".join(parts)

    if sec in ("全文", "all"):
        return compose(parsed["body"])
    if sec in (_HALF_UPPER, _HALF_LOWER):
        return compose(parsed["halves"][sec])

    rm = re.fullmatch(r"[Rr](\d+)", sec)
    if rm:
        rule = next((x for x in parsed["rules"] if x["id"] == f"R{rm.group(1)}"), None)
        if not rule:
            ids = "、".join(x["id"] for x in parsed["rules"]) or "（此頁沒有規則編號）"
            return f"⚠️ {_page_label(r)} 沒有 {sec.upper()}。可用規則：{ids}"
        note = _memory_note(rule["memory_ids"], resolve_memory_ids(rule["memory_ids"]))
        return compose(rule["text"], note)

    for s in parsed["sections"]:
        if sec == s["title"] or sec in s["title"]:
            return compose(s["text"])
    names = "、".join(s["title"] for s in parsed["sections"] if s["level"] == 2)
    return f"⚠️ {_page_label(r)} 找不到章節「{sec}」。可用章節：{names}"


def lookup(query=None, page=None, section=None, customer_code=None, top_k=3):
    """Wiki 查詢。

    - 搜尋模式（給 query）：回傳最相關的頁面摘要與命中規則。
    - 章節模式（給 page）：page 接受主題代碼、GIT_PATH 或標題；section 接受
      規則編號（R7）、章節名、上半／下半／全文；未給 section 回傳該頁目錄。
    未給 customer_code 時不回傳客製頁。
    """
    if not db.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，此功能停用。"
    if not (query or page):
        return "⚠️ 請提供 query（搜尋）或 page（指定頁面）。"
    try:
        rows = _visible(db.load_all(), customer_code)
    except Exception as e:
        return f"⚠️ Wiki DB 連線失敗：{e}"
    if not rows:
        return "（HRMS_WIKI 目前沒有已發佈的頁面）"

    if page:
        r = _find_page(rows, page)
        if not r:
            low = page.strip().lower()
            cands = [x["title"] for x in rows
                     if low in (x.get("title") or "").lower()
                     or low in (x.get("git_path") or "").lower()][:5]
            hint = f"可能是：{'、'.join(cands)}" if cands else "請改用 query 搜尋"
            return f"⚠️ 找不到頁面「{page}」。{hint}"
        return _read_section(r, section)
    return _search(rows, query, top_k)
