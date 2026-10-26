"""Lightweight HTTP client: never imports the server, model, or vector database."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def connect(url):
    async with streamable_http_client(url) as streams:
        async with ClientSession(*streams, read_timeout_seconds=120) as session:
            await session.initialize()
            yield session


async def call(session, name, arguments=None):
    response = await session.call_tool(name, arguments or {})
    if response.is_error:
        raise RuntimeError(f'MCP tool failed: {name}')
    text = ''.join(c.text for c in response.content if c.type == 'text')
    value = json.loads(text)
    if value.get('status') == 'error':
        raise RuntimeError(f'{name}: {value}')
    return value


def completed(progress):
    if progress.get('active') is not False:
        raise RuntimeError('reindex did not report a terminal state')
    result = progress.get('last_result', progress.get('result', {}))
    if progress.get('error') or progress.get('last_error') or result.get('errors', 0):
        raise RuntimeError(f'reindex failed: {progress}')
    if not result or result.get('skipped_reason'):
        raise RuntimeError(f'reindex completion has no successful result: {progress}')
    return result


async def reindex(session, timeout=1800, force=False):
    start = await call(session, 'reindex_documents', {'force': force})
    if start.get('status') not in ('started', 'already_running'):
        raise RuntimeError(f'reindex not accepted: {start}')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        progress = (await call(session, 'get_reindex_status'))['reindex']
        if progress.get('active') is False:
            return completed(progress)
        await asyncio.sleep(3)
    raise TimeoutError('reindex deadline exceeded; server operation may still be running')


async def main(args):
    while True:
        started = time.monotonic()
        try:
            async with connect(args.url) as session:
                result = await reindex(session, args.timeout, args.force) if args.action == 'reindex' else await call(session, args.action)
            print(json.dumps({'status':'success','at':time.time(),'seconds':time.monotonic()-started,'result':result}), flush=True)
        except Exception as exc:
            print(json.dumps({'status':'error','at':time.time(),'error':str(exc)}), flush=True)
            if not args.interval:
                raise
        if not args.interval:
            return
        await asyncio.sleep(args.interval)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8179/mcp')
    parser.add_argument('--action', choices=['reindex','get_index_stats','get_reindex_status','list_documents'], default='reindex')
    parser.add_argument('--interval', type=int, default=0)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--force', action='store_true')
    asyncio.run(main(parser.parse_args()))
