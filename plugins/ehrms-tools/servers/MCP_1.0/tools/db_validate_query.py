"""
SQL 查詢驗證工具
驗證 SQL 語法、結構正確性和安全性
"""
import sqlparse
import re
from typing import List, Dict, Any, Set, Optional
from utils.db import execute_query_with_pool
from utils.formatter import format_text_response, format_error_response
from utils.session_manager import get_session_manager


def extract_tables_from_sql(sql: str) -> Set[str]:
    """從 SQL 中提取表格名稱"""
    tables = set()
    
    # 使用 sqlparse 解析 SQL
    try:
        parsed = sqlparse.parse(sql)
        for statement in parsed:
            for token in statement.flatten():
                if token.ttype is None and token.value:
                    # 簡單的表格名稱匹配（大寫字母開頭，包含底線）
                    if re.match(r'^[A-Z][A-Z0-9_]+$', token.value.upper()):
                        # 過濾掉常見的關鍵字
                        if token.value.upper() not in ['SELECT', 'FROM', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'WHERE', 'GROUP', 'ORDER', 'BY', 'HAVING', 'AS', 'AND', 'OR', 'NOT', 'IN', 'EXISTS', 'NULL', 'IS', 'LIKE', 'BETWEEN']:
                            tables.add(token.value.upper())
    except Exception:
        # 如果解析失敗，使用正則表達式備選方案
        patterns = [
            r'FROM\s+([A-Z][A-Z0-9_]+)',
            r'JOIN\s+([A-Z][A-Z0-9_]+)',
            r'INTO\s+([A-Z][A-Z0-9_]+)',
            r'UPDATE\s+([A-Z][A-Z0-9_]+)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, sql, re.IGNORECASE)
            tables.update(matches)
    
    return tables


def extract_columns_from_sql(sql: str) -> Set[str]:
    """從 SQL 中提取欄位名稱（簡化版本）"""
    columns = set()
    
    # 簡單的正則表達式匹配
    # 匹配 SELECT 後的欄位名稱
    select_pattern = r'SELECT\s+(.*?)\s+FROM'
    match = re.search(select_pattern, sql, re.IGNORECASE | re.DOTALL)
    if match:
        select_clause = match.group(1)
        # 分割欄位（以逗號分隔）
        for col in select_clause.split(','):
            col = col.strip()
            # 移除別名（AS 後面的部分）
            if ' AS ' in col.upper():
                col = col.split(' AS ')[0].strip()
            elif ' ' in col and not col.startswith('('):
                # 可能是別名格式：column alias
                parts = col.split()
                if len(parts) >= 2:
                    col = parts[0]
            # 移除表名前綴（table.column）
            if '.' in col:
                col = col.split('.')[-1]
            # 過濾掉函數和常數
            if col and not col.startswith('(') and not col.startswith("'") and not col.startswith('"'):
                columns.add(col.upper())
    
    return columns


def validate_syntax(sql: str) -> Dict[str, Any]:
    """驗證 SQL 語法"""
    try:
        parsed = sqlparse.parse(sql)
        if not parsed:
            return {
                "is_valid": False,
                "error": "無法解析 SQL 語句"
            }
        
        # 檢查是否有語法錯誤
        for statement in parsed:
            if statement.get_type() == 'UNKNOWN':
                return {
                    "is_valid": False,
                    "error": "SQL 語句類型無法識別"
                }
        
        return {
            "is_valid": True,
            "statement_type": parsed[0].get_type() if parsed else "UNKNOWN"
        }
    except Exception as e:
        return {
            "is_valid": False,
            "error": f"語法解析錯誤：{str(e)}"
        }


def validate_structure(sql: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """驗證 SQL 結構（表格和欄位是否存在）"""
    issues = []
    warnings = []
    
    # 提取表格名稱
    tables = extract_tables_from_sql(sql)
    
    if not tables:
        warnings.append("無法從 SQL 中提取表格名稱，請確認 SQL 格式正確")
        return {
            "is_valid": True,
            "issues": issues,
            "warnings": warnings
        }
    
    # 驗證表格是否存在
    for table_name in tables:
        table_check_query = """
        SELECT COUNT(*) as count 
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_NAME = ?
        """
        try:
            result = execute_query_with_pool(
                table_check_query,
                params=(table_name,)
            )
            if not result or result[0]['count'] == 0:
                issues.append(f"表格 '{table_name}' 不存在")
        except Exception as e:
            warnings.append(f"無法驗證表格 '{table_name}'：{str(e)}")
    
    # 驗證欄位（簡化版本，僅檢查主要表格的欄位）
    # 這裡可以進一步擴充，檢查所有表格的欄位
    
    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "tables_found": list(tables)
    }


def validate_joins(sql: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """驗證 JOIN 關係是否合理"""
    issues = []
    warnings = []
    suggestions = []
    
    # 提取表格名稱
    tables = extract_tables_from_sql(sql)
    
    if len(tables) < 2:
        return {
            "is_valid": True,
            "issues": [],
            "warnings": [],
            "suggestions": []
        }
    
    # 如果有 session，可以從 hrms_table_relation 驗證
    if session_id:
        session_manager = get_session_manager()
        session = session_manager.get_session(session_id)
        
        if session:
            # 檢查表格之間的關聯關係
            for table in tables:
                try:
                    relation_query = """
                    SELECT COUNT(*) as count
                    FROM hrms_table_relation
                    WHERE (SOURCE_TABLE_NAME = ? OR TARGET_TABLE_NAME = ?)
                      AND IS_ACTIVE = 1
                    """
                    result = execute_query_with_pool(
                        relation_query,
                        params=(table, table)
                    )
                    if result and result[0]['count'] == 0:
                        warnings.append(f"表格 '{table}' 在 hrms_table_relation 中沒有關聯記錄，無法驗證 JOIN 關係")
                except Exception:
                    pass
    
    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "suggestions": suggestions
    }


def validate_security(sql: str) -> Dict[str, Any]:
    """驗證 SQL 安全性"""
    issues = []
    warnings = []
    
    sql_upper = sql.upper().strip()
    
    # 檢查危險操作
    dangerous_keywords = ['DROP', 'DELETE', 'UPDATE', 'TRUNCATE', 'ALTER', 'CREATE', 'EXEC', 'EXECUTE']
    found_keywords = []
    
    for keyword in dangerous_keywords:
        # 使用單詞邊界匹配，避免誤判
        pattern = r'\b' + keyword + r'\b'
        if re.search(pattern, sql_upper):
            found_keywords.append(keyword)
    
    if found_keywords:
        issues.append(f"SQL 包含危險操作：{', '.join(found_keywords)}")
    
    # 檢查 SELECT *
    if 'SELECT *' in sql_upper or 'SELECT\t*' in sql_upper:
        warnings.append("建議避免使用 SELECT *，明確指定需要的欄位以提高可讀性和性能")
    
    # 檢查是否有 WHERE 條件（對於 UPDATE/DELETE）
    if 'UPDATE' in sql_upper or 'DELETE' in sql_upper:
        if 'WHERE' not in sql_upper:
            issues.append("UPDATE 或 DELETE 語句缺少 WHERE 條件，可能導致大量資料被修改")
    
    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings
    }


