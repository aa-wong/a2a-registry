"""AWS Lambda entrypoints for the Agent Registry MCP server.

`handler`  — Streamable HTTP MCP endpoint (API Gateway -> Mangum -> FastMCP).
             Stateless + JSON-response mode: every Lambda instance can serve
             any request, and no SSE stream is held open (API Gateway buffers
             responses, so streaming wouldn't survive the trip anyway).
`refresh`  — EventBridge-scheduled liveness sweep. The in-process
             fire-and-forget refresh in server.py dies when Lambda freezes
             post-response, so the schedule does the real health checking.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from mangum import Mangum  # noqa: E402

from agent_registry.server import mcp  # noqa: E402

mcp.settings.stateless_http = True
mcp.settings.json_response = True

app = mcp.streamable_http_app()
# lifespan="auto" runs Starlette startup on cold start, which boots the
# StreamableHTTPSessionManager the app routes requests through.
handler = Mangum(app, lifespan="auto")


def refresh(event, context):
    from agent_registry import health

    asyncio.run(health.refresh_stale())
    return {"ok": True}
