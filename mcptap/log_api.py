"""HTTP handlers for the proxy log viewer API."""

from typing import Optional

from aiohttp import web  # type: ignore

from mcptap.log_store import TIME_RANGE_MAP, TIME_RANGES, LogStore
from mcptap.settings import LOGGER


async def handle_logs_list(request: web.Request) -> web.Response:
    """Return a paginated list of request logs.

    Query parameters:
        range   — time range preset (15m, 30m, 1h, 3h, 24h, 48h, 1w)
        before  — cursor: return rows older than this Unix timestamp
        limit   — page size (default 50, max 200)
    """
    log_store: Optional[LogStore] = request.app.get("log_store")
    if log_store is None or not log_store.enabled:
        return web.json_response({"error": "log store is not available"}, status=503)

    range_value = request.query.get("range", "1h")
    range_seconds = TIME_RANGE_MAP.get(range_value)
    if range_seconds is None and range_value:
        return web.json_response({"error": f"invalid range: {range_value}"}, status=400)

    before_param = request.query.get("before")
    before: Optional[float] = None
    if before_param:
        try:
            before = float(before_param)
        except ValueError:
            return web.json_response({"error": "invalid before parameter"}, status=400)

    limit = 50
    limit_param = request.query.get("limit")
    if limit_param:
        try:
            limit = max(1, min(200, int(limit_param)))
        except ValueError:
            pass

    rows, has_more = log_store.query(
        range_seconds=range_seconds,
        before=before,
        limit=limit,
    )

    return web.json_response(
        {
            "rows": rows,
            "has_more": has_more,
            "range_options": TIME_RANGES,
        }
    )


async def handle_log_detail(request: web.Request) -> web.Response:
    """Return the full detail of a single request log entry."""
    log_store: Optional[LogStore] = request.app.get("log_store")
    if log_store is None or not log_store.enabled:
        return web.json_response({"error": "log store is not available"}, status=503)

    log_id = request.match_info.get("log_id", "")
    if not log_id:
        return web.json_response({"error": "missing log id"}, status=400)

    detail = log_store.get_by_id(log_id)
    if detail is None:
        return web.json_response({"error": "log entry not found"}, status=404)

    return web.json_response(detail)


async def serve_logs_page(_request: web.Request) -> web.Response:
    """Serve the log viewer HTML page."""
    import os

    html_path = os.path.join(os.path.dirname(__file__), "static", "logs.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except FileNotFoundError:
        LOGGER.error("Log viewer HTML not found at %s", html_path)
        return web.Response(text="Log viewer not found", status=404)
    return web.Response(text=html_content, content_type="text/html")
