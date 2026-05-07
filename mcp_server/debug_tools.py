"""Debugging and development utilities for MCP server.

Provides tools for:
- Tool introspection and documentation
- Performance profiling and timing
- Enhanced logging and debugging
- Development/testing utilities
"""

from __future__ import annotations

import ast
import logging
import time
import json
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Callable, TypeVar, Optional

logger = logging.getLogger(__name__)
T = TypeVar('T')


@dataclass
class ToolInfo:
    """Information about an MCP tool."""
    name: str
    docstring: Optional[str] = None
    parameters: dict[str, Any] = field(default_factory=dict)
    is_async: bool = False
    line_number: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class TimingInfo:
    """Information about function execution timing."""
    function_name: str
    elapsed_seconds: float
    start_time: float = field(default_factory=time.time)
    end_time: float = field(default_factory=time.time)
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    exception: Optional[str] = None
    result_size: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to dictionary, excluding internal timing fields."""
        data = asdict(self)
        data.pop('start_time', None)
        data.pop('end_time', None)
        return data


class DebugLogger:
    """Enhanced logging with development/debugging features."""

    def __init__(self, name: str = __name__, verbose: bool = False):
        self.logger = logging.getLogger(name)
        self.verbose = verbose
        self.timings: list[TimingInfo] = []

    def debug_log(self, message: str, level: str = 'info', **context) -> dict:
        """Log a debug message with optional context."""
        level_lower = level.lower()
        log_method = getattr(self.logger, level_lower, self.logger.info)

        if context:
            context_str = json.dumps(context, default=str)
            message = f"{message} | context: {context_str}"

        log_method(message)
        
        return {
            'ok': True,
            'level': level_lower,
            'message': message,
            'context': context,
            'timestamp': time.time(),
        }

    def get_timings(self, limit: int = 50, clear: bool = False) -> dict:
        """Get recorded timing information."""
        recent = self.timings[-limit:] if limit else self.timings
        timings_data = [t.to_dict() for t in recent]
        
        if clear:
            self.timings.clear()

        total_time = sum(t.elapsed_seconds for t in recent)
        avg_time = total_time / len(recent) if recent else 0

        return {
            'ok': True,
            'total_calls': len(self.timings),
            'displayed_calls': len(recent),
            'total_elapsed': total_time,
            'average_elapsed': avg_time,
            'timings': timings_data,
        }

    @contextmanager
    def time_operation(self, func_name: str, **kwargs):
        """Context manager to time an operation."""
        timing = TimingInfo(
            function_name=func_name,
            elapsed_seconds=0,
            kwargs=kwargs,
        )
        timing.start_time = time.time()
        try:
            yield timing
        finally:
            timing.end_time = time.time()
            timing.elapsed_seconds = timing.end_time - timing.start_time
            self.timings.append(timing)
            self.logger.debug(
                f"Operation '{func_name}' completed in {timing.elapsed_seconds:.3f}s"
            )


def list_declared_tool_names(server_path: Path | None = None) -> list[str]:
    """List all @mcp.tool decorated functions in the server module.
    
    Args:
        server_path: Path to server.py file. If None, uses server.py in same directory.
        
    Returns:
        List of tool function names
    """
    target = server_path or Path(__file__).with_name('server.py')
    tree = ast.parse(target.read_text(encoding='utf-8'), filename=str(target))
    tools: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                func = decorator.func
            else:
                func = decorator
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == 'mcp' and func.attr == 'tool':
                    tools.append(node.name)
                    break
    return tools


def extract_tool_info(server_path: Path | None = None) -> dict[str, ToolInfo]:
    """Extract detailed information about all MCP tools.
    
    Returns:
        Dictionary mapping tool name to ToolInfo
    """
    target = server_path or Path(__file__).with_name('server.py')
    tree = ast.parse(target.read_text(encoding='utf-8'), filename=str(target))
    tools: dict[str, ToolInfo] = {}

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Check if this is an MCP tool
        is_mcp_tool = False
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                func = decorator.func
            else:
                func = decorator
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == 'mcp' and func.attr == 'tool':
                    is_mcp_tool = True
                    break

        if not is_mcp_tool:
            continue

        # Extract parameters
        parameters: dict[str, Any] = {}
        if node.args:
            for arg in node.args.args:
                parameters[arg.arg] = {
                    'annotation': ast.unparse(arg.annotation) if arg.annotation else None,
                }
            
            # Handle keyword-only args
            for arg in node.args.kwonlyargs:
                parameters[arg.arg] = {
                    'annotation': ast.unparse(arg.annotation) if arg.annotation else None,
                    'keyword_only': True,
                }

        tool_info = ToolInfo(
            name=node.name,
            docstring=ast.get_docstring(node),
            parameters=parameters,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            line_number=node.lineno,
        )
        tools[node.name] = tool_info

    return tools


def get_tool_documentation(server_path: Path | None = None) -> dict:
    """Get formatted documentation for all MCP tools.
    
    Returns:
        Dictionary with tool names as keys and doc info as values
    """
    tools = extract_tool_info(server_path)
    
    documentation: dict[str, dict] = {}
    for tool_name, info in tools.items():
        documentation[tool_name] = {
            'name': info.name,
            'async': info.is_async,
            'description': info.docstring or 'No documentation available',
            'parameters': info.parameters,
            'line_number': info.line_number,
        }

    return {
        'ok': True,
        'tool_count': len(documentation),
        'tools': documentation,
    }


def timing_decorator(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator to automatically time function execution.
    
    Args:
        func: Function to time
        
    Returns:
        Wrapped function that records timing info
    """
    def wrapper(*args, **kwargs) -> T:
        debug_logger = DebugLogger()
        with debug_logger.time_operation(func.__name__, args_count=len(args)):
            return func(*args, **kwargs)
    return wrapper


# Global debug logger instance
_debug_logger = DebugLogger()


def debug_log(message: str, level: str = 'info', **context) -> dict:
    """Global debugging log function for MCP tools."""
    return _debug_logger.debug_log(message, level=level, **context)


def get_timings(limit: int = 50, clear: bool = False) -> dict:
    """Get recorded timing information globally."""
    return _debug_logger.get_timings(limit=limit, clear=clear)
