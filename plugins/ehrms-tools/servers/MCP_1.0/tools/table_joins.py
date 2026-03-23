"""
表格 JOIN 關係分析工具服務
支援可選的 session_id 參數，提供 session 驗證功能
"""
from typing import List, Dict, Any, Optional
from utils.db import execute_query_with_pool
from utils.formatter import format_text_response, format_error_response
from utils.session_manager import get_session_manager


def analyze_table_joins(table_name: str, session_id: Optional[str] = None) -> List[Dict[str, str]]:
    """
    分析表格 JOIN 關係
    
    Args:
        table_name: 表格名稱
        session_id: 工作階段 ID（可選，提供時會進行 session 驗證）
    
    Returns:
        List[Dict[str, str]]: MCP 格式回應
    """
    try:
        # 如果有 session_id，驗證 session
        if session_id:
            session_manager = get_session_manager()
            session = session_manager.get_session(session_id)
            if not session:
                return format_text_response(
                    f"❌ Session ID '{session_id}' 不存在或已過期。請先使用 `db_mcp_init` 建立新的 session。"
                )
        
        table_name_upper = table_name.upper()
        
        # 檢查 hrms_table_relation 表格是否存在
        relation_table_check_query = """
        SELECT COUNT(*) as count 
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_NAME = ?
        """
        relation_table_exists = execute_query_with_pool(
            relation_table_check_query,
            params=('hrms_table_relation',)
        )
        
        if not relation_table_exists or relation_table_exists[0]['count'] == 0:
            return format_text_response(
                "❌ hrms_table_relation 表格不存在。請先建立表格關聯記錄表。"
            )
        
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
        
        # 查詢 hrms_table_relation 獲取關聯關係（包含作為源表和目標表的關係）
        relation_query = """
        SELECT 
            RELATION_NAME,
            SOURCE_TABLE_NAME,
            SOURCE_COLUMN_NAME,
            TARGET_TABLE_NAME,
            TARGET_COLUMN_NAME,
            RELATION_TYPE,
            RELATION_STRENGTH,
            CONFIDENCE_LEVEL,
            RELATION_DESCRIPTION,
            BUSINESS_RULE,
            'OUTBOUND' as DIRECTION
        FROM hrms_table_relation
        WHERE SOURCE_TABLE_NAME = ?
          AND IS_ACTIVE = 1
        
        UNION ALL
        
        SELECT 
            RELATION_NAME,
            TARGET_TABLE_NAME as SOURCE_TABLE_NAME,
            TARGET_COLUMN_NAME as SOURCE_COLUMN_NAME,
            SOURCE_TABLE_NAME as TARGET_TABLE_NAME,
            SOURCE_COLUMN_NAME as TARGET_COLUMN_NAME,
            CASE 
                WHEN RELATION_TYPE = '1:N' THEN 'N:1'
                WHEN RELATION_TYPE = 'N:1' THEN '1:N'
                ELSE RELATION_TYPE
            END as RELATION_TYPE,
            RELATION_STRENGTH,
            CONFIDENCE_LEVEL,
            RELATION_DESCRIPTION,
            BUSINESS_RULE,
            'INBOUND' as DIRECTION
        FROM hrms_table_relation
        WHERE TARGET_TABLE_NAME = ?
          AND IS_ACTIVE = 1
          AND SOURCE_TABLE_NAME != ?
        
        ORDER BY 
            CASE CONFIDENCE_LEVEL 
                WHEN 'HIGH' THEN 3 
                WHEN 'MEDIUM' THEN 2 
                WHEN 'LOW' THEN 1 
                ELSE 0 
            END DESC,
            CASE RELATION_STRENGTH 
                WHEN 'REQUIRED' THEN 2 
                WHEN 'OPTIONAL' THEN 1 
                ELSE 0 
            END DESC,
            RELATION_NAME
        """
        
        relations = execute_query_with_pool(
            relation_query,
            params=(table_name_upper, table_name_upper, table_name_upper)
        )
        
        if not relations:
            # 如果有 session_id，提供更詳細的提示
            if session_id:
                return format_text_response(
                    f"🔍 表格 '{table_name}' 在 hrms_table_relation 中沒有找到關聯關係記錄。\n\n"
                    f"💡 **建議**: 可以使用 `search_tables` 工具根據關鍵字搜尋相關表格，或檢查 hrms_table_relation 表格是否需要補充關聯資料。"
                )
            else:
                return format_text_response(
                    f"🔍 表格 '{table_name}' 在 hrms_table_relation 中沒有找到關聯關係記錄。"
                )
        
        # 格式化結果
        result_text = f"🔗 表格 '{table_name}' 的關聯關係（來自 hrms_table_relation）：\n\n"
        
        outbound_relations = [r for r in relations if r['DIRECTION'] == 'OUTBOUND']
        inbound_relations = [r for r in relations if r['DIRECTION'] == 'INBOUND']
        
        # 顯示出站關聯（當前表格作為源表）
        if outbound_relations:
            result_text += "📤 **出站關聯**（此表格關聯到其他表格）：\n\n"
            for i, relation in enumerate(outbound_relations, 1):
                confidence_emoji = {
                    'HIGH': '🟢',
                    'MEDIUM': '🟡',
                    'LOW': '🔴'
                }.get(relation['CONFIDENCE_LEVEL'], '⚪')
                
                strength_emoji = '🔒' if relation['RELATION_STRENGTH'] == 'REQUIRED' else '🔓'
                
                result_text += f"{confidence_emoji} **{i}. {relation['TARGET_TABLE_NAME']}**\n"
                result_text += f"   SQL: LEFT JOIN {relation['TARGET_TABLE_NAME']} ON {relation['SOURCE_TABLE_NAME']}.{relation['SOURCE_COLUMN_NAME']} = {relation['TARGET_TABLE_NAME']}.{relation['TARGET_COLUMN_NAME']}\n"
                result_text += f"   關聯類型: {relation['RELATION_TYPE']} {strength_emoji} {relation['RELATION_STRENGTH']}\n"
                result_text += f"   信心度: {relation['CONFIDENCE_LEVEL']}\n"
                
                if relation.get('RELATION_DESCRIPTION'):
                    result_text += f"   描述: {relation['RELATION_DESCRIPTION']}\n"
                
                if relation.get('BUSINESS_RULE'):
                    result_text += f"   業務規則: {relation['BUSINESS_RULE']}\n"
                
                result_text += "\n"
        
        # 顯示入站關聯（其他表格關聯到當前表格）
        if inbound_relations:
            result_text += "📥 **入站關聯**（其他表格關聯到此表格）：\n\n"
            for i, relation in enumerate(inbound_relations, 1):
                confidence_emoji = {
                    'HIGH': '🟢',
                    'MEDIUM': '🟡',
                    'LOW': '🔴'
                }.get(relation['CONFIDENCE_LEVEL'], '⚪')
                
                strength_emoji = '🔒' if relation['RELATION_STRENGTH'] == 'REQUIRED' else '🔓'
                
                result_text += f"{confidence_emoji} **{i}. {relation['TARGET_TABLE_NAME']}**\n"
                result_text += f"   SQL: LEFT JOIN {relation['TARGET_TABLE_NAME']} ON {relation['SOURCE_TABLE_NAME']}.{relation['SOURCE_COLUMN_NAME']} = {relation['TARGET_TABLE_NAME']}.{relation['TARGET_COLUMN_NAME']}\n"
                result_text += f"   關聯類型: {relation['RELATION_TYPE']} {strength_emoji} {relation['RELATION_STRENGTH']}\n"
                result_text += f"   信心度: {relation['CONFIDENCE_LEVEL']}\n"
                
                if relation.get('RELATION_DESCRIPTION'):
                    result_text += f"   描述: {relation['RELATION_DESCRIPTION']}\n"
                
                if relation.get('BUSINESS_RULE'):
                    result_text += f"   業務規則: {relation['BUSINESS_RULE']}\n"
                
                result_text += "\n"
        
        # 添加完整SQL範例（僅包含出站關聯，因為入站關聯通常用於反向查詢）
        if outbound_relations:
            result_text += "📝 **完整SQL範例**:\n```sql\n"
            result_text += f"SELECT * FROM {table_name_upper}\n"
            for relation in outbound_relations:
                result_text += f"LEFT JOIN {relation['TARGET_TABLE_NAME']} ON {relation['SOURCE_TABLE_NAME']}.{relation['SOURCE_COLUMN_NAME']} = {relation['TARGET_TABLE_NAME']}.{relation['TARGET_COLUMN_NAME']}\n"
            result_text += "```\n\n"
        
        # 添加統計資訊
        total_relations = len(relations)
        high_confidence = len([r for r in relations if r['CONFIDENCE_LEVEL'] == 'HIGH'])
        required_relations = len([r for r in relations if r['RELATION_STRENGTH'] == 'REQUIRED'])
        
        result_text += f"📊 **統計**: 總關聯數 {total_relations}，高信心度 {high_confidence}，必要關聯 {required_relations}"
        
        return format_text_response(result_text)
        
    except Exception as e:
        return format_error_response(f"JOIN關係分析失敗：{str(e)}", "analyze_table_joins")






