"""Start the Jira MCP over stdio, reusing local Jira settings if needed."""
import asyncio
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def main():
    required = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
    if any(not os.environ.get(key) for key in required):
        settings_path = Path.home() / ".claude" / "settings.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise SystemExit("Unable to read local Jira settings; configure JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN.")
        for key in required:
            value = settings.get("env", {}).get(key)
            if not os.environ.get(key) and isinstance(value, str) and value:
                os.environ[key] = value
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise SystemExit("Missing Jira settings: " + ", ".join(missing))
    os.environ["TRANSPORT"] = "stdio"
    from jira_mcp.server import main_stdio
    asyncio.run(main_stdio())


if __name__ == "__main__":
    main()
