"""
表格搜尋工具服務
"""
import re
from typing import List, Dict, Any, Set
from utils.db import execute_query_with_pool
from utils.formatter import format_text_response, format_error_response


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


def search_tables(description: str, limit: int = 20) -> List[Dict[str, str]]:
    """
    搜尋表格工具主函式
    
    Args:
        description: 搜尋關鍵字
        limit: 結果數量限制
    
    Returns:
        List[Dict[str, str]]: MCP 格式回應
    """
    try:
        limit_value = limit if limit is not None and limit > 0 else 20
        
        # 將關鍵字按空格拆分，過濾空字串
        keywords = [kw.strip() for kw in description.split() if kw.strip()]
        if not keywords:
            return format_text_response("❌ 請提供至少一個關鍵字。")
        
        # 對每個關鍵字進行 SQL 注入防護
        keywords_escaped = [kw.replace("'", "''") for kw in keywords]
        
        # 動態生成 SQL 查詢（支援多關鍵字匹配）
        # 每個關鍵字在每個欄位中都要匹配（OR 條件）
        keyword_conditions = []
        params_list = [limit_value]  # 第一個參數是 TOP 的 limit
        
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
        SELECT TOP (?) 
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
        
        # 組合所有參數：limit + WHERE 條件參數 + ORDER BY 條件參數
        all_params = tuple(params_list + order_params)
        
        rules = execute_query_with_pool(
            rule_search_query,
            params=all_params
        )
        
        # 如果找到規則，處理規則結果
        if rules:
            result_text = f"🔍 搜尋結果：在 hrms_sys_rule (Category = 'DB') 中找到 {len(rules)} 個相關規則\n\n"
            
            # 收集所有提取的表格名稱（批次處理）
            all_extracted_tables: Set[str] = set()
            rules_with_tables = []
            
            for rule in rules:
                content = rule.get('CONTENT', '') or ''
                extracted_tables = extract_table_names(content)
                all_extracted_tables.update(extracted_tables)
                rules_with_tables.append((rule, extracted_tables))
            
            # 批次查詢所有表格資訊
            table_info_dict = {}
            if all_extracted_tables:
                # 使用參數化查詢（IN 子句需要特殊處理）
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
            
            # 批次查詢所有關聯關係
            relations_dict = {}
            if all_extracted_tables:
                placeholders = ','.join(['?' for _ in all_extracted_tables])
                relation_query = f"""
                SELECT 
                    RELATION_NAME, SOURCE_TABLE_NAME, SOURCE_COLUMN_NAME,
                    TARGET_TABLE_NAME, TARGET_COLUMN_NAME, RELATION_TYPE,
                    RELATION_STRENGTH, CONFIDENCE_LEVEL, RELATION_DESCRIPTION, BUSINESS_RULE
                FROM hrms_table_relation
                WHERE IS_ACTIVE = 1
                  AND (SOURCE_TABLE_NAME IN ({placeholders})
                       OR TARGET_TABLE_NAME IN ({placeholders}))
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
                # 需要重複參數（因為 IN 子句出現兩次）
                relation_params = tuple(list(all_extracted_tables) + list(all_extracted_tables))
                relations = execute_query_with_pool(relation_query, params=relation_params)
                
                # 按表格名稱分組關聯
                for rel in relations:
                    source_table = rel.get('SOURCE_TABLE_NAME', '')
                    target_table = rel.get('TARGET_TABLE_NAME', '')
                    if source_table not in relations_dict:
                        relations_dict[source_table] = []
                    if target_table not in relations_dict:
                        relations_dict[target_table] = []
                    relations_dict[source_table].append(rel)
                    relations_dict[target_table].append(rel)
            
            # 格式化規則結果
            for rule_idx, (rule, extracted_tables) in enumerate(rules_with_tables, 1):
                module = rule.get('MODULE', '') or '未分類'
                category = rule.get('CATEGORY', '') or '未分類'
                title = rule.get('TITLE', '') or '無標題'
                keywords = rule.get('KEYWORDS', '') or '無關鍵字'
                description = rule.get('DESCRIPTION', '') or '無描述'
                content = rule.get('CONTENT', '') or ''
                author = rule.get('AUTHOR', '') or '未知'
                
                result_text += f"📋 **規則 {rule_idx}: {title}**\n"
                result_text += f"   模組: {module} | 分類: {category}\n"
                result_text += f"   關鍵字: {keywords}\n"
                result_text += f"   描述: {description}\n"
                result_text += f"   作者: {author}\n\n"
                
                # 顯示相關表格
                if extracted_tables:
                    result_text += f"   📊 **相關表格** (從規則內容提取，共 {len(extracted_tables)} 個):\n"
                    for i, table_name in enumerate(extracted_tables, 1):
                        if table_name in table_info_dict:
                            info = table_info_dict[table_name]
                            table_desc = info.get('TABLE_DESC', '') or '無描述'
                            function_name = info.get('FUNCTION_NAME', '') or '未分類'
                            result_text += f"   {i}. **{table_name}** - {table_desc} ({function_name})\n"
                        else:
                            result_text += f"   {i}. **{table_name}** - 未在 HRMS_TABLES 中註冊\n"
                    result_text += "\n"
                    
                    # 顯示關聯關係
                    table_relations = []
                    for table_name in extracted_tables:
                        if table_name in relations_dict:
                            table_relations.extend(relations_dict[table_name])
                    
                    # 去重（基於 RELATION_NAME）
                    seen_relations = set()
                    unique_relations = []
                    for rel in table_relations:
                        rel_name = rel.get('RELATION_NAME', '')
                        if rel_name and rel_name not in seen_relations:
                            seen_relations.add(rel_name)
                            unique_relations.append(rel)
                    
                    if unique_relations:
                        result_text += f"   🔗 **表格關聯關係** (從 hrms_table_relation，共 {len(unique_relations)} 個):\n"
                        for rel in unique_relations:
                            source_table = rel.get('SOURCE_TABLE_NAME', '')
                            source_col = rel.get('SOURCE_COLUMN_NAME', '')
                            target_table = rel.get('TARGET_TABLE_NAME', '')
                            target_col = rel.get('TARGET_COLUMN_NAME', '')
                            rel_type = rel.get('RELATION_TYPE', '')
                            rel_strength = rel.get('RELATION_STRENGTH', '')
                            confidence = rel.get('CONFIDENCE_LEVEL', '')
                            rel_desc = rel.get('RELATION_DESCRIPTION', '') or ''
                            business_rule = rel.get('BUSINESS_RULE', '') or ''
                            
                            result_text += f"   - **{source_table}.{source_col}** → **{target_table}.{target_col}**\n"
                            result_text += f"     類型: {rel_type} | 強度: {rel_strength} | 信心度: {confidence}\n"
                            if rel_desc:
                                result_text += f"     描述: {rel_desc}\n"
                            if business_rule:
                                result_text += f"     業務規則: {business_rule}\n"
                        result_text += "\n"
                
                # 顯示規則內容摘要
                if content:
                    content_preview = content[:300] + ('...' if len(content) > 300 else '')
                    result_text += f"   📝 **規則內容摘要**:\n   {content_preview}\n\n"
                
                result_text += "---\n\n"
            
            return format_text_response(result_text)
        
        # 第二步：如果沒有找到規則，fallback 到原本的 HRMS_TABLES 搜尋
        result_text = f"🔍 未在 hrms_sys_rule (Category = 'DB') 中找到相關規則，改為搜尋 HRMS_TABLES...\n\n"
        
        # 為 HRMS_TABLES 搜尋生成多關鍵字條件
        table_keyword_conditions = []
        table_params_list = [limit_value]
        
        for kw_escaped in keywords_escaped:
            like_pattern = f'%{kw_escaped}%'
            table_keyword_conditions.append(f"""
                (table_desc LIKE ? OR table_name LIKE ? OR function_name LIKE ?)
            """)
            table_params_list.extend([like_pattern, like_pattern, like_pattern])
        
        table_where_conditions = " OR ".join(table_keyword_conditions)
        
        # 生成排序條件
        table_order_when_clauses = []
        table_order_params = []
        
        # table_desc 開頭匹配（優先級 1）
        for kw_escaped in keywords_escaped:
            starts_with_pattern = f'{kw_escaped}%'
            table_order_when_clauses.append("WHEN table_desc LIKE ? THEN 1")
            table_order_params.append(starts_with_pattern)
        
        # table_name 開頭匹配（優先級 2）
        for kw_escaped in keywords_escaped:
            starts_with_pattern = f'{kw_escaped}%'
            table_order_when_clauses.append("WHEN table_name LIKE ? THEN 2")
            table_order_params.append(starts_with_pattern)
        
        # function_name 包含匹配（優先級 3）
        for kw_escaped in keywords_escaped:
            like_pattern = f'%{kw_escaped}%'
            table_order_when_clauses.append("WHEN function_name LIKE ? THEN 3")
            table_order_params.append(like_pattern)
        
        table_order_case = "\n                ".join(table_order_when_clauses) if table_order_when_clauses else "1=1 THEN 4"
        
        search_query = f"""
        SELECT TOP (?) function_name, table_desc, table_name
        FROM HRMS_TABLES 
        WHERE ({table_where_conditions})
        ORDER BY 
            CASE 
                {table_order_case}
                ELSE 4
            END,
            table_name
        """
        
        table_all_params = tuple(table_params_list + table_order_params)
        
        results = execute_query_with_pool(
            search_query,
            params=table_all_params
        )
        
        if not results:
            return format_text_response(result_text + f"🔍 未找到與「{description}」相關的表格。")
        
        # 格式化結果
        result_text += f"找到 {len(results)} 個與「{description}」相關的表格：\n\n"
        
        for i, row in enumerate(results, 1):
            function_name = row.get('function_name', '') or '未分類'
            table_desc = row.get('table_desc', '')
            table_name = row.get('table_name', '')
            
            result_text += f"📊 **{i}. {table_name}**\n"
            result_text += f"   模組: {function_name}\n"
            result_text += f"   描述: {table_desc}\n\n"
        
        return format_text_response(result_text)
        
    except Exception as e:
        return format_error_response(f"表格搜尋失敗：{str(e)}", "search_tables")


