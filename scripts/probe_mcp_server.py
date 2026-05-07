from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def fetch_status(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read(400).decode('utf-8', errors='replace')
            return {
                'url': url,
                'ok': True,
                'status': response.status,
                'content_type': response.headers.get('Content-Type'),
                'body_preview': body[:200],
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(400).decode('utf-8', errors='replace')
        return {
            'url': url,
            'ok': False,
            'status': exc.code,
            'content_type': exc.headers.get('Content-Type'),
            'body_preview': body[:200],
            'error': str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {'url': url, 'ok': False, 'error': str(exc)}


if __name__ == '__main__':
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = sys.argv[2] if len(sys.argv) > 2 else '8000'
    urls = [
        f'http://{host}:{port}/health',
        f'http://{host}:{port}/mcp',
        f'http://{host}:{port}/sse',
    ]
    print(json.dumps({'results': [fetch_status(url) for url in urls]}, indent=2))