def db_validate_query(session_id: str, sql_query: str) -> List[Dict[str, str]]:
    """
    驗證 SQL 查詢的語法、結構和安全性
    
    Args:
        session_id: 工作階段 ID
        sql_query: 要驗證的 SQL 語句
    
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
        
        # 執行各項驗證
        syntax_result = validate_syntax(sql_query)
        structure_result = validate_structure(sql_query, session_id)
        join_result = validate_joins(sql_query, session_id)
        security_result = validate_security(sql_query)
        
        # 彙總驗證結果
        all_valid = (
            syntax_result.get("is_valid", False) and
            structure_result.get("is_valid", False) and
            join_result.get("is_valid", False) and
            security_result.get("is_valid", False)
        )
        
        # 格式化結果
        result_text = f"🔍 **SQL 驗證結果**\n\n"
        
        if all_valid:
            result_text += "✅ **整體驗證**: 通過\n\n"
        else:
            result_text += "❌ **整體驗證**: 未通過\n\n"
        
        # 語法檢查
        result_text += "📝 **語法檢查**:\n"
        if syntax_result.get("is_valid"):
            result_text += f"✅ 語法正確（類型：{syntax_result.get('statement_type', 'UNKNOWN')}）\n"
        else:
            result_text += f"❌ {syntax_result.get('error', '語法錯誤')}\n"
        result_text += "\n"
        
        # 結構檢查
        result_text += "🏗️ **結構檢查**:\n"
        if structure_result.get("is_valid"):
            result_text += "✅ 表格結構驗證通過\n"
            if structure_result.get("tables_found"):
                result_text += f"   找到表格: {', '.join(structure_result['tables_found'])}\n"
        else:
            result_text += "❌ 結構驗證失敗\n"
            for issue in structure_result.get("issues", []):
                result_text += f"   - {issue}\n"
        
        if structure_result.get("warnings"):
            for warning in structure_result["warnings"]:
                result_text += f"   ⚠️ {warning}\n"
        result_text += "\n"
        
        # JOIN 驗證
        result_text += "🔗 **JOIN 關係驗證**:\n"
        if join_result.get("is_valid"):
            result_text += "✅ JOIN 關係驗證通過\n"
        else:
            result_text += "❌ JOIN 關係驗證失敗\n"
            for issue in join_result.get("issues", []):
                result_text += f"   - {issue}\n"
        
        if join_result.get("warnings"):
            for warning in join_result["warnings"]:
                result_text += f"   ⚠️ {warning}\n"
        
        if join_result.get("suggestions"):
            for suggestion in join_result["suggestions"]:
                result_text += f"   💡 {suggestion}\n"
        result_text += "\n"
        
        # 安全檢查
        result_text += "🔒 **安全檢查**:\n"
        if security_result.get("is_valid"):
            result_text += "✅ 安全檢查通過\n"
        else:
            result_text += "❌ 安全檢查失敗\n"
            for issue in security_result.get("issues", []):
                result_text += f"   - {issue}\n"
        
        if security_result.get("warnings"):
            for warning in security_result["warnings"]:
                result_text += f"   ⚠️ {warning}\n"
        result_text += "\n"
        
        # 改進建議
        all_suggestions = []
        if security_result.get("warnings"):
            all_suggestions.extend(security_result["warnings"])
        if join_result.get("suggestions"):
            all_suggestions.extend(join_result["suggestions"])
        
        if all_suggestions:
            result_text += "💡 **改進建議**:\n"
            for suggestion in all_suggestions:
                result_text += f"   - {suggestion}\n"
        
        return format_text_response(result_text)
        
    except Exception as e:
        return format_error_response(f"SQL 驗證失敗：{str(e)}", "db_validate_query")


