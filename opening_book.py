"""Read-only access to the compact, precomputed openings book.

The archive database deliberately remains in :mod:`opening_database`.  This
module has no game-level tables and is the only database shape used by the web
application in production.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from opening_database import _dict_row, move_notation, square_name


BOOK_SCHEMA_VERSION = 1


def pack_board(board: list[int]) -> bytes:
    """Encode the 81 signed board values as one byte each (0 through 6)."""
    if len(board) != 81 or any(not isinstance(value, int) or value < -3 or value > 3 for value in board):
        raise ValueError("A board must contain 81 values between -3 and 3")
    return bytes(value + 3 for value in board)


def unpack_board(value: bytes | memoryview) -> list[int]:
    raw = bytes(value)
    if len(raw) != 81 or any(square > 6 for square in raw):
        raise ValueError("Invalid packed board in opening book")
    return [square - 3 for square in raw]


def create_book_schema(connection: sqlite3.Connection) -> None:
    """Create the final production schema; the builder is its only writer."""
    connection.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE book_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            schema_version INTEGER NOT NULL,
            book_version TEXT NOT NULL,
            built_at TEXT NOT NULL,
            source_games INTEGER NOT NULL,
            source_moves INTEGER NOT NULL,
            source_positions INTEGER NOT NULL,
            roots INTEGER NOT NULL,
            rated_games INTEGER NOT NULL,
            last_updated TEXT
        );

        CREATE TABLE book_positions (
            position_id INTEGER PRIMARY KEY,
            board BLOB NOT NULL CHECK (length(board) = 81),
            side INTEGER NOT NULL CHECK (side IN (-1, 1)),
            total_samples INTEGER NOT NULL DEFAULT 0,
            blue_rating_average REAL,
            blue_rating_games INTEGER NOT NULL DEFAULT 0,
            red_rating_average REAL,
            red_rating_games INTEGER NOT NULL DEFAULT 0,
            average_rating REAL,
            rating_games INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE book_moves (
            from_position_id INTEGER NOT NULL REFERENCES book_positions(position_id),
            source_square INTEGER NOT NULL CHECK (source_square BETWEEN 0 AND 80),
            target_square INTEGER NOT NULL CHECK (target_square BETWEEN 0 AND 80),
            source_order INTEGER NOT NULL,
            target_order INTEGER NOT NULL,
            next_position_id INTEGER NOT NULL REFERENCES book_positions(position_id),
            samples INTEGER NOT NULL,
            blue_wins INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            red_wins INTEGER NOT NULL,
            resolved_samples INTEGER NOT NULL,
            blue_rating_average REAL,
            blue_rating_games INTEGER NOT NULL,
            red_rating_average REAL,
            red_rating_games INTEGER NOT NULL,
            average_rating REAL,
            rating_games INTEGER NOT NULL,
            PRIMARY KEY (from_position_id, source_square, target_square)
        ) WITHOUT ROWID;

        CREATE INDEX book_moves_by_popularity
            ON book_moves(from_position_id, samples DESC, source_order, target_order);

        CREATE TABLE book_roots (
            position_id INTEGER PRIMARY KEY REFERENCES book_positions(position_id),
            samples INTEGER NOT NULL
        ) WITHOUT ROWID;

        CREATE INDEX book_roots_by_popularity
            ON book_roots(samples DESC, position_id);
    """)


def _ratings(row: dict[str, Any]) -> dict[str, float | int | None]:
    def rounded(value: Any) -> float | None:
        return round(float(value), 1) if value is not None else None

    return {
        "average": rounded(row["average_rating"]),
        "blue": rounded(row["blue_rating_average"]),
        "red": rounded(row["red_rating_average"]),
        "games": int(row["rating_games"]),
    }


