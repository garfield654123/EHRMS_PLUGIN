"""
資料庫 MCP 初始化工具
根據主題從 hrms_sys_rule 獲取相關表清單並建立 session
"""
import re
from typing import List, Dict, Any, Set
from utils.db import execute_query_with_pool
from utils.formatter import format_text_response, format_error_response
from utils.session_manager import get_session_manager


def extract_table_names(content: str) -> List[str]:
    """從 CONTENT 中提取表格名稱"""
    if not content:
        return []
    
    patterns = [
        r'FROM\s+([A-Z][A-Z0-9_]+)',           # FROM TABLE_NAME
        r'JOIN\s+([A-Z][A-Z0-9_]+)',           # JOIN TABLE_NAME
        r'from\s+([A-Z][A-Z0-9_]+)',           # from TABLE_NAME (小寫)
        r'join\s+([A-Z][A-Z0-9_]+)',           # join TABLE_NAME (小寫)
        r'select\s+\*\s+from\s+([A-Z][A-Z0-9_]+)',  # select * from TABLE_NAME
    ]
    
    tables = set()
    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        tables.update(matches)
    
    # 過濾掉不符合命名慣例的表格名稱
    valid_prefixes = ['HRMS_', 'PORTAL_', 'HRMST_', 'WF_', 'BRANDS_']
    valid_tables = [t for t in tables if any(t.startswith(p) for p in valid_prefixes)]
    
    return sorted(valid_tables)


