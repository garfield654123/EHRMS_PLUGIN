# -*- coding: utf-8 -*-
"""HRMS_CUSTOM_SA 資料層：客製分支 SA 規格文件索引（一次性建檔，非 append-only）

與 HRMS_MEMORY/HRMS_JIRA 不同：本表以 (BRANCH_NAME, DOC_PATH) 唯一鍵 upsert，
重跑 custom-sa-analyze 會覆蓋同一份文件的舊分析結果，不走 supersede 訂正鏈
（一次性建檔工具，沒有「保留歷史結論」的需求）。

連線與記錄者身分沿用 memory_db（同一個 MSSQL 帳號）。
"""
import memory_db as mdb

T = "dbo.HRMS_CUSTOM_SA"


def upsert_doc(tax_id, branch_name, doc_path, doc_format, doc_type=None,
               version_label=None, is_latest=None, analyzed=False,
               summary=None, mapped_paths=None, mapping_status="unmapped",
               parse_issue=None, branch_commit=None):
    """依 (BRANCH_NAME, DOC_PATH) upsert 一筆。回傳 (新增/更新, ID)。"""
    cur = mdb._get_conn().cursor()
    cur.execute(
        f"SET NOCOUNT ON; "
        f"MERGE {T} AS tgt "
        f"USING (SELECT ? AS BRANCH_NAME, ? AS DOC_PATH) AS src "
        f"ON tgt.BRANCH_NAME = src.BRANCH_NAME AND tgt.DOC_PATH = src.DOC_PATH "
        f"WHEN MATCHED THEN UPDATE SET "
        f"  TAX_ID=?, DOC_FORMAT=?, DOC_TYPE=?, VERSION_LABEL=?, IS_LATEST=?, "
        f"  ANALYZED=?, SUMMARY=?, MAPPED_PATHS=?, MAPPING_STATUS=?, PARSE_ISSUE=?, "
        f"  CREATED_BY=?, SCANNED_AT=SYSDATETIME(), BRANCH_COMMIT=? "
        f"WHEN NOT MATCHED THEN INSERT "
        f"  (TAX_ID,BRANCH_NAME,DOC_PATH,DOC_FORMAT,DOC_TYPE,VERSION_LABEL,IS_LATEST,"
        f"   ANALYZED,SUMMARY,MAPPED_PATHS,MAPPING_STATUS,PARSE_ISSUE,CREATED_BY,BRANCH_COMMIT) "
        f"  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        f"OUTPUT $action, inserted.ID;",
        (
            # USING
            branch_name, doc_path,
            # UPDATE SET
            tax_id, doc_format, doc_type, version_label, is_latest,
            bool(analyzed), summary, mapped_paths, mapping_status, parse_issue,
            mdb.created_by(), branch_commit,
            # INSERT VALUES
            tax_id, branch_name, doc_path, doc_format, doc_type, version_label, is_latest,
            bool(analyzed), summary, mapped_paths, mapping_status, parse_issue,
            mdb.created_by(), branch_commit,
        ))
    action, new_id = cur.fetchone()
    return action, new_id


def append_mapped_path(branch_name, doc_path, path_line):
    """把一行路徑追加進既有 MAPPED_PATHS（去重、不覆蓋其他欄位）；
    MAPPING_STATUS 若原本是 unmapped，順帶升級為 partial（HRMS_CUSTOM 那邊
    是獨立從程式碼反查回來的新證據，比原本沒找到要好）。
    找不到該筆文件時不動作，回傳 False；成功回傳 True。
    供 HRMS_CUSTOM 深讀掃描找到 SA 對應文件時回填用（雙向關聯）。"""
    cur = mdb._get_conn().cursor()
    cur.execute(
        f"SELECT MAPPED_PATHS, MAPPING_STATUS FROM {T} WHERE BRANCH_NAME=? AND DOC_PATH=?",
        (branch_name, doc_path))
    row = cur.fetchone()
    if row is None:
        return False
    existing, status = row[0] or "", row[1]
    lines = [l for l in existing.splitlines() if l.strip()]
    if path_line not in lines:
        lines.append(path_line)
    new_status = "partial" if status == "unmapped" else status
    cur.execute(
        f"UPDATE {T} SET MAPPED_PATHS=?, MAPPING_STATUS=? WHERE BRANCH_NAME=? AND DOC_PATH=?",
        ("\n".join(lines), new_status, branch_name, doc_path))
    return True
