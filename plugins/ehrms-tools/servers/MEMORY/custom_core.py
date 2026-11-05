# -*- coding: utf-8 -*-
"""HRMS_CUSTOM 核心邏輯：客製分支檔案層級深度分析

唯一寫入口 custom_log，供兩階段掃描 skill 對每個候選客製檔案深讀後呼叫。
sa_doc_path 有給值時，同步回填 HRMS_CUSTOM_SA.MAPPED_PATHS（雙向關聯）。
一次性建檔工具：以 (branch_name, custom_path) upsert，重跑覆蓋舊結果，無 supersede 訂正鏈。
"""
import custom_db as db
import custom_git
import custom_sa_db as sa_db
import memory_db as mdb
import pathnorm

CUSTOM_TYPES = ("standard", "pure", "version_lag", "std_attached")

_TYPE_LABELS = {"standard": "標準客製", "pure": "純客製"}
_BRANCH_LIMIT = 5
_FILE_LIMIT = 120
_DOC_LIMIT = 20
_TEXT_LIMIT = 300


def log_file(company_sno, branch_name, custom_path, custom_type, std_baseline,
             standard_path="", description="", sa_doc_path="",
             source="deep_scan", branch_commit=""):
    """寫入/更新一筆 HRMS_CUSTOM；sa_doc_path 有值時同步回填 HRMS_CUSTOM_SA。"""
    if not (company_sno and branch_name and custom_path and std_baseline):
        return "⚠️ company_sno、branch_name、custom_path、std_baseline 為必填。"
    if custom_type not in CUSTOM_TYPES:
        return f"⚠️ custom_type 只能是 {' / '.join(CUSTOM_TYPES)}。"
    if not mdb.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，HRMS_CUSTOM 功能停用。"

    custom_path_norm, errs1 = pathnorm.normalize_lines(custom_path)
    if errs1:
        return "⚠️ custom_path 需為 repo 相對路徑，無法轉換：\n- " + "\n- ".join(errs1)
    standard_path_norm = None
    if standard_path:
        standard_path_norm, errs2 = pathnorm.normalize_lines(standard_path)
        if errs2:
            return "⚠️ standard_path 需為 repo 相對路徑，無法轉換：\n- " + "\n- ".join(errs2)

    try:
        action, new_id = db.upsert_file(
            company_sno, branch_name, custom_path_norm, custom_type, std_baseline,
            standard_path=standard_path_norm or None,
            description=description or None,
            sa_doc_path=sa_doc_path or None,
            source=source or "deep_scan",
            branch_commit=branch_commit or None,
        )
    except Exception as e:
        return (f"⚠️ HRMS_CUSTOM 寫入失敗：{e}\n"
                f"請確認 HRMS_CUSTOM 資料表已建立，且帳號有 INSERT/UPDATE 權限。")

    verb = "新增" if action == "INSERT" else "更新"
    out = [f"✅ 已{verb}（紀錄 ID **{new_id}**）{branch_name} :: {custom_path_norm}（{custom_type}）"]

    if sa_doc_path:
        try:
            backfilled = sa_db.append_mapped_path(branch_name, sa_doc_path, custom_path_norm)
            if backfilled:
                out.append(f"↺ 已回填 HRMS_CUSTOM_SA：{sa_doc_path} 的 mapped_paths 加入此檔案")
            else:
                out.append(f"⚠️ 回填失敗：HRMS_CUSTOM_SA 找不到 {branch_name} :: {sa_doc_path} 這筆文件")
        except Exception as e:
            out.append(f"⚠️ 回填 HRMS_CUSTOM_SA 時發生錯誤：{e}")

    return "\n".join(out)


def _short(text):
    text = " ".join((text or "").split())
    return text if len(text) <= _TEXT_LIMIT else text[:_TEXT_LIMIT] + "…"


def _analyzed_lines(rows, path_kw):
    real = [r for r in rows if r["CUSTOM_TYPE"] in _TYPE_LABELS]
    lag = sum(r["CUSTOM_TYPE"] == "version_lag" for r in rows)
    attached = sum(r["CUSTOM_TYPE"] == "std_attached" for r in rows)
    lines = [f"- 檔案層級分析 {len(rows)} 個（比對基準 {rows[0]['STD_BASELINE']}）："
             f"真客製 {len(real)} 個，版本落後 {lag} 個，標準附屬 {attached} 個"]
    shown = [r for r in real if path_kw in r["CUSTOM_PATH"].lower()]
    if path_kw and not shown:
        lines.append(f"  - 真客製檔案中沒有路徑符合「{path_kw}」的")
    for r in shown[:_FILE_LIMIT]:
        lines.append(f"  - [{_TYPE_LABELS[r['CUSTOM_TYPE']]}] {r['CUSTOM_PATH']}"
                     + (f"：{_short(r['DESCRIPTION'])}" if r["DESCRIPTION"] else ""))
    if len(shown) > _FILE_LIMIT:
        lines.append(f"  - …其餘 {len(shown) - _FILE_LIMIT} 個省略，可用 path_keyword 縮小範圍")
    return lines


