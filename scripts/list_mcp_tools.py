from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.debug_tools import list_declared_tool_names


if __name__ == '__main__':
    print(json.dumps({'tool_count': len(list_declared_tool_names()), 'tools': list_declared_tool_names()}, indent=2))
