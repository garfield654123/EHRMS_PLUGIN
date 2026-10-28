"""Launch the shared EHRMS MCP servers for local desktop clients."""

import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DB_KEYS = ("MSSQL_SERVER", "MSSQL_DATABASE", "MSSQL_USERNAME", "MSSQL_PASSWORD")
JIRA_KEYS = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
SERVER_CONFIG = {
    "database": ("MCP_1.0", "server", DB_KEYS),
    "jira": ("JIRA_MCP", "jira_mcp.server", JIRA_KEYS),
    "memory": ("MEMORY", "server", DB_KEYS),
}


def has_value(value):
    return isinstance(value, str) and bool(value.strip()) and not (
        value.startswith("${") and value.endswith("}")
    )


def load_environment(server_name, environ=None, home=None):
    if environ is None:
        environ = os.environ
    home = Path(home) if home is not None else Path.home()
    required = SERVER_CONFIG[server_name][2]
    keys = required + (("JIRA_EMAIL", "MEMORY_USER") if server_name == "memory" else ())
    for key in keys:
        if key in environ and not has_value(environ[key]):
            del environ[key]

    explicit_file = environ.get("EHRMS_ENV_FILE")
    env_file = Path(explicit_file).expanduser() if explicit_file else home / ".ehrms-tools" / ".env"
    if explicit_file and not env_file.is_file():
        raise ValueError("EHRMS_ENV_FILE does not point to an existing file.")
    if env_file.is_file():
        from dotenv import dotenv_values

        values = dotenv_values(env_file, encoding="utf-8-sig", interpolate=False)
        for key in keys:
            if key not in environ and has_value(values.get(key)):
                environ[key] = values[key]

    if any(key not in environ for key in keys):
        settings_path = home / ".claude" / "settings.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            settings = {}
        values = settings.get("env", {}) if isinstance(settings, dict) else {}
        if isinstance(values, dict):
            for key in keys:
                if key not in environ and has_value(values.get(key)):
                    environ[key] = values[key]

    missing = [key for key in required if key not in environ]
    if missing:
        raise ValueError("Missing EHRMS settings: " + ", ".join(missing))
    environ["MCP_MODE"] = "stdio"
    environ["TRANSPORT"] = "stdio"
    return environ


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", choices=SERVER_CONFIG)
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    try:
        load_environment(args.server)
    except (OSError, ValueError) as error:
        parser.exit(2, str(error) + "\n")
    directory, module_name, _required = SERVER_CONFIG[args.server]
    sys.path.insert(0, str(PLUGIN_ROOT / "servers" / directory))
    module = importlib.import_module(module_name)
    try:
        asyncio.run(module.main_stdio())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
