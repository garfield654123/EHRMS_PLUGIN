"""Configuration management for Jira MCP server."""

import os
from dataclasses import dataclass
from typing import List, Optional
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()


@dataclass
class JiraCredentials:
    """Client 端傳入的 Jira 認證資訊"""
    base_url: str
    email: str
    api_token: str

    @property
    def api_base_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/rest/api/3"

    @property
    def auth(self) -> tuple[str, str]:
        return (self.email, self.api_token)

    @staticmethod
    def from_headers(headers: dict) -> Optional["JiraCredentials"]:
        """從 HTTP headers 解析認證資訊（header 名稱不分大小寫）"""
        email = headers.get("x-jira-email")
        token = headers.get("x-jira-token")
        base_url = headers.get("x-jira-base-url")
        if email and token and base_url:
            return JiraCredentials(base_url=base_url, email=email, api_token=token)
        return None


class Config:
    """Jira API 設定類別"""

    def __init__(self):
        self.base_url = os.getenv("JIRA_BASE_URL")
        self.email = os.getenv("JIRA_EMAIL", "ziping.zhou@104.com.tw")
        self.api_token = os.getenv("JIRA_API_TOKEN")
        self.default_user = os.getenv("DEFAULT_USER", "ziping.zhou@104.com.tw")

        # 驗證必要設定
        if not self.base_url:
            raise ValueError("JIRA_BASE_URL 環境變數未設定")
        if not self.api_token:
            raise ValueError("JIRA_API_TOKEN 環境變數未設定")

        # 確保 base_url 不含結尾斜線
        self.base_url = self.base_url.rstrip("/")

    @property
    def api_base_url(self) -> str:
        """取得 API 基礎 URL"""
        return f"{self.base_url}/rest/api/3"

    @property
    def auth(self) -> tuple[str, str]:
        """取得認證資訊 (email, api_token)"""
        return (self.email, self.api_token)


class ServerConfig:
    """MCP 伺服器配置類別 - 支援 stdio 與 HTTP 雙模式"""

    # 伺服器基本設定
    SERVER_NAME = os.getenv("MCP_SERVER_NAME", "jira-mcp-server")
    TRANSPORT = os.getenv("TRANSPORT", "stdio").lower()  # stdio | http
    HOST = os.getenv("MCP_SERVER_HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "7000"))

    # 認證設定
    REQUIRE_AUTH = os.getenv("MCP_REQUIRE_AUTH", "false").lower() == "true"
    API_KEY = os.getenv("MCP_API_KEY", "")
    ALLOWED_IPS = os.getenv("MCP_ALLOWED_IPS", "")

    # CORS 設定
    ENABLE_CORS = os.getenv("MCP_ENABLE_CORS", "true").lower() == "true"
    CORS_ORIGINS = os.getenv("MCP_CORS_ORIGINS", "*")

    @staticmethod
    def get_allowed_ips_list() -> List[str]:
        """取得允許的 IP 列表"""
        if not ServerConfig.ALLOWED_IPS:
            return []
        ips = [ip.strip() for ip in ServerConfig.ALLOWED_IPS.split(",")]
        return [ip for ip in ips if ip]

    @staticmethod
    def get_cors_origins() -> List[str]:
        """取得允許的 CORS 來源列表"""
        if not ServerConfig.ENABLE_CORS:
            return []
        if ServerConfig.CORS_ORIGINS == "*":
            return ["*"]
        origins = [origin.strip() for origin in ServerConfig.CORS_ORIGINS.split(",")]
        return [origin for origin in origins if origin]


# 全域設定實例
config = Config()
