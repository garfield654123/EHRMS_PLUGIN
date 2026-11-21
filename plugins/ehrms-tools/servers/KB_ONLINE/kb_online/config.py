# -*- coding: utf-8 -*-
"""線上知識庫 MCP 的設定：全部從環境變數讀，啟動時讀一次。

環境變數的來源，前面的優先：
1. 啟動時已經在環境裡的值
2. KB_ENV_FILE 指到的檔案；沒指定時讀 ~/.ehrms-tools/.env（與本機 plugin 共用）
3. ~/.claude/settings.json 的 env：只補資料庫與 Jira 連線設定（本機 plugin 也是這樣讀），
   讓已經裝好 plugin 的電腦不用再設定一次
"""
import json
import os
from pathlib import Path

SERVER_VERSION = "0.1.0"
SEARCH_VERSION = "keyword-1"   # 檢索方式版本；加入 RAG 後要換，使用紀錄才分得出前後


CONNECTION_KEYS = ("MSSQL_SERVER", "MSSQL_DATABASE", "MSSQL_USERNAME", "MSSQL_PASSWORD",
                   "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")


def load_env_file(home=None):
    """已經在環境裡的值優先，檔案只補沒有的。"""
    home = Path(home) if home is not None else Path.home()
    explicit = os.getenv("KB_ENV_FILE")
    path = Path(explicit).expanduser() if explicit else home / ".ehrms-tools" / ".env"
    if path.is_file():
        from dotenv import dotenv_values
        for key, value in dotenv_values(path, encoding="utf-8-sig", interpolate=False).items():
            if value and not os.getenv(key):
                os.environ[key] = value
    elif explicit:
        raise ValueError(f"KB_ENV_FILE 指到的檔案不存在：{path}")

    if all(os.getenv(k) for k in CONNECTION_KEYS):
        return
    try:
        settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8-sig"))
        values = settings.get("env", {}) if isinstance(settings, dict) else {}
    except (OSError, ValueError):
        return
    if isinstance(values, dict):
        for key in CONNECTION_KEYS:
            if isinstance(values.get(key), str) and values[key].strip() and not os.getenv(key):
                os.environ[key] = values[key]


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
