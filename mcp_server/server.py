from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from web_research.cache import cache
from web_research.config import settings
from web_research.fetch import discover_links as run_discover_links
from web_research.fetch import read_url as run_read_url
from web_research.search import web_search as run_web_search
from web_research.service import research_web as run_research_web

logger = logging.getLogger(__name__)

settings.validate()

mcp = FastMCP(
    'lmstudio-web-research',
    host=settings.mcp_host,
    port=settings.mcp_port,
    mount_path=settings.mcp_mount_path,
    sse_path=settings.mcp_sse_path,
    message_path=settings.mcp_message_path,
    streamable_http_path=settings.mcp_streamable_http_path,
)


@mcp.custom_route('/health', methods=['GET'], include_in_schema=False)
async def health_check(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            'ok': True,
            'service': 'lmstudio-web-research',
            'transport': settings.mcp_transport,
            'host': settings.mcp_host,
            'port': settings.mcp_port,
            'streamable_http_path': settings.mcp_streamable_http_path,
            'sse_path': settings.mcp_sse_path,
            'cache': cache.stats(),
        }
    )


@mcp.tool()
def web_search(query: str, max_results: int = 10, freshness: str | None = None, site: str | None = None) -> dict:
    '''Search the open web and return normalized result URLs/snippets for follow-up reading.'''
    return run_web_search(query=query, max_results=max_results, freshness=freshness, site=site)


@mcp.tool()
async def read_url(url: str, query: str | None = None, render: bool = False) -> dict:
    '''Read one web page or PDF URL and return extracted text plus query-focused evidence.'''
    return await run_read_url(url=url, query=query, render=render, source_id=1)


@mcp.tool()
async def discover_links(
    url: str,
    query: str | None = None,
    render: bool = False,
    file_types: list[str] | None = None,
    limit: int = 50,
) -> dict:
    '''List links and online files from a page so the model can choose follow-up sources.'''
    return await run_discover_links(url=url, query=query, render=render, file_types=file_types, limit=limit)


@mcp.tool()
async def research_web(
    query: str,
    max_results: int = 8,
    read_top: int = 4,
    freshness: str | None = None,
    site: str | None = None,
    render: bool = False,
) -> dict:
    '''Search the web, read top results, rank evidence, and return citation-ready sources.'''
    return await run_research_web(
        query=query,
        max_results=max_results,
        read_top=read_top,
        freshness=freshness,
        site=site,
        render=render,
    )


if __name__ == '__main__':
    logger.info(
        'Starting MCP server transport=%s host=%s port=%s streamable_http_path=%s sse_path=%s',
        settings.mcp_transport,
        settings.mcp_host,
        settings.mcp_port,
        settings.mcp_streamable_http_path,
        settings.mcp_sse_path,
    )
    mount_path = settings.mcp_mount_path if settings.mcp_transport == 'sse' else None
    mcp.run(transport=settings.mcp_transport, mount_path=mount_path)
