"""Shared read-only API used by Vercel Functions and the local server."""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from opening_book import OpeningBookDatabase
from opening_database import OpeningDatabase


WEB_ROOT = Path(__file__).parent / "web"

def _positive_int(value: str, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(value), maximum))
    except ValueError:
        return default


def _game_type(value: str) -> str | None:
    return value if value in {"rated", "casual"} else None


def _date_value(value: str) -> str | None:
    try:
        from datetime import date
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _rating_value(value: str) -> float | None:
    try:
        rating = float(value)
        return rating if math.isfinite(rating) and rating >= 0 else None
    except ValueError:
        return None


def _rating_bands(value: str) -> tuple[str, ...] | None:
    if not value:
        return None
    if value == "none":
        return ()
    allowed = {"under1200", "1200-1299", "1300-1399", "1400-1499", "1500plus"}
    return tuple(band for band in value.split(",") if band in allowed)


def execute_request(database: OpeningDatabase | OpeningBookDatabase, endpoint: str,
                    query: dict[str, list[str]]) -> dict[str, Any]:
    if endpoint == "health":
        if isinstance(database, OpeningBookDatabase):
            return database.health()
        row = database.connection.execute("SELECT COUNT(*) AS games FROM games").fetchone()
        return {"status": "ok", "database": "turso" if database.remote else "sqlite",
                "games": row["games"]}

    if isinstance(database, OpeningBookDatabase):
        if endpoint == "summary":
            return database.summary()
        if endpoint == "roots":
            limit = _positive_int(query.get("limit", ["20"])[0], 20, 100)
            return {"roots": database.roots(limit), "summary": database.summary()}
        if endpoint == "opening":
            position = query.get("position", [""])[0]
            limit = _positive_int(query.get("limit", ["30"])[0], 30, 100)
            return database.opening(position, limit)
        raise KeyError("Endpoint not found")

    player = query.get("player", [""])[0]
    color = query.get("color", [""])[0]
    filters = {
        "game_type": _game_type(query.get("game_type", [""])[0]),
        "date_from": _date_value(query.get("date_from", [""])[0]),
        "date_to": _date_value(query.get("date_to", [""])[0]),
        "rating_min": _rating_value(query.get("rating_min", [""])[0]),
        "rating_max": _rating_value(query.get("rating_max", [""])[0]),
        "rating_bands": _rating_bands(query.get("rating_bands", [""])[0]),
    }
    if endpoint == "summary":
        return database.summary(player, color, **filters)
    if endpoint == "roots":
        limit = _positive_int(query.get("limit", ["20"])[0], 20, 100)
        return {"roots": database.roots(limit, player, color, **filters),
                "summary": database.header_summary(player, color, **filters)}
    if endpoint == "opening":
        position = query.get("position", [""])[0]
        limit = _positive_int(query.get("limit", ["30"])[0], 30, 100)
        return database.opening(position, limit, player, color, **filters)
    raise KeyError("Endpoint not found")


class VercelOpeningHandler(SimpleHTTPRequestHandler):
    """Base class for the small endpoint files under api/."""

    endpoint = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def _write_json(self, data: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        cache = "no-store" if self.endpoint == "health" or status != HTTPStatus.OK else "public, max-age=0, s-maxage=60, stale-while-revalidate=300"
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        self.endpoint = self.endpoint or {
            "/api/health": "health",
            "/api/summary": "summary",
            "/api/roots": "roots",
            "/api/opening": "opening",
        }.get(parsed.path.rstrip("/"), "")
        if not self.endpoint:
            if parsed.path in {"", "/"}:
                self.path = "/index.html"
            super().do_GET()
            return
        database = None
        try:
            query = parse_qs(parsed.query)
            database = OpeningBookDatabase.from_turso()
            data = execute_request(database, self.endpoint, query)
            self._write_json(data)
        except KeyError as error:
            self._write_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            print(f"[openings-api] endpoint={self.endpoint} {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            if self.endpoint == "health":
                message = str(error) if isinstance(error, RuntimeError) else "Connection test failed; see Vercel Runtime Logs."
                self._write_json({
                    "status": "error",
                    "error": type(error).__name__,
                    "message": message,
                    "turso_url_configured": bool(os.environ.get("TURSO_DATABASE_URL")),
                    "turso_token_configured": bool(os.environ.get("TURSO_AUTH_TOKEN")),
                }, HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self._write_json({"error": "Database request failed"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            if database is not None:
                database.close()
