"""Servidor local do banco comunitário de aberturas.

Uso: python opening_server.py
Depois abra http://127.0.0.1:8877
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
import json
import os
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from opening_api import execute_request
from opening_database import OpeningDatabase


ROOT = Path(__file__).parent
WEB_ROOT = ROOT / "web"
LOCAL_DATABASE = ROOT / "data" / "openings_community.sqlite3"
LEGACY_DATABASE = ROOT.parent.parent / "openings_community.sqlite3"


def default_database_path() -> Path:
    configured = os.environ.get("OPENINGS_DATABASE")
    if configured:
        return Path(configured)
    # Temporary compatibility while a running local crawler keeps the old file locked.
    return LOCAL_DATABASE if LOCAL_DATABASE.exists() or not LEGACY_DATABASE.exists() else LEGACY_DATABASE


class OpeningHandler(SimpleHTTPRequestHandler):
    database: OpeningDatabase
    database_lock: threading.Lock
    api_cache: OrderedDict[str, tuple[float, bytes]]
    cors_origin: str

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def send_head(self):
        """Evita 304 sem corpo para os arquivos do front servido localmente."""
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            for header in ("If-Modified-Since", "If-None-Match"):
                if header in self.headers:
                    del self.headers[header]
        return super().send_head()

    def end_headers(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        print("[openings] " + format % args)

    def _write_json(self, body: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        """Entrega uma resposta cacheável, inclusive para um front em outro domínio."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if status == HTTPStatus.OK:
            self.send_header("Cache-Control", "public, max-age=60, stale-while-revalidate=300")
        if self.cors_origin:
            self.send_header("Access-Control-Allow-Origin", self.cors_origin)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._write_json(body, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        endpoints = {"/api/health": "health", "/api/summary": "summary",
                     "/api/roots": "roots", "/api/opening": "opening"}
        endpoint = endpoints.get(parsed.path)
        if endpoint is None:
            super().do_GET()
            return
        try:
            with self.database_lock:
                cached = self.api_cache.get(self.path)
                if cached is not None and cached[0] > time.monotonic():
                    body = cached[1]
                    self.api_cache.move_to_end(self.path)
                else:
                    data = execute_request(self.database, endpoint, query)
                    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                    self.api_cache[self.path] = (time.monotonic() + 60, body)
                    if len(self.api_cache) > 2048:
                        self.api_cache.popitem(last=False)
            self._write_json(body)
        except KeyError as error:
            self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Banco local de aberturas RPS2")
    parser.add_argument("--database", default=str(default_database_path()), help="arquivo SQLite")
    parser.add_argument("--host", default="127.0.0.1", help="endereço de escuta")
    parser.add_argument("--port", type=int, default=8877, help="porta HTTP")
    parser.add_argument("--cors-origin", default="*", help="origem CORS do front publicado; * permite qualquer origem")
    args = parser.parse_args()

    database_path = Path(args.database).resolve()
    if not os.environ.get("TURSO_DATABASE_URL"):
        database_path.parent.mkdir(parents=True, exist_ok=True)
    OpeningHandler.database = OpeningDatabase.from_environment(database_path)
    OpeningHandler.database_lock = threading.Lock()
    OpeningHandler.api_cache = OrderedDict()
    OpeningHandler.cors_origin = args.cors_origin
    server = ThreadingHTTPServer((args.host, args.port), OpeningHandler)
    print(f"Banco de aberturas em http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando.")
    finally:
        server.server_close()
        OpeningHandler.database.close()


if __name__ == "__main__":
    main()
