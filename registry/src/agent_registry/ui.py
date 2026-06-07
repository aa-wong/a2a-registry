"""Lightweight registry web backend.

Serves a small JSON API over the existing SQLite registry plus the built
Vite/React app from registry/web/dist.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import cards, db

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
MAX_BODY_BYTES = 32_768


async def _register(card_url: str) -> dict[str, Any]:
    card, resolved_url = await cards.fetch_agent_card(card_url)
    problems = cards.validate_card(card)
    if problems:
        raise ValueError("Invalid agent card: " + "; ".join(problems))
    record = db.upsert_agent(
        card_url=resolved_url,
        endpoint=cards.extract_endpoint(card),
        card=card,
        tags=cards.extract_tags(card),
    )
    return _agent_payload(record)


def _agent_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = cards.summarize(record)
    payload["card_url"] = record.get("card_url")
    payload["registered_at"] = record.get("registered_at")
    return payload


class RegistryWebHandler(BaseHTTPRequestHandler):
    server_version = "AgentRegistryWeb/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_bytes(
        self,
        body: bytes,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        payload: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self._send_bytes(
            json.dumps(payload).encode("utf-8"),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def _send_error_json(
        self,
        message: str,
        *,
        status: HTTPStatus,
    ) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body is too large")
        raw_body = self.rfile.read(length)
        if not raw_body:
            return {}
        value = json.loads(raw_body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_OPTIONS(self) -> None:
        self._send_bytes(b"", content_type="text/plain")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/agents":
            agents = [_agent_payload(record) for record in db.list_agents()]
            self._send_json({"agents": agents})
            return
        if path == "/api/health":
            self._send_json({"ok": True, "db_path": str(db.db_path())})
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/agents":
            self._send_error_json("Not found", status=HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json()
            card_url = str(payload.get("card_url") or "").strip()
            if not card_url:
                raise ValueError("card_url is required")
            agent = asyncio.run(_register(card_url))
        except Exception as exc:
            self._send_error_json(
                f"{type(exc).__name__}: {exc}",
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        self._send_json({"agent": agent}, status=HTTPStatus.CREATED)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        prefix = "/api/agents/"
        if not path.startswith(prefix):
            self._send_error_json("Not found", status=HTTPStatus.NOT_FOUND)
            return
        agent_id = unquote(path[len(prefix) :]).strip()
        if not agent_id:
            self._send_error_json("agent id is required", status=HTTPStatus.BAD_REQUEST)
            return
        if not db.delete_agent(agent_id):
            self._send_error_json("Agent not found", status=HTTPStatus.NOT_FOUND)
            return
        self._send_json({"deleted": True, "id": agent_id})

    def _serve_static(self, request_path: str) -> None:
        if request_path == "/":
            relative = "index.html"
        else:
            relative = unquote(request_path).lstrip("/")

        candidate = (WEB_DIST / relative).resolve()
        dist_root = WEB_DIST.resolve()
        if dist_root not in candidate.parents and candidate != dist_root:
            self._send_error_json("Not found", status=HTTPStatus.NOT_FOUND)
            return
        if not candidate.exists() or candidate.is_dir():
            candidate = WEB_DIST / "index.html"
        if not candidate.exists():
            self._send_bytes(
                b"React app is not built yet. Run `npm install && npm run build` in registry/web.",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                content_type="text/plain; charset=utf-8",
            )
            return

        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send_bytes(candidate.read_bytes(), content_type=content_type)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Registry web backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), RegistryWebHandler)
    print(f"Agent Registry web: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
