# -*- coding: utf-8 -*-
"""HRMS_CUSTOM 核心邏輯：客製分支檔案層級深度分析

唯一寫入口 custom_log，供兩階段掃描 skill 對每個候選客製檔案深讀後呼叫。
sa_doc_path 有給值時，同步回填 HRMS_CUSTOM_SA.MAPPED_PATHS（雙向關聯）。
一次性建檔工具：以 (branch_name, custom_path) upsert，重跑覆蓋舊結果，無 supersede 訂正鏈。
"""
import custom_db as db
import custom_sa_db as sa_db
import memory_db as mdb
import pathnorm

CUSTOM_TYPES = ("standard", "pure", "version_lag")


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
