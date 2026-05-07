#!/usr/bin/env python
"""Debug wrapper for MCP server with logging."""
import sys
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from web_research.config import configure_logging
from mcp_server.debug_tools import list_declared_tool_names
from web_research.config import settings

configure_logging(logging.DEBUG)

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    try:
        logger.info("Starting MCP Server...")
        from mcp_server.server import mcp
        logger.info("MCP server imported successfully")
        tool_names = list_declared_tool_names()
        logger.info("Available tools registered (%d): %s", len(tool_names), ", ".join(tool_names))
        logger.info(
            "Transport=%s host=%s port=%s streamable_http_url=http://%s:%s%s sse_url=http://%s:%s%s",
            settings.mcp_transport,
            settings.mcp_host,
            settings.mcp_port,
            settings.mcp_host,
            settings.mcp_port,
            settings.mcp_streamable_http_path,
            settings.mcp_host,
            settings.mcp_port,
            settings.mcp_sse_path,
        )
        logger.info("Server starting - listening for MCP connections...")
        mount_path = settings.mcp_mount_path if settings.mcp_transport == 'sse' else None
        mcp.run(transport=settings.mcp_transport, mount_path=mount_path)
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        raise
