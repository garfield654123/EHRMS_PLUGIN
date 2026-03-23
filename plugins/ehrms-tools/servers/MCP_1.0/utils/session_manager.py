"""
Session 管理模組
提供工作階段的建立、查詢、更新和過期清理功能
"""
import uuid
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from threading import Lock


class SessionManager:
    """Session 管理器，使用記憶體字典儲存 session"""
    
    def __init__(self, default_ttl: int = 3600):
        """
        初始化 Session 管理器
        
        Args:
            default_ttl: 預設的 session 過期時間（秒），預設 1 小時
        """
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()
        # 從環境變數讀取 TTL，預設 1 小時
        self.default_ttl = int(os.getenv("MCP_SESSION_TTL", str(default_ttl)))
    
    def create_session(
        self,
        topic: str,
        related_tables: List[str],
        rule_summary: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None
    ) -> str:
        """
        建立新的 session
        
        Args:
            topic: 主題關鍵字
            related_tables: 相關表格清單
            rule_summary: 規則摘要資訊（可選）
            ttl: 自訂過期時間（秒），如果為 None 則使用預設值
        
        Returns:
            str: session_id
        """
        session_id = str(uuid.uuid4())
        now = datetime.now()
        expires_at = now + timedelta(seconds=ttl or self.default_ttl)
        
        session_data = {
            "session_id": session_id,
            "topic": topic,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "related_tables": related_tables,
            "rule_summary": rule_summary or {},
            "queried_tables": {},  # 快取的表格結構資訊
            "query_history": [],   # 查詢歷史
            "db_credentials": {}   # 可選：DB 連線資訊
        }
        
        with self._lock:
            self._sessions[session_id] = session_data
        
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        取得 session 資料
        
        Args:
            session_id: session ID
        
        Returns:
            Dict[str, Any]: session 資料，如果不存在或已過期則返回 None
        """
        with self._lock:
            session = self._sessions.get(session_id)
            
            if not session:
                return None
            
            # 檢查是否過期
            expires_at = datetime.fromisoformat(session["expires_at"])
            if datetime.now() > expires_at:
                # 已過期，刪除 session
                del self._sessions[session_id]
                return None
            
            return session
    
    def update_session(self, session_id: str, updates: Dict[str, Any]) -> bool:
        """
        更新 session 資料
        
        Args:
            session_id: session ID
            updates: 要更新的資料（字典）
        
        Returns:
            bool: 是否更新成功
        """
        with self._lock:
            session = self._sessions.get(session_id)
            
            if not session:
                return False
            
            # 檢查是否過期
            expires_at = datetime.fromisoformat(session["expires_at"])
            if datetime.now() > expires_at:
                del self._sessions[session_id]
                return False
            
            # 更新資料
            session.update(updates)
            return True
    
    def cache_table_info(self, session_id: str, table_name: str, table_info: Dict[str, Any]) -> bool:
        """
        快取表格結構資訊到 session
        
        Args:
            session_id: session ID
            table_name: 表格名稱
            table_info: 表格結構資訊
        
        Returns:
            bool: 是否快取成功
        """
        with self._lock:
            session = self._sessions.get(session_id)
            
            if not session:
                return False
            
            # 檢查是否過期
            expires_at = datetime.fromisoformat(session["expires_at"])
            if datetime.now() > expires_at:
                del self._sessions[session_id]
                return False
            
            # 快取表格資訊
            if "queried_tables" not in session:
                session["queried_tables"] = {}
            
            session["queried_tables"][table_name.upper()] = table_info
            return True
    
    def get_cached_table_info(self, session_id: str, table_name: str) -> Optional[Dict[str, Any]]:
        """
        取得快取的表格結構資訊
        
        Args:
            session_id: session ID
            table_name: 表格名稱
        
        Returns:
            Dict[str, Any]: 表格結構資訊，如果不存在則返回 None
        """
        session = self.get_session(session_id)
        
        if not session:
            return None
        
        return session.get("queried_tables", {}).get(table_name.upper())
    
    def add_query_history(self, session_id: str, query: str, result_summary: Optional[str] = None) -> bool:
        """
        新增查詢歷史到 session
        
        Args:
            session_id: session ID
            query: SQL 查詢語句
            result_summary: 結果摘要（可選）
        
        Returns:
            bool: 是否新增成功
        """
        with self._lock:
            session = self._sessions.get(session_id)
            
            if not session:
                return False
            
            # 檢查是否過期
            expires_at = datetime.fromisoformat(session["expires_at"])
            if datetime.now() > expires_at:
                del self._sessions[session_id]
                return False
            
            # 新增查詢歷史
            if "query_history" not in session:
                session["query_history"] = []
            
            history_entry = {
                "query": query,
                "timestamp": datetime.now().isoformat(),
                "result_summary": result_summary
            }
            
            session["query_history"].append(history_entry)
            
            # 限制歷史記錄數量（最多保留 50 筆）
            if len(session["query_history"]) > 50:
                session["query_history"] = session["query_history"][-50:]
            
            return True
    
    def delete_session(self, session_id: str) -> bool:
        """
        刪除 session
        
        Args:
            session_id: session ID
        
        Returns:
            bool: 是否刪除成功
        """
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False
    
    def cleanup_expired_sessions(self) -> int:
        """
        清理所有過期的 session
        
        Returns:
            int: 清理的 session 數量
        """
        now = datetime.now()
        expired_ids = []
        
        with self._lock:
            for session_id, session in self._sessions.items():
                expires_at = datetime.fromisoformat(session["expires_at"])
                if now > expires_at:
                    expired_ids.append(session_id)
            
            for session_id in expired_ids:
                del self._sessions[session_id]
        
        return len(expired_ids)
    
    def get_session_count(self) -> int:
        """
        取得目前有效的 session 數量
        
        Returns:
            int: session 數量
        """
        self.cleanup_expired_sessions()
        return len(self._sessions)


# 全域 Session 管理器實例
_session_manager = SessionManager()


def get_session_manager() -> SessionManager:
    """取得全域 Session 管理器實例"""
    return _session_manager


