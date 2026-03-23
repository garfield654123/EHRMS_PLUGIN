"""Entry point for running the Jira MCP server."""

import asyncio
import sys
from .server import main


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nJira MCP server stopped.")
        sys.exit(0)


run()
