#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""啟動 EHRMS 線上知識庫 MCP：py -3 servers/KB_ONLINE/run_server.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb_online.server import main  # noqa: E402

if __name__ == "__main__":
    main()
