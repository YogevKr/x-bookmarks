"""Minimal MCP server exposing bookmark search tools."""

from __future__ import annotations

import json
import sys

from bookmark_query import (
    bookmark_context,
    collect_stats,
    domain_counts,
    format_context_result,
    format_search_results,
    format_show_result,
    get_index_status,
    list_bookmarks,
    refresh_index,
    search_bookmarks,
    show_bookmark,
)


def _tool_definitions() -> list[dict]:
    return [
        {
            "name": "search_bookmarks",
            "description": "Hybrid search across bookmarks",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                    "author": {"type": "string"},
                    "category": {"type": "string"},
                    "domain": {"type": "string"},
                    "group_by": {"type": "string", "enum": ["category", "author", "domain", "year"]},
                },
                "required": ["query"],
            },
        },
        {
            "name": "list_bookmarks",
            "description": "List bookmarks with filters",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                    "author": {"type": "string"},
                    "category": {"type": "string"},
                    "domain": {"type": "string"},
                    "language": {"type": "string"},
                    "type": {"type": "string"},
                },
            },
        },
        {
            "name": "show_bookmark",
            "description": "Show one bookmark in detail",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "bookmark_context",
            "description": "Show related context around one bookmark",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "limit": {"type": "integer", "default": 5}},
                "required": ["id"],
            },
        },
        {
            "name": "get_stats",
            "description": "Get aggregate bookmark stats",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_status",
            "description": "Get index freshness status",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "refresh_index",
            "description": "Rebuild the bookmark index",
            "inputSchema": {
                "type": "object",
                "properties": {"force": {"type": "boolean", "default": False}},
            },
        },
        {
            "name": "domain_counts",
            "description": "Get external domain counts",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 20}, "category": {"type": "string"}},
            },
        },
    ]


def handle_request(method: str, params: dict | None) -> dict | None:
    params = params or {}
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "x-bookmarks", "version": "0.1.0"},
        }
    if method == "ping":
        return {}
    if method == "resources/list":
        return {
            "resources": [
                {"uri": "x-bookmarks://status", "name": "Index Status", "mimeType": "application/json"},
                {"uri": "x-bookmarks://stats", "name": "Bookmark Stats", "mimeType": "application/json"},
            ]
        }
    if method == "resources/read":
        uri = params.get("uri")
        if uri == "x-bookmarks://status":
            payload = get_index_status()
        elif uri == "x-bookmarks://stats":
            payload = collect_stats()
        else:
            raise ValueError(f"Unknown resource: {uri}")
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False)}]}
    if method == "tools/list":
        return {"tools": _tool_definitions()}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if name == "search_bookmarks":
            result = search_bookmarks(
                arguments["query"],
                author=arguments.get("author"),
                category=arguments.get("category"),
                domain=arguments.get("domain"),
                limit=int(arguments.get("limit", 10)),
                group_by=arguments.get("group_by"),
            )
            return {
                "content": [{"type": "text", "text": format_search_results(result)}],
                "structuredContent": result,
            }
        if name == "list_bookmarks":
            result = list_bookmarks(
                query=arguments.get("query"),
                author=arguments.get("author"),
                category=arguments.get("category"),
                domain=arguments.get("domain"),
                language=arguments.get("language"),
                bookmark_type_value=arguments.get("type"),
                limit=int(arguments.get("limit", 20)),
            )
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "structuredContent": result}
        if name == "show_bookmark":
            result = show_bookmark(arguments["id"])
            return {"content": [{"type": "text", "text": format_show_result(result)}], "structuredContent": result}
        if name == "bookmark_context":
            result = bookmark_context(arguments["id"], limit=int(arguments.get("limit", 5)))
            return {"content": [{"type": "text", "text": format_context_result(result)}], "structuredContent": result}
        if name == "get_stats":
            result = collect_stats()
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "structuredContent": result}
        if name == "get_status":
            result = get_index_status()
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "structuredContent": result}
        if name == "refresh_index":
            result = refresh_index(force=bool(arguments.get("force", False)))
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "structuredContent": result}
        if name == "domain_counts":
            result = domain_counts(limit=int(arguments.get("limit", 20)), category=arguments.get("category"))
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "structuredContent": result}
        raise ValueError(f"Unknown tool: {name}")
    if method in {"notifications/initialized", "shutdown"}:
        return {}
    return None


def _read_message() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        key, value = line.decode("utf-8").split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def run_mcp_server() -> None:
    while True:
        message = _read_message()
        if message is None:
            break
        method = message.get("method")
        request_id = message.get("id")
        try:
            result = handle_request(method, message.get("params"))
            if request_id is not None:
                _write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            if request_id is not None:
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": str(exc)},
                    }
                )
