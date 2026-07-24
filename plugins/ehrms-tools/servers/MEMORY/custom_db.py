# -*- coding: utf-8 -*-
"""HRMS_CUSTOM 資料層：客製分支檔案層級深度分析（一次性建檔，非 append-only）

與 HRMS_CUSTOM_SA 同樣以唯一鍵 upsert（本表為 BRANCH_NAME+CUSTOM_PATH），
重跑掃描會覆蓋同一檔案的舊分析結果，不走 supersede 訂正鏈。

連線與記錄者身分沿用 memory_db（同一個 MSSQL 帳號）。
"""
import memory_db as mdb

T = "dbo.HRMS_CUSTOM"


def upsert_file(company_sno, branch_name, custom_path, custom_type,
                std_baseline, standard_path=None, description=None,
                sa_doc_path=None, source="deep_scan", branch_commit=None):
    """依 (BRANCH_NAME, CUSTOM_PATH) upsert 一筆。回傳 (新增/更新, ID)。"""
    cur = mdb._get_conn().cursor()
    cur.execute(
        f"SET NOCOUNT ON; "
        f"MERGE {T} AS tgt "
        f"USING (SELECT ? AS BRANCH_NAME, ? AS CUSTOM_PATH) AS src "
        f"ON tgt.BRANCH_NAME = src.BRANCH_NAME AND tgt.CUSTOM_PATH = src.CUSTOM_PATH "
        f"WHEN MATCHED THEN UPDATE SET "
        f"  COMPANY_SNO=?, STANDARD_PATH=?, CUSTOM_TYPE=?, DESCRIPTION=?, "
        f"  SA_DOC_PATH=?, STD_BASELINE=?, SOURCE=?, SCANNED_AT=SYSDATETIME(), BRANCH_COMMIT=? "
        f"WHEN NOT MATCHED THEN INSERT "
        f"  (COMPANY_SNO,BRANCH_NAME,CUSTOM_PATH,STANDARD_PATH,CUSTOM_TYPE,DESCRIPTION,"
        f"   SA_DOC_PATH,STD_BASELINE,SOURCE,BRANCH_COMMIT) "
        f"  VALUES (?,?,?,?,?,?,?,?,?,?) "
        f"OUTPUT $action, inserted.ID;",
        (
            # USING
            branch_name, custom_path,
            # UPDATE SET
            company_sno, standard_path, custom_type, description,
            sa_doc_path, std_baseline, source, branch_commit,
            # INSERT VALUES
            company_sno, branch_name, custom_path, standard_path, custom_type, description,
            sa_doc_path, std_baseline, source, branch_commit,
        ))
    action, new_id = cur.fetchone()
    return action, new_id
