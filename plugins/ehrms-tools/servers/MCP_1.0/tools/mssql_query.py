"""
MSSQL 查詢工具服務
"""
import time
from typing import List, Dict, Any, Optional
import sqlparse
from utils.db import execute_query_with_pool, get_db_credentials
from utils.formatter import format_query_results, format_text_response, format_error_response
from utils.session_manager import get_session_manager


def _is_read_only_query(query: str) -> bool:
    """只放行單一 SELECT / WITH 語句。
    DB 帳號因 ehrms-memory 記憶功能被授予 HRMS_MEMORY 表的 INSERT/UPDATE 權限，
    此工具必須在程式端維持唯讀，避免成為寫入通道。"""
    statements = [s for s in sqlparse.split(query) if s.strip()]
    if len(statements) != 1:
        return False
    parsed = sqlparse.parse(statements[0])
    if not parsed:
        return False
    stmt = parsed[0]
    # get_type 會穿透 WITH（CTE），回傳其後真正的 DML 型別，
    # 擋下 WITH x AS (...) DELETE/UPDATE/INSERT 這類夾帶寫入
    if stmt.get_type() != "SELECT":
        return False
    # SELECT ... INTO 會建表寫入，一併擋下
    for tok in stmt.flatten():
        if tok.ttype in sqlparse.tokens.Keyword and tok.normalized == "INTO":
            return False
    return True


def execute_mssql_query(
    query: str,
    server: Optional[str] = None,
    database: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    session_id: Optional[str] = None
) -> List[Dict[str, str]]:
    """
    執行 MSSQL 查詢（支援 session_id，向後相容）
    
    Args:
        query: SQL 查詢語句
        server: 伺服器位址（可選）
        database: 資料庫名稱（可選）
        username: 使用者名稱（可選）
        password: 密碼（可選）
        session_id: 工作階段 ID（可選，用於記錄查詢歷史）
    
    Returns:
        List[Dict[str, str]]: MCP 格式回應
    """
    try:
        start_time = time.time()
        
        # 如果有 session_id，驗證 session
        if session_id:
            session_manager = get_session_manager()
            session = session_manager.get_session(session_id)
            if not session:
                return format_text_response(
                    f"❌ Session ID '{session_id}' 不存在或已過期。請先使用 `db_mcp_init` 建立新的 session。"
                )
        
        # 驗證連線資訊
        get_db_credentials(server, database, username, password)

        # 唯讀防護：僅允許單一 SELECT / WITH 語句
        if not _is_read_only_query(query):
            return format_text_response(
                "❌ mssql_query 僅允許單一 SELECT（或 WITH）查詢語句，"
                "不接受 INSERT/UPDATE/DELETE/DDL 或多語句。"
            )

        # 執行查詢（注意：對於動態 SQL，無法完全參數化，但仍使用連線池）
        results = execute_query_with_pool(
            query,
            server=server,
            database=database,
            username=username,
            password=password
        )
        
        execution_time = time.time() - start_time
        
        # 格式化結果
        result_text = format_query_results(results, max_display=10)
        
        # 添加執行統計
        result_text += f"\n⏱️ **執行時間**: {execution_time:.3f} 秒\n"
        result_text += f"📊 **返回筆數**: {len(results)} 筆\n"
        
        # 如果有 session_id，記錄查詢歷史
        if session_id:
            session_manager = get_session_manager()
            result_summary = f"執行成功，返回 {len(results)} 筆記錄，耗時 {execution_time:.3f} 秒"
            session_manager.add_query_history(session_id, query, result_summary)
        
        return format_text_response(result_text)
        
    except ValueError as e:
        return format_text_response(
            f"❌ 缺少必要的資料庫連接資訊。請提供 server, database, username, password 參數或設定環境變數。"
        )
    except Exception as e:
        # 如果有 session_id，記錄錯誤到查詢歷史
        if session_id:
            try:
                session_manager = get_session_manager()
                session_manager.add_query_history(session_id, query, f"執行失敗：{str(e)}")
            except:
                pass
        
        return format_error_response(f"查詢執行失敗：{str(e)}", "mssql_query")






