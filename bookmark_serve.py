"""Tiny local HTTP API for bookmark queries."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from bookmark_query import (
    bookmark_context,
    collect_stats,
    domain_counts,
    get_index_status,
    list_bookmarks,
    refresh_index,
    search_bookmarks,
    show_bookmark,
)


class BookmarkHandler(BaseHTTPRequestHandler):
    server_version = "x-bookmarks/0.1.0"

    def _json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                self._json(200, {"ok": True})
                return
            if parsed.path == "/status":
                self._json(200, get_index_status())
                return
            if parsed.path == "/stats":
                self._json(200, collect_stats())
                return
            if parsed.path == "/search":
                payload = search_bookmarks(
                    query.get("q", [""])[0],
                    author=query.get("author", [None])[0],
                    category=query.get("category", [None])[0],
                    domain=query.get("domain", [None])[0],
                    language=query.get("language", [None])[0],
                    bookmark_type_value=query.get("type", [None])[0],
                    after=query.get("after", [None])[0],
                    before=query.get("before", [None])[0],
                    limit=int(query.get("limit", ["20"])[0]),
                    group_by=query.get("group_by", [None])[0],
                )
                self._json(200, payload)
                return
            if parsed.path == "/list":
                payload = list_bookmarks(
                    query=query.get("q", [None])[0],
                    author=query.get("author", [None])[0],
                    category=query.get("category", [None])[0],
                    domain=query.get("domain", [None])[0],
                    language=query.get("language", [None])[0],
                    bookmark_type_value=query.get("type", [None])[0],
                    after=query.get("after", [None])[0],
                    before=query.get("before", [None])[0],
                    limit=int(query.get("limit", ["20"])[0]),
                    offset=int(query.get("offset", ["0"])[0]),
                    sort=query.get("sort", ["date"])[0],
                )
                self._json(200, payload)
                return
            if parsed.path == "/show":
                self._json(200, show_bookmark(query["id"][0]))
                return
            if parsed.path == "/context":
                self._json(200, bookmark_context(query["id"][0], limit=int(query.get("limit", ["5"])[0])))
                return
            if parsed.path == "/domains":
                payload = domain_counts(
                    category=query.get("category", [None])[0],
                    author=query.get("author", [None])[0],
                    language=query.get("language", [None])[0],
                    bookmark_type_value=query.get("type", [None])[0],
                    limit=int(query.get("limit", ["20"])[0]),
                )
                self._json(200, payload)
                return
            self._json(404, {"error": "not_found"})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/refresh":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body.decode("utf-8"))
            self._json(200, refresh_index(force=bool(payload.get("force", False))))
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def run_http_server(*, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), BookmarkHandler)
    print(f"Serving x-bookmarks on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
