# -*- coding: utf-8 -*-
"""HRMS_CUSTOM_SA 核心邏輯：客製分支 SA 規格文件索引與深度分析結果

唯一寫入口 custom_sa_log，供 custom-sa-analyze skill（headless 批次）逐文件呼叫。
一次性建檔工具：以 (branch_name, doc_path) upsert，重跑覆蓋舊結果，無 supersede 訂正鏈。
"""
import custom_sa_db as db
import memory_db as mdb
import pathnorm

DOC_TYPES = ("spec", "confirm", "accept", "install", "other")
MAPPING_STATUSES = ("unmapped", "partial", "mapped")


def log_doc(tax_id, branch_name, doc_path, doc_format, doc_type=None,
            version_label="", is_latest=None, analyzed=False,
            summary="", mapped_paths="", mapping_status="unmapped",
            parse_issue="", branch_commit=""):
    """寫入/更新一筆 HRMS_CUSTOM_SA。"""
    if not (tax_id and branch_name and doc_path and doc_format):
        return "⚠️ tax_id、branch_name、doc_path、doc_format 為必填。"
    if doc_type and doc_type not in DOC_TYPES:
        return f"⚠️ doc_type 只能是 {' / '.join(DOC_TYPES)} 或留空。"
    if mapping_status not in MAPPING_STATUSES:
        return f"⚠️ mapping_status 只能是 {' / '.join(MAPPING_STATUSES)}。"
    if not mdb.db_enabled():
        return "⚠️ 未設定 MSSQL_* 環境變數，HRMS_CUSTOM_SA 功能停用。"

    mapped_paths_norm, path_errs = pathnorm.normalize_lines(mapped_paths)
    if path_errs:
        return ("⚠️ mapped_paths 需為 **repo 相對路徑**，"
                "以下無法轉換：\n- " + "\n- ".join(path_errs))

    try:
        action, new_id = db.upsert_doc(
            tax_id, branch_name, doc_path, doc_format,
            doc_type=doc_type or None,
            version_label=version_label or None,
            is_latest=is_latest,
            analyzed=analyzed,
            summary=summary or None,
            mapped_paths=mapped_paths_norm or None,
            mapping_status=mapping_status,
            parse_issue=parse_issue or None,
            branch_commit=branch_commit or None,
        )
    except Exception as e:
        return (f"⚠️ HRMS_CUSTOM_SA 寫入失敗：{e}\n"
                f"請確認 HRMS_CUSTOM_SA 資料表已建立，且帳號有 INSERT/UPDATE 權限。")

    verb = "新增" if action == "INSERT" else "更新"
    return f"✅ 已{verb}（紀錄 ID **{new_id}**）{branch_name} :: {doc_path}（{mapping_status}）"
