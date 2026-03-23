"""
回應格式化模組
提供統一的文字輸出格式化功能
"""
from typing import List, Dict, Any, Optional


def format_text_response(text: str) -> List[Dict[str, str]]:
    """
    格式化文字回應為 MCP 標準格式
    
    Args:
        text: 要輸出的文字內容
    
    Returns:
        List[Dict[str, str]]: MCP 格式的回應
    """
    return [{"type": "text", "text": text}]


def format_error_response(error_message: str, tool_name: Optional[str] = None) -> List[Dict[str, str]]:
    """
    格式化錯誤回應
    
    Args:
        error_message: 錯誤訊息
        tool_name: 工具名稱（可選）
    
    Returns:
        List[Dict[str, str]]: MCP 格式的錯誤回應
    """
    prefix = f"❌ {tool_name}: " if tool_name else "❌ "
    return format_text_response(f"{prefix}{error_message}")


def format_success_response(message: str) -> List[Dict[str, str]]:
    """
    格式化成功回應
    
    Args:
        message: 成功訊息
    
    Returns:
        List[Dict[str, str]]: MCP 格式的成功回應
    """
    return format_text_response(f"✅ {message}")


def format_query_results(results: List[Dict[str, Any]], max_display: int = 10) -> str:
    """
    格式化查詢結果為文字
    
    Args:
        results: 查詢結果（字典列表）
        max_display: 最多顯示的記錄數
    
    Returns:
        str: 格式化後的文字
    """
    if not results:
        return "✅ 查詢執行成功，但沒有返回結果。"
    
    result_text = f"✅ 查詢執行成功，返回 {len(results)} 筆記錄：\n\n"
    
    # 顯示前 N 筆記錄
    for i, row in enumerate(results[:max_display], 1):
        result_text += f"記錄 {i}:\n"
        for key, value in row.items():
            result_text += f"  {key}: {value}\n"
        result_text += "\n"
    
    if len(results) > max_display:
        result_text += f"... 還有 {len(results) - max_display} 筆記錄"
    
    return result_text


def format_table_info(table_name: str, system_name: str, table_desc: str) -> str:
    """
    格式化表格基本資訊標題
    
    Args:
        table_name: 表格名稱
        system_name: 系統名稱
        table_desc: 表格描述
    
    Returns:
        str: 格式化後的文字
    """
    return f"📋 **表格欄位資訊**: {table_name}\n🏷️ **系統名稱**: {system_name}\n📝 **表格描述**: {table_desc}\n\n"


def format_column_info(
    column_name: str,
    data_type: str,
    max_length: Optional[int] = None,
    precision: Optional[int] = None,
    scale: Optional[int] = None,
    description: Optional[str] = None,
    param_note: Optional[str] = None
) -> str:
    """
    格式化單一欄位資訊
    
    Args:
        column_name: 欄位名稱
        data_type: 資料類型
        max_length: 最大長度
        precision: 精確度
        scale: 小數位數
        description: 欄位說明
        param_note: 參數說明
    
    Returns:
        str: 格式化後的文字
    """
    result = f"**{column_name}**\n"
    result += f"   🔤 格式: {data_type}"
    
    # 根據資料類型顯示適當的長度資訊
    if data_type in ['varchar', 'nvarchar', 'char', 'nchar'] and max_length and max_length > 0:
        actual_length = max_length // 2 if data_type.startswith('n') else max_length
        result += f"({actual_length})"
    elif data_type in ['decimal', 'numeric'] and precision and precision > 0:
        result += f"({precision},{scale or 0})"
    elif data_type in ['float', 'real'] and precision and precision > 0:
        result += f"({precision})"
    
    result += "\n"
    
    if description:
        # 如果有 | 符號，分別顯示描述和參數說明
        if '|' in description:
            main_desc = description.split('|')[0].strip()
            result += f"   📄 說明: {main_desc}\n"
            if param_note:
                result += f"   ⚙️ 參數說明: {param_note}\n"
        else:
            result += f"   📄 說明: {description}\n"
    else:
        result += f"   📄 說明: 無說明\n"
    
    result += "\n"
    return result


def format_relation_info(
    relation: Dict[str, Any],
    index: int,
    direction: str = "OUTBOUND"
) -> str:
    """
    格式化單一關聯關係資訊
    
    Args:
        relation: 關聯關係資料
        index: 索引編號
        direction: 方向（OUTBOUND 或 INBOUND）
    
    Returns:
        str: 格式化後的文字
    """
    confidence_emoji = {
        'HIGH': '🟢',
        'MEDIUM': '🟡',
        'LOW': '🔴'
    }.get(relation.get('CONFIDENCE_LEVEL', ''), '⚪')
    
    strength_emoji = '🔒' if relation.get('RELATION_STRENGTH') == 'REQUIRED' else '🔓'
    
    target_table = relation.get('TARGET_TABLE_NAME', '')
    source_table = relation.get('SOURCE_TABLE_NAME', '')
    source_col = relation.get('SOURCE_COLUMN_NAME', '')
    target_col = relation.get('TARGET_COLUMN_NAME', '')
    
    result = f"{confidence_emoji} **{index}. {target_table}**\n"
    result += f"   SQL: LEFT JOIN {target_table} ON {source_table}.{source_col} = {target_table}.{target_col}\n"
    result += f"   關聯類型: {relation.get('RELATION_TYPE', '')} {strength_emoji} {relation.get('RELATION_STRENGTH', '')}\n"
    result += f"   信心度: {relation.get('CONFIDENCE_LEVEL', '')}\n"
    
    if relation.get('RELATION_DESCRIPTION'):
        result += f"   描述: {relation.get('RELATION_DESCRIPTION')}\n"
    
    if relation.get('BUSINESS_RULE'):
        result += f"   業務規則: {relation.get('BUSINESS_RULE')}\n"
    
    result += "\n"
    return result






