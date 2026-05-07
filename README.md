# LM Studio Web Research MCP

Assistant-style web access for local LM Studio models. This server gives models a small online research toolkit: search the open web, read pages or PDF URLs, and collect citation-ready evidence from top results.

This is not a crawler, local file tool, memory tool, or permanent RAG index. It uses free no-key search pages, direct HTTP fetches, optional Chromium rendering, PDF URL extraction, query-focused passage ranking, link discovery, and process-local session caching.

## MCP Tools

- `web_search(query, max_results=10, freshness=None, site=None)`
  - Searches the open web and returns normalized `title`, `url`, `source`, `snippet`, and `rank`.
- `read_url(url, query=None, render=False)`
  - Reads one HTTP/HTTPS page or PDF URL and returns `final_url`, `status_code`, `content_type`, `title`, `summary`, `text`, `links`, and `evidence`. Captcha or anti-bot blocks return `ok=false`, `blocked=true`, `block_type`, and `block_marker`.
- `discover_links(url, query=None, render=False, file_types=None, limit=50)`
  - Pulls links and online files from a page. Use `file_types=["pdf"]` or similar when the model needs source documents.
- `research_web(query, max_results=8, read_top=4, freshness=None, site=None, render=False)`
  - Searches, reads top unique results, ranks evidence, and returns `sources`, `evidence`, `citations`, structured `failures`, `blocked_sources`, and `manual_visit_links`.

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
python -m mcp_server.server
```

Default HTTP endpoint:

```text
http://127.0.0.1:8000/mcp
```

For command-launched MCP clients, use `MCP_TRANSPORT=stdio` as shown in [mcp.json.example](mcp.json.example).

## Recommended Model Flow

1. Use `research_web` for most online questions.
2. Use `web_search` when the model needs to inspect candidate URLs first.
3. Use `read_url` for a specific source the user or search results provide.
4. Use `discover_links` when a page likely contains PDFs, reports, docs, datasets, or follow-up source links.
5. Use `render=True` in `read_url`, `discover_links`, or `research_web` when pages need JavaScript rendering.

## What Was Missing

- Search needed a no-key provider that is less challenge-prone than DuckDuckGo alone. The tool now tries Mojeek first, then DuckDuckGo HTML/lite if available.
- Models needed source discovery, not just text extraction. `read_url` now returns page links, and `discover_links` can filter for PDFs and other online files.
- Research results needed to preserve the search result attached to each fetched source, so models can explain why a source was opened.
- The project needed to stop carrying old crawler/index/database files and settings.

## Configuration

```text
WEB_RESEARCH_LOG_PATH=.runtime/web_research.log
ALLOWED_DOMAINS=
USER_AGENT=Mozilla/5.0 ...
REQUEST_TIMEOUT=25
MAX_CONTENT_CHARS=120000
MCP_TRANSPORT=streamable-http
MCP_HOST=127.0.0.1
MCP_PORT=8000
BROWSER_HEADLESS=true
BROWSER_TIMEOUT_MS=30000
BROWSER_MAX_CONTENT_CHARS=60000
BROWSER_LOCALE=en-US
BROWSER_TIMEZONE_ID=Asia/Calcutta
BROWSER_PROFILE_DIR=
```

`freshness` supports `day`, `week`, `month`, and `year` when the underlying free search page honors the filter. `ALLOWED_DOMAINS` is optional and supports comma-separated wildcard patterns. `BROWSER_PROFILE_DIR` is optional; leave it empty for per-request browser profiles.

`render=True` uses Chromium through Playwright. `BROWSER_HEADLESS=true` keeps that browser in the background; set `BROWSER_HEADLESS=false` only when you need to watch or manually debug an authorized session.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Notes

- Search uses free no-key web pages, so it can be less reliable than a paid search API and may occasionally be challenged or rate-limited.
- Search is live-only. Previously read pages are not stored in or returned from a persistent search index.
- Cache is in-memory only and resets when the MCP process restarts.
- Browser sessions use isolated temporary profiles by default.
- Captcha and anti-bot challenges are reported as structured failures. The tool does not attempt to solve or bypass captchas; `research_web` skips blocked sources and continues with other live results.
- When a source is blocked, `research_web` tries safe same-domain recovery candidates such as print, AMP, PDF, RSS, feed, and sitemap URLs. Successful recovered sources include `recovered_from`; blocked failures include `recovery_attempts`.
- Blocked sources include `manual_handoff` guidance, and `research_web` returns top-level `manual_visit_links` that clients should show to the user. If you are authorized to access a page, open it manually, complete the site check yourself, then retry with an explicit `BROWSER_PROFILE_DIR`.
- Respect site terms, robots policies, and rate limits.

## Captcha and Blocked Pages

When a page is gated by captcha or anti-bot checks, `read_url` returns a structured blocked response:

```json
{
  "ok": false,
  "blocked": true,
  "block_type": "captcha",
  "block_marker": "captcha"
}
```

`research_web` keeps going with other live results and also returns `blocked_sources` and `manual_visit_links`. Clients should show `manual_visit_links` directly to the user so they can open blocked pages in their own browser when they are authorized to do so. The tool does not use proxy rotation, captcha-solving services, or stealth-driver bypasses.

For blocked search results, `research_web` also tries safe same-domain alternates:

- print-friendly query/path variants
- AMP query/path variants
- same-path PDF variants
- same-domain `sitemap.xml`, RSS, and feed endpoints

If one works, the source includes `recovered_from`. If none work, the blocked failure includes `recovery_attempts`.
