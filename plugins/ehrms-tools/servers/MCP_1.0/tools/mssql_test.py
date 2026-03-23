"""
MSSQL 連線測試工具服務
"""
from typing import List, Dict
from utils.db import test_connection
from utils.formatter import format_text_response, format_error_response


def test_mssql_connection(
    server: str,
    database: str,
    username: str,
    password: str
) -> List[Dict[str, str]]:
    """
    測試 MSSQL 連線
    
    Args:
        server: 伺服器位址
        database: 資料庫名稱
        username: 使用者名稱
        password: 密碼
    
    Returns:
        List[Dict[str, str]]: MCP 格式回應
    """
    try:
        test_connection(server, database, username, password)
        return format_text_response(
            f"✅ 成功連接到 MSSQL 資料庫：{server}/{database}"
        )
    except Exception as e:
        return format_error_response(f"連接失敗：{str(e)}", "mssql_test_connection")






