# -*- coding: utf-8 -*-
"""線上知識庫 MCP 的設定：全部從環境變數讀，啟動時讀一次。

環境變數可以放在 KB_ENV_FILE 指到的檔案，或 ~/.ehrms-tools/.env（與本機 plugin 共用）。
"""
import os
from pathlib import Path

SERVER_VERSION = "0.1.0"
SEARCH_VERSION = "keyword-1"   # 檢索方式版本；加入 RAG 後要換，使用紀錄才分得出前後


def load_env_file():
    """已經在環境裡的值優先，檔案只補沒有的。"""
    explicit = os.getenv("KB_ENV_FILE")
    path = Path(explicit).expanduser() if explicit else Path.home() / ".ehrms-tools" / ".env"
    if not path.is_file():
        if explicit:
            raise ValueError(f"KB_ENV_FILE 指到的檔案不存在：{path}")
        return
    from dotenv import dotenv_values
    for key, value in dotenv_values(path, encoding="utf-8-sig", interpolate=False).items():
        if value and not os.getenv(key):
            os.environ[key] = value


def _list(name, default=""):
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


class Settings:
    def __init__(self):
        self.host = os.getenv("KB_HOST", "127.0.0.1")
        self.port = int(os.getenv("KB_PORT", "8765"))

        # 資料存放：mssql＝正式（HRMS_KB_* 四張表）；sqlite＝表還沒建好之前先在本機累積
        self.store = os.getenv("KB_STORE", "sqlite").lower()
        self.data_dir = Path(os.getenv("KB_DATA_DIR", str(Path.home() / ".ehrms-tools" / "kb-online")))
        self.sqlite_path = self.data_dir / "kb_online.db"
        self.spool_path = self.data_dir / "usage_spool.jsonl"   # 使用紀錄寫不進資料庫時先放這裡

        # 連線用的 Host 標頭白名單（防 DNS rebinding）。部署到主機後要加上主機名稱或 IP，例如 kb.example:8765
        self.allowed_hosts = _list("KB_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*")
        # 前面有反向代理時才開：改信任 X-Forwarded-For 的第一個位址
        self.trust_forwarded = os.getenv("KB_TRUST_FORWARDED", "0") == "1"

        # 身分確認：比對 Jira 名單
        self.jira_base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        self.jira_email = os.getenv("JIRA_EMAIL", "")
        self.jira_token = os.getenv("JIRA_API_TOKEN", "")
        self.email_domains = [d.lower() for d in _list("KB_EMAIL_DOMAINS")]   # 空白＝不限網域

    def mssql_ready(self):
        return all(os.getenv(k) for k in ("MSSQL_SERVER", "MSSQL_DATABASE", "MSSQL_USERNAME", "MSSQL_PASSWORD"))
