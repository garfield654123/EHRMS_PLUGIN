"""
表格欄位資訊查詢工具服務
支援可選的 session_id 參數，提供快取功能
"""
from typing import List, Dict, Any, Optional
from utils.db import execute_query_with_pool
from utils.formatter import format_text_response, format_error_response, format_table_info, format_column_info
from utils.session_manager import get_session_manager


def get_table_columns(table_name: str, session_id: Optional[str] = None) -> List[Dict[str, str]]:
    """
    查詢表格欄位資訊
    
    Args:
        table_name: 表格名稱
        session_id: 工作階段 ID（可選，提供時會啟用快取功能）
    
    Returns:
        List[Dict[str, str]]: MCP 格式回應
    """
    try:
        table_name_upper = table_name.upper()
        
        # 如果有 session_id，驗證並檢查快取
        if session_id:
            session_manager = get_session_manager()
            session = session_manager.get_session(session_id)
            if not session:
                return format_text_response(
                    f"❌ Session ID '{session_id}' 不存在或已過期。請先使用 `db_mcp_init` 建立新的 session。"
                )
            
            # 檢查是否有快取的資料
            cached_info = session_manager.get_cached_table_info(session_id, table_name_upper)
            if cached_info:
                result_text = f"📋 **表格欄位資訊** (快取): {table_name_upper}\n\n"
                result_text += cached_info.get("formatted_text", "")
                return format_text_response(result_text)
        
        # 檢查指定表格是否存在
        table_check_query = """
        SELECT COUNT(*) as count 
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_NAME = ?
        """
        table_exists = execute_query_with_pool(
            table_check_query,
            params=(table_name_upper,)
        )
        
        if not table_exists or table_exists[0]['count'] == 0:
            return format_text_response(f"❌ 表格 '{table_name}' 不存在。")
        
        # 查詢表格基本資訊（從 hrms_tables）
        table_info_query = """
        SELECT TABLE_NAME, TABLE_DESC, FUNCTION_NAME
        FROM HRMS_TABLES
        WHERE TABLE_NAME = ?
        """
        table_infos = execute_query_with_pool(
            table_info_query,
            params=(table_name_upper,)
        )
        
        system_name = '未知系統'
        table_desc = '無描述'
        if table_infos:
            system_name = table_infos[0].get('FUNCTION_NAME', '') or '未知系統'
            table_desc = table_infos[0].get('TABLE_DESC', '') or '無描述'
        
        # 查詢表格欄位資訊（使用參數化查詢）
        # 注意：ep.value 是 sql_variant 類型，需要轉換為字串才能使用 LIKE
        columns_query = """
        SELECT
            ep.major_id,
            ht.FUNCTION_NAME as '系統名稱',
            ht.table_desc as '表格編號(用途)',
            OBJECT_NAME(c.object_id) AS '表格名稱',
            c.name AS '欄位名稱',
            t.name AS '格式',
            c.max_length as '長度(MLength)',
            c.precision as '精確度(Precision)',
            c.scale as '小數點(Scale)',
            CAST(ep.value AS NVARCHAR(MAX)) as '欄位說明',
            CASE
                WHEN CAST(ep.value AS NVARCHAR(MAX)) LIKE '%|%'
                THEN SUBSTRING(CAST(ep.value AS NVARCHAR(MAX)), CHARINDEX('|', CAST(ep.value AS NVARCHAR(MAX))) + 1, LEN(CAST(ep.value AS NVARCHAR(MAX))))
                ELSE ''
            END as '參數說明'
        FROM
            sys.columns AS c
        INNER JOIN
            sys.types AS t ON c.system_type_id = t.system_type_id AND c.user_type_id = t.user_type_id
        LEFT JOIN
            sys.extended_properties AS ep ON ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.class = 1
        LEFT JOIN
            hrms_tables as ht on ht.TABLE_ID = ep.major_id
        WHERE
            OBJECT_SCHEMA_NAME(c.object_id) <> 'sys'
            AND OBJECT_NAME(c.object_id) = ?
        ORDER BY c.column_id
        """
        
        results = execute_query_with_pool(
            columns_query,
            params=(table_name_upper,)
        )
        
        if not results:
            return format_text_response(
                f"🔍 表格 '{table_name}' 沒有找到欄位資訊或欄位說明。"
            )
        
        # 格式化結果
        table_name_display = results[0]['表格名稱'] if results else table_name
        # 如果沒有從 hrms_tables 取得，則從查詢結果取得
        if not table_infos:
            system_name = results[0]['系統名稱'] if results and results[0]['系統名稱'] else '未知系統'
            table_desc = results[0]['表格編號(用途)'] if results and results[0]['表格編號(用途)'] else '無描述'
        
        result_text = format_table_info(table_name_display, system_name, table_desc)
        result_text += f"📊 **欄位列表** (共 {len(results)} 個欄位):\n\n"
        
        columns_data = []
        for i, row in enumerate(results, 1):
            column_name = row['欄位名稱'] or ''
            data_type = row['格式'] or ''
            max_length = row['長度(MLength)'] or 0
            precision = row['精確度(Precision)'] or 0
            scale = row['小數點(Scale)'] or 0
            description = row['欄位說明'] or ''
            param_note = row['參數說明'] or ''
            
            result_text += format_column_info(
                column_name,
                data_type,
                max_length,
                precision,
                scale,
                description,
                param_note
            )
            
            # 如果有 session_id，儲存欄位資料供快取使用
            if session_id:
                columns_data.append({
                    "column_name": column_name,
                    "data_type": data_type,
                    "max_length": max_length,
                    "precision": precision,
                    "scale": scale,
                    "description": description,
                    "param_note": param_note
                })
        
        # 如果有 session_id，快取結果到 session
        if session_id:
            session_manager = get_session_manager()
            table_info_cache = {
                "table_name": table_name_upper,
                "system_name": system_name,
                "table_desc": table_desc,
                "columns": columns_data,
                "formatted_text": result_text
            }
            session_manager.cache_table_info(session_id, table_name_upper, table_info_cache)
        
        return format_text_response(result_text)
        
    except Exception as e:
        return format_error_response(f"欄位資訊查詢失敗：{str(e)}", "get_table_columns")






