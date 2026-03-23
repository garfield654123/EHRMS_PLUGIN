"""
資料庫連線管理模組
提供連線池、參數化查詢和環境變數管理
"""
import os
import pyodbc
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager
from threading import Lock


class DatabaseConnectionPool:
    """簡單的資料庫連線池，重用連線以提升效能"""
    
    def __init__(self):
        self._connection: Optional[pyodbc.Connection] = None
        self._lock = Lock()
        self._credentials: Optional[Tuple[str, str, str, str]] = None
    
    def _get_available_driver(self) -> str:
        """取得可用的 ODBC 驅動程式"""
        # 優先順序：從新到舊
        preferred_drivers = [
            "ODBC Driver 17 for SQL Server",
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 13 for SQL Server",
            "SQL Server Native Client 11.0",
            "SQL Server"
        ]
        
        available_drivers = pyodbc.drivers()
        
        for driver in preferred_drivers:
            if driver in available_drivers:
                return driver
        
        # 如果都沒有，嘗試找任何包含 SQL Server 的驅動程式
        sql_server_drivers = [d for d in available_drivers if 'SQL Server' in d]
        if sql_server_drivers:
            return sql_server_drivers[0]
        
        # 如果還是沒有，返回預設值（可能會失敗，但至少會給出明確錯誤）
        return "ODBC Driver 17 for SQL Server"
    
    def _get_connection_string(self, server: str, database: str, username: str, password: str) -> str:
        """建立連線字串"""
        driver = self._get_available_driver()
        return f"""
        DRIVER={{{driver}}};
        SERVER={server};
        DATABASE={database};
        UID={username};
        PWD={password};
        TrustServerCertificate=yes;
        """
    
    def _create_connection(self, server: str, database: str, username: str, password: str) -> pyodbc.Connection:
        """建立新的資料庫連線"""
        connection_string = self._get_connection_string(server, database, username, password)
        return pyodbc.connect(connection_string)
    
    def get_connection(
        self, 
        server: Optional[str] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ) -> pyodbc.Connection:
        """
        取得資料庫連線（重用現有連線或建立新連線）
        
        Args:
            server: 伺服器位址（可選，預設從環境變數讀取）
            database: 資料庫名稱（可選，預設從環境變數讀取）
            username: 使用者名稱（可選，預設從環境變數讀取）
            password: 密碼（可選，預設從環境變數讀取）
        
        Returns:
            pyodbc.Connection: 資料庫連線物件
        """
        # 從環境變數或參數獲取連線資訊
        server_name = server or os.getenv('MSSQL_SERVER')
        database_name = database or os.getenv('MSSQL_DATABASE')
        username_name = username or os.getenv('MSSQL_USERNAME')
        password_name = password or os.getenv('MSSQL_PASSWORD')
        
        if not all([server_name, database_name, username_name, password_name]):
            raise ValueError("缺少必要的資料庫連接資訊。請提供參數或設定環境變數。")
        
        current_credentials = (server_name, database_name, username_name, password_name)
        
        with self._lock:
            # 如果連線存在且憑證相同，檢查連線是否有效
            if self._connection and self._credentials == current_credentials:
                try:
                    # 簡單的連線有效性檢查
                    cursor = self._connection.cursor()
                    cursor.execute("SELECT 1")
                    cursor.close()
                    return self._connection
                except (pyodbc.Error, pyodbc.InterfaceError):
                    # 連線已失效，關閉並重新建立
                    try:
                        self._connection.close()
                    except:
                        pass
                    self._connection = None
            
            # 建立新連線
            self._connection = self._create_connection(server_name, database_name, username_name, password_name)
            self._credentials = current_credentials
            return self._connection
    
    def close(self):
        """關閉連線池中的所有連線"""
        with self._lock:
            if self._connection:
                try:
                    self._connection.close()
                except:
                    pass
                self._connection = None
                self._credentials = None
    
    @contextmanager
    def connection(
        self,
        server: Optional[str] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ):
        """
        連線 context manager，自動管理連線生命週期
        
        注意：為了重用連線，此 context manager 不會自動關閉連線
        只有在連線失效或憑證變更時才會重新建立
        """
        conn = self.get_connection(server, database, username, password)
        try:
            yield conn
        except Exception:
            # 如果發生錯誤，標記連線可能已失效
            with self._lock:
                if self._connection == conn:
                    try:
                        self._connection.close()
                    except:
                        pass
                    self._connection = None
                    self._credentials = None
            raise


# 全域連線池實例
_connection_pool = DatabaseConnectionPool()


def get_db_credentials(
    server: Optional[str] = None,
    database: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Tuple[str, str, str, str]:
    """
    取得資料庫憑證（從參數或環境變數）
    
    Returns:
        Tuple[str, str, str, str]: (server, database, username, password)
    
    Raises:
        ValueError: 如果缺少必要的連線資訊
    """
    server_name = server or os.getenv('MSSQL_SERVER')
    database_name = database or os.getenv('MSSQL_DATABASE')
    username_name = username or os.getenv('MSSQL_USERNAME')
    password_name = password or os.getenv('MSSQL_PASSWORD')
    
    if not all([server_name, database_name, username_name, password_name]):
        raise ValueError("缺少必要的資料庫連接資訊。請提供參數或設定環境變數。")
    
    return (server_name, database_name, username_name, password_name)


def execute_query(
    connection: pyodbc.Connection,
    query: str,
    params: Optional[tuple] = None
) -> List[Dict[str, Any]]:
    """
    執行 MSSQL 查詢並返回結果（支援參數化查詢）
    
    Args:
        connection: 資料庫連線
        query: SQL 查詢語句
        params: 查詢參數（可選，用於參數化查詢）
    
    Returns:
        List[Dict[str, Any]]: 查詢結果（字典列表）
    """
    cursor = connection.cursor()
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        # 獲取列名
        columns = [column[0] for column in cursor.description] if cursor.description else []
        
        # 獲取資料
        rows = cursor.fetchall()
        
        # 轉換為字典列表
        result = []
        for row in rows:
            result.append({columns[i]: row[i] for i in range(len(columns))})
        
        return result
    finally:
        cursor.close()


def execute_query_with_pool(
    query: str,
    params: Optional[tuple] = None,
    server: Optional[str] = None,
    database: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    使用連線池執行查詢（便利函式）
    
    Args:
        query: SQL 查詢語句
        params: 查詢參數（可選）
        server: 伺服器位址（可選）
        database: 資料庫名稱（可選）
        username: 使用者名稱（可選）
        password: 密碼（可選）
    
    Returns:
        List[Dict[str, Any]]: 查詢結果
    """
    with _connection_pool.connection(server, database, username, password) as conn:
        return execute_query(conn, query, params)


def test_connection(
    server: str,
    database: str,
    username: str,
    password: str
) -> bool:
    """
    測試資料庫連線
    
    Args:
        server: 伺服器位址
        database: 資料庫名稱
        username: 使用者名稱
        password: 密碼
    
    Returns:
        bool: 連線是否成功
    
    Raises:
        Exception: 如果連線失敗
    """
    conn = _connection_pool._create_connection(server, database, username, password)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        return True
    finally:
        conn.close()