class OpeningBookDatabase:
    """SQLite/libSQL reader for an immutable opening book."""

    supports_filters = False

    def __init__(self, path: str | Path | None = None, *, connection: Any = None) -> None:
        self.path = Path(path) if path is not None else None
        self.remote = connection is not None
        if connection is not None:
            self.connection = connection
        else:
            if self.path is None or not self.path.exists():
                raise FileNotFoundError("Opening book not found; run opening_build.py first")
            self.connection = sqlite3.connect(self.path, check_same_thread=False)
            self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.row_factory = _dict_row

    @classmethod
    def from_turso(cls, url: str | None = None, auth_token: str | None = None) -> "OpeningBookDatabase":
        url = url or os.environ.get("TURSO_DATABASE_URL")
        auth_token = auth_token or os.environ.get("TURSO_AUTH_TOKEN")
        if not url or not auth_token:
            raise RuntimeError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required")
        try:
            import turso_serverless
        except ImportError as error:
            raise RuntimeError("Install the production dependencies from requirements.txt") from error
        return cls(connection=turso_serverless.connect(url, auth_token=auth_token))

    @classmethod
    def from_environment(cls, local_path: str | Path) -> "OpeningBookDatabase":
        if os.environ.get("TURSO_DATABASE_URL") or os.environ.get("TURSO_AUTH_TOKEN"):
            return cls.from_turso()
        return cls(local_path)

    def close(self) -> None:
        self.connection.close()

    def _meta(self) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM book_meta WHERE id = 1").fetchone()
        if not row:
            raise RuntimeError("Opening book is incomplete: missing metadata")
        return dict(row)

    def health(self) -> dict[str, Any]:
        meta = self._meta()
        return {"status": "ok", "database": "turso" if self.remote else "sqlite",
                "games": meta["source_games"], "book_version": meta["book_version"]}

    def summary(self) -> dict[str, Any]:
        meta = self._meta()
        return {
            "games": meta["source_games"], "moves": meta["source_moves"],
            "positions": meta["source_positions"], "roots": meta["roots"],
            "rated_games": meta["rated_games"], "last_updated": meta["last_updated"],
            "built_at": meta["built_at"], "book_version": meta["book_version"],
        }

    def roots(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT r.position_id, r.samples, p.board, p.side
            FROM book_roots AS r JOIN book_positions AS p ON p.position_id = r.position_id
            ORDER BY r.samples DESC, r.position_id LIMIT ?
        """, (limit,)).fetchall()
        return [{"id": int(row["position_id"]), "games": int(row["samples"]),
                 "board": unpack_board(row["board"]), "side": int(row["side"])} for row in rows]

    def opening(self, position: str | int, limit: int = 30) -> dict[str, Any]:
        try:
            position_id = int(position)
        except (TypeError, ValueError) as error:
            raise KeyError("Position not found") from error
        current = self.connection.execute("""
            SELECT position_id, board, side, total_samples, blue_rating_average, blue_rating_games,
                   red_rating_average, red_rating_games, average_rating, rating_games
            FROM book_positions WHERE position_id = ?
        """, (position_id,)).fetchone()
        if not current:
            raise KeyError("Position not found")
        board = unpack_board(current["board"])
        rows = self.connection.execute("""
            SELECT source_square, target_square, next_position_id, samples, blue_wins, draws, red_wins,
                   resolved_samples, blue_rating_average, blue_rating_games, red_rating_average,
                   red_rating_games, average_rating, rating_games
            FROM book_moves WHERE from_position_id = ?
            ORDER BY samples DESC, source_order, target_order LIMIT ?
        """, (position_id, limit)).fetchall()
        total = int(current["total_samples"])
        moves = []
        for row in rows:
            source = square_name(int(row["source_square"]))
            target = square_name(int(row["target_square"]))
            moves.append({
                "move": move_notation(board[int(row["source_square"])], source, target),
                "source": source, "target": target, "next": int(row["next_position_id"]),
                "games": int(row["samples"]),
                "share": round(int(row["samples"]) * 100 / total, 1) if total else 0,
                "wdl": {"blue": int(row["blue_wins"]), "draws": int(row["draws"]),
                        "red": int(row["red_wins"])},
                "resolved": int(row["resolved_samples"]), "ratings": _ratings(row),
            })
        return {"id": int(current["position_id"]), "board": board, "side": int(current["side"]),
                "total": total, "ratings": _ratings(current), "moves": moves}