def _git_lines(branch, path_kw, has_docs):
    files = custom_git.branch_files(branch)
    if files is None:
        return ["- 分支檔案：讀不到這個分支，需要時請到 CUSTOM_GIT 查看。"]
    # 部分分支連執行時產生的 ERRORLOG 一起進版，不是客製內容
    programs = [f for f in files if not f.startswith("SA/") and "/ERRORLOG/" not in f.upper()]
    sa_count = sum(f.startswith("SA/") for f in files)
    shown = [f for f in programs if path_kw in f.lower()]
    lines = [f"- 分支檔案 {len(programs)} 個（尚未做檔案層級分析，可能含標準附屬檔或版本落後的舊拷貝，不代表都是客製）："]
    if path_kw and not shown:
        lines.append(f"  - 沒有路徑符合「{path_kw}」的檔案")
    lines += [f"  - {f}" for f in shown[:_FILE_LIMIT]]
    if len(shown) > _FILE_LIMIT:
        lines.append(f"  - …其餘 {len(shown) - _FILE_LIMIT} 個省略，可用 path_keyword 縮小範圍")
    if sa_count and not has_docs:
        lines.append(f"- SA 文件：分支內有 {sa_count} 份，尚未分析。")
    return lines


def _doc_lines(rows):
    done = sum(bool(r["ANALYZED"]) for r in rows)
    lines = [f"- SA 文件 {len(rows)} 份（已分析 {done} 份）："]
    for r in rows[:_DOC_LIMIT]:
        lines.append(f"  - {r['DOC_PATH']}"
                     + (f"：{_short(r['SUMMARY'])}" if r["ANALYZED"] and r["SUMMARY"] else "（未分析）"))
    if len(rows) > _DOC_LIMIT:
        lines.append(f"  - …其餘 {len(rows) - _DOC_LIMIT} 份省略")
    return lines


def lookup(keyword, path_keyword=""):
    """查客戶有沒有客製、客製了哪些程式與規格書。只讀不寫。

    來源互補：CUSTOM_GIT 分支涵蓋最廣但未分類；HRMS_CUSTOM、HRMS_CUSTOM_SA 有分析但覆蓋率低。
    任一來源不可用時照常回傳其他來源，並註明缺了什麼，避免把「查不到」誤讀成「沒有客製」。
    """
    kw = (keyword or "").strip().strip("【】[]")
    if not kw:
        return "⚠️ 請提供客戶統編、公司名稱或英文代號。"
    path_kw = (path_keyword or "").strip().lower()

    notes = []
    branches = custom_git.list_branches()
    if branches is None:
        notes.append(f"讀不到 CUSTOM_GIT（{custom_git.repo_dir()}），未比對分支。")

    file_rows, doc_rows = [], []
    if mdb.db_enabled():
        try:
            doc_rows = sa_db.find_docs(kw)
        except Exception as e:
            notes.append(f"查 HRMS_CUSTOM_SA 失敗：{e}")
        try:
            file_rows = db.find_files(kw)
        except Exception as e:
            if "42S02" in str(e):
                notes.append("目前資料庫沒有 HRMS_CUSTOM 表，檔案清單未分類。")
            else:
                notes.append(f"查 HRMS_CUSTOM 失敗：{e}")
    else:
        notes.append("未設定 MSSQL_* 環境變數，未查客製分析表。")

    names = {b for b in branches or [] if custom_git.matches(b, kw)}
    names |= {r["BRANCH_NAME"] for r in file_rows} | {r["BRANCH_NAME"] for r in doc_rows}
    note_text = "".join(f"\n⚠️ {n}" for n in notes)

    if not names:
        if branches is None:
            return f"目前無法判斷「{kw}」是否有客製。{note_text}"
        return (f"CUSTOM_GIT 查無「{kw}」的客製分支（共 {len(branches)} 個分支，依分支名稱比對）。"
                f"可能是標準版客戶，或分支用了不同簡稱；公司名稱查不到時請改用統編再查一次。{note_text}")

    parts = []
    for branch in sorted(names)[:_BRANCH_LIMIT]:
        files = [r for r in file_rows if r["BRANCH_NAME"] == branch]
        docs = [r for r in doc_rows if r["BRANCH_NAME"] == branch]
        lines = [f"## 客製分支：{branch}（統編 {branch.split('_', 1)[0]}）"]
        lines += _analyzed_lines(files, path_kw) if files else _git_lines(branch, path_kw, bool(docs))
        if docs:
            lines += _doc_lines(docs)
        parts.append("\n".join(lines))
    if len(names) > _BRANCH_LIMIT:
        parts.append(f"（另有 {len(names) - _BRANCH_LIMIT} 個符合的分支未列出，請用統編或完整分支名縮小範圍）")
    parts.append("分支資料以本機最後一次 git fetch 為準；要比對與標準版的邏輯差異請用 custom-locate。" + note_text)
    return "\n\n".join(parts)
