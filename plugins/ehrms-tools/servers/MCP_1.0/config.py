"""
Server configuration for the MCP server.

This project references `ServerConfig` from `server.py` but the file may be
absent in some templates. This module provides a minimal, env-driven config
layer without changing any business logic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ServerConfig:
    """Centralized configuration loaded from environment variables."""

    # Server identity
    SERVER_NAME: str = os.getenv("MCP_SERVER_NAME", "simple-mcp-server")

    # HTTP server binding
    HOST: str = os.getenv("MCP_SERVER_HOST", "0.0.0.0")
    PORT: int = _env_int("MCP_SERVER_PORT", 9000)

    # Security
    REQUIRE_AUTH: bool = _env_bool("MCP_REQUIRE_AUTH", False)
    API_KEY: str = os.getenv("MCP_API_KEY", "")
    ALLOWED_IPS: str = os.getenv("MCP_ALLOWED_IPS", "")

    # CORS
    ENABLE_CORS: bool = _env_bool("MCP_ENABLE_CORS", True)
    CORS_ORIGINS: str = os.getenv("MCP_CORS_ORIGINS", "*")

    @staticmethod
    def get_allowed_ips_list() -> List[str]:
        """Return a simple allowlist of client IPs.

        Note: `server.py` currently checks exact string match, so this returns
        plain IPs (no CIDR expansion).
        """

        raw = os.getenv("MCP_ALLOWED_IPS", "").strip()
        if not raw:
            return []
        return [x.strip() for x in raw.split(",") if x.strip()]

    @staticmethod
    def get_cors_origins() -> List[str]:
        raw = os.getenv("MCP_CORS_ORIGINS", "*").strip()
        if not raw or raw == "*":
            return ["*"]
        return [x.strip() for x in raw.split(",") if x.strip()]

