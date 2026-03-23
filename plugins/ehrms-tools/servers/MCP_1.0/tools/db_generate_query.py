"""
SQL 生成工具
根據 session 上下文、表結構、關聯資訊提供 SQL 生成建議
"""
from typing import List, Dict, Any
from utils.formatter import format_text_response, format_error_response
from utils.session_manager import get_session_manager


def db_generate_query(session_id: str, prompt: str) -> List[Dict[str, str]]:
    """
    LLM 根據 session 上下文、表結構、關聯資訊生成 SQL（不執行）
    
    Args:
        session_id: 工作階段 ID
        prompt: 查詢需求描述
    
    Returns:
        List[Dict[str, str]]: MCP 格式回應
    """
    try:
        session_manager = get_session_manager()
        
        # 驗證 session_id
        session = session_manager.get_session(session_id)
        if not session:
            return format_text_response(
                f"❌ Session ID '{session_id}' 不存在或已過期。請先使用 `db_mcp_init` 建立新的 session。"
            )
        
        # 從 session 獲取上下文資訊
        topic = session.get("topic", "")
        related_tables = session.get("related_tables", [])
        rule_summary = session.get("rule_summary", {})
        queried_tables = session.get("queried_tables", {})
        
        # 建立上下文資訊摘要
        result_text = f"📝 **SQL 生成上下文資訊**\n\n"
        result_text += f"🎯 **主題**: {topic}\n"
        result_text += f"📋 **查詢需求**: {prompt}\n\n"
        
        # 顯示相關表格
        if related_tables:
            result_text += f"🗂️ **相關表格** ({len(related_tables)} 個):\n"
            for i, table_name in enumerate(related_tables, 1):
                result_text += f"   {i}. {table_name}\n"
            result_text += "\n"
        
        # 顯示已查詢的表格結構
        if queried_tables:
            result_text += f"📊 **已查詢的表格結構** ({len(queried_tables)} 個):\n"
            for table_name, table_info in queried_tables.items():
                columns_count = len(table_info.get("columns", []))
                result_text += f"   - **{table_name}**: {columns_count} 個欄位\n"
            result_text += "\n"
        
        # 顯示規則摘要
        if rule_summary:
            rules = rule_summary.get("rules", [])
            if rules:
                result_text += f"📋 **相關規則** ({len(rules)} 個):\n"
                for i, rule in enumerate(rules[:3], 1):  # 只顯示前 3 個
                    result_text += f"   {i}. {rule.get('title', '無標題')}\n"
                if len(rules) > 3:
                    result_text += f"   ... 還有 {len(rules) - 3} 個規則\n"
                result_text += "\n"
        
        # SQL 生成建議
        result_text += "💡 **SQL 生成建議**:\n\n"
        result_text += "基於以上上下文資訊，建議生成 SQL 時：\n\n"
        
        # 1. 表格建議
        if related_tables:
            result_text += f"1. **使用相關表格**: 建議從以下表格開始：{', '.join(related_tables[:5])}\n"
            if len(related_tables) > 5:
                result_text += f"   （還有 {len(related_tables) - 5} 個相關表格）\n"
        
        # 2. JOIN 建議
        if queried_tables:
            # NOTE: 工具名稱需與 server.py 實際註冊一致，避免引導錯誤
            result_text += "\n2. **JOIN 關係**: 使用 `analyze_table_joins` 查詢表格關聯關係\n"
            result_text += "   建議查詢的表格：\n"
            for table_name in list(queried_tables.keys())[:3]:
                result_text += f"   - {table_name}\n"
        
        # 3. 欄位建議
        if queried_tables:
            result_text += "\n3. **欄位選擇**: 已查詢的表格結構包含以下欄位資訊\n"
            result_text += "   建議明確指定需要的欄位，避免使用 SELECT *\n"
        
        # 4. 驗證建議
        result_text += "\n4. **驗證步驟**:\n"
        result_text += "   - 生成 SQL 後，使用 `db_validate_query` 驗證語法、結構和安全性\n"
        result_text += "   - 驗證通過後，使用 `mssql_query` 執行查詢\n"
        
        # 5. 範例 SQL 結構
        result_text += "\n5. **範例 SQL 結構**:\n"
        result_text += "```sql\n"
        if related_tables:
            main_table = related_tables[0]
            result_text += f"SELECT \n"
            result_text += f"    -- 明確指定需要的欄位\n"
            result_text += f"FROM {main_table}\n"
            if len(related_tables) > 1:
                result_text += f"LEFT JOIN {related_tables[1]} ON ...\n"
            result_text += f"WHERE \n"
            result_text += f"    -- 添加查詢條件\n"
        else:
            result_text += f"SELECT column1, column2\n"
            result_text += f"FROM table_name\n"
            result_text += f"WHERE condition\n"
        result_text += "```\n"
        
        result_text += "\n⚠️ **注意**: 此工具僅提供上下文資訊和生成建議，實際 SQL 生成需要由 LLM 根據以上資訊完成。\n"
        result_text += "生成 SQL 後，請務必使用 `db_validate_query` 進行驗證。"
        
        return format_text_response(result_text)
        
    except Exception as e:
        return format_error_response(f"SQL 生成建議失敗：{str(e)}", "db_generate_query")