def db_mcp_init(topic: str) -> List[Dict[str, str]]:
    """
    初始化 MCP session，根據主題從 hrms_sys_rule 獲取相關表清單
    
    Args:
        topic: 主題關鍵字（支援多關鍵字，用空格分隔）
    
    Returns:
        List[Dict[str, str]]: MCP 格式回應
    """
    try:
        session_manager = get_session_manager()
        
        # 將關鍵字按空格拆分，過濾空字串
        keywords = [kw.strip() for kw in topic.split() if kw.strip()]
        if not keywords:
            return format_text_response("❌ 請提供至少一個關鍵字。")
        
        # 對每個關鍵字進行 SQL 注入防護
        keywords_escaped = [kw.replace("'", "''") for kw in keywords]
        
        # 動態生成 SQL 查詢（支援多關鍵字匹配）
        # 每個關鍵字在每個欄位中都要匹配（OR 條件）
        keyword_conditions = []
        params_list = []
        
        # 為每個關鍵字生成 LIKE 條件
        for kw_escaped in keywords_escaped:
            like_pattern = f'%{kw_escaped}%'
            keyword_conditions.append(f"""
                (KEYWORDS LIKE ? OR TITLE LIKE ? OR DESCRIPTION LIKE ? OR CONTENT LIKE ?)
            """)
            # 每個關鍵字需要 4 個參數（對應 4 個欄位）
            params_list.extend([like_pattern, like_pattern, like_pattern, like_pattern])
        
        # 組合所有關鍵字條件（使用 OR 連接，任一關鍵字匹配即可）
        where_conditions = " OR ".join(keyword_conditions)
        
        # 生成排序條件的 CASE 語句
        # 優先級：TITLE 開頭匹配 > KEYWORDS 包含匹配 > DESCRIPTION 包含匹配
        order_when_clauses = []
        order_params = []
        
        # TITLE 開頭匹配（優先級 1）
        for kw_escaped in keywords_escaped:
            starts_with_pattern = f'{kw_escaped}%'
            order_when_clauses.append("WHEN TITLE LIKE ? THEN 1")
            order_params.append(starts_with_pattern)
        
        # KEYWORDS 包含匹配（優先級 2）
        for kw_escaped in keywords_escaped:
            like_pattern = f'%{kw_escaped}%'
            order_when_clauses.append("WHEN KEYWORDS LIKE ? THEN 2")
            order_params.append(like_pattern)
        
        # DESCRIPTION 包含匹配（優先級 3）
        for kw_escaped in keywords_escaped:
            like_pattern = f'%{kw_escaped}%'
            order_when_clauses.append("WHEN DESCRIPTION LIKE ? THEN 3")
            order_params.append(like_pattern)
        
        order_case = "\n                ".join(order_when_clauses) if order_when_clauses else "1=1 THEN 4"
        
        rule_search_query = f"""
        SELECT TOP (10)
            RULE_ID, MODULE, CATEGORY, TITLE, KEYWORDS, 
            DESCRIPTION, CONTENT, AUTHOR, MODIFIED_DATE
        FROM hrms_sys_rule
        WHERE IS_ACTIVE = 1
          AND CATEGORY = 'DB'
          AND ({where_conditions})
        ORDER BY 
            CASE 
                {order_case}
                ELSE 4
            END,
            MODIFIED_DATE DESC
        """
        
        # 組合所有參數：WHERE 條件參數 + ORDER BY 條件參數
        all_params = tuple(params_list + order_params)
        
        rules = execute_query_with_pool(
            rule_search_query,
            params=all_params
        )
        
        if not rules:
            return format_text_response(
                f"❌ 未找到與主題「{topic}」相關的規則（Category = 'DB'）。\n"
                f"請嘗試使用其他關鍵字或檢查 hrms_sys_rule 表格。"
            )
        
        # 收集所有提取的表格名稱
        all_extracted_tables: Set[str] = set()
        rules_summary = []
        
        for rule in rules:
            content = rule.get('CONTENT', '') or ''
            extracted_tables = extract_table_names(content)
            all_extracted_tables.update(extracted_tables)
            
            # 建立規則摘要
            rule_summary = {
                "rule_id": rule.get('RULE_ID', ''),
                "title": rule.get('TITLE', '') or '無標題',
                "module": rule.get('MODULE', '') or '未分類',
                "keywords": rule.get('KEYWORDS', '') or '無關鍵字',
                "description": rule.get('DESCRIPTION', '') or '無描述',
                "extracted_tables": extracted_tables
            }
            rules_summary.append(rule_summary)
        
        # 批次查詢所有表格資訊
        table_info_dict = {}
        if all_extracted_tables:
            placeholders = ','.join(['?' for _ in all_extracted_tables])
            table_info_query = f"""
            SELECT TABLE_NAME, TABLE_DESC, FUNCTION_NAME
            FROM HRMS_TABLES
            WHERE TABLE_NAME IN ({placeholders})
            """
            table_infos = execute_query_with_pool(
                table_info_query,
                params=tuple(all_extracted_tables)
            )
            table_info_dict = {info['TABLE_NAME']: info for info in table_infos}
        
        # 建立 session
        session_id = session_manager.create_session(
            topic=topic,
            related_tables=list(all_extracted_tables),
            rule_summary={
                "rules": rules_summary,
                "total_rules": len(rules),
                "total_tables": len(all_extracted_tables)
            }
        )
        
        # 格式化返回結果
        result_text = f"✅ Session 初始化成功！\n\n"
        result_text += f"📋 **Session ID**: `{session_id}`\n"
        result_text += f"🎯 **主題**: {topic}\n"
        result_text += f"📊 **找到規則數**: {len(rules)} 個\n"
        result_text += f"🗂️ **相關表格數**: {len(all_extracted_tables)} 個\n\n"
        
        # 顯示規則摘要
        result_text += f"📋 **規則摘要**:\n"
        for i, rule_summary in enumerate(rules_summary, 1):
            result_text += f"\n{i}. **{rule_summary['title']}**\n"
            result_text += f"   模組: {rule_summary['module']}\n"
            result_text += f"   關鍵字: {rule_summary['keywords']}\n"
            result_text += f"   描述: {rule_summary['description']}\n"
            if rule_summary['extracted_tables']:
                result_text += f"   相關表格: {', '.join(rule_summary['extracted_tables'])}\n"
        
        # 顯示表格清單
        if all_extracted_tables:
            result_text += f"\n🗂️ **相關表格清單** ({len(all_extracted_tables)} 個):\n"
            for i, table_name in enumerate(sorted(all_extracted_tables), 1):
                if table_name in table_info_dict:
                    info = table_info_dict[table_name]
                    table_desc = info.get('TABLE_DESC', '') or '無描述'
                    function_name = info.get('FUNCTION_NAME', '') or '未分類'
                    result_text += f"{i}. **{table_name}** - {table_desc} ({function_name})\n"
                else:
                    result_text += f"{i}. **{table_name}** - 未在 HRMS_TABLES 中註冊\n"
        
        # NOTE: 工具名稱需與 server.py 實際註冊一致，避免引導錯誤
        result_text += (
            f"\n💡 **提示**: 使用 `get_table_columns` 查詢表格結構，"
            f"使用 `analyze_table_joins` 查詢表格關聯關係。"
        )
        
        return format_text_response(result_text)
        
    except Exception as e:
        return format_error_response(f"Session 初始化失敗：{str(e)}", "db_mcp_init")

