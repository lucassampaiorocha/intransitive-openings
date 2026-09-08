"""Build an immutable, compact production opening book from an archive."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from opening_book import BOOK_SCHEMA_VERSION, create_book_schema, pack_board
from opening_database import square_index


ROOT = Path(__file__).parent
DEFAULT_ARCHIVE = ROOT / "data" / "openings_community.sqlite3"
DEFAULT_BOOK = ROOT / "data" / "openings_book.sqlite3"
BATCH_SIZE = 5_000


def _batches(values: Iterable[tuple], size: int = BATCH_SIZE) -> Iterable[list[tuple]]:
    batch: list[tuple] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _packed_board(board_json: str) -> bytes:
    board = json.loads(board_json)
    if not isinstance(board, list):
        raise ValueError("Archive contains a non-list board")
    return pack_board(board)


def _notation_order(square: str) -> int:
    """Match the archive query's lexical `source, target` tie-breaking."""
    return (ord(square[0].upper()) - ord("A")) * 9 + int(square[1]) - 1


def build_book(archive_path: str | Path, output_path: str | Path,
               *, book_version: str | None = None) -> dict[str, int | str | None]:
    """Create ``output_path`` atomically without mutating the source archive."""
    archive = Path(archive_path).resolve()
    output = Path(output_path).resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"Archive database not found: {archive}")
    if archive == output:
        raise ValueError("Archive and output paths must be different")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".building")
    if temporary.exists():
        temporary.unlink()

    connection = sqlite3.connect(temporary)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA page_size = 4096")
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("ATTACH DATABASE ? AS archive", (str(archive),))
        create_book_schema(connection)
        connection.execute("CREATE TEMP TABLE position_map (hash TEXT PRIMARY KEY, position_id INTEGER NOT NULL)")

        source_rows = connection.execute("""
            SELECT p.hash, p.board_json, p.side
            FROM archive.positions AS p
            JOIN (
                SELECT before_hash AS hash FROM archive.game_moves
                UNION SELECT after_hash AS hash FROM archive.game_moves
                UNION SELECT root_hash AS hash FROM archive.games WHERE ply_count > 0
            ) AS used ON used.hash = p.hash
            ORDER BY p.hash
        """)

        def positions() -> Iterable[tuple]:
            for position_id, row in enumerate(source_rows, start=1):
                yield (position_id, _packed_board(row["board_json"]), int(row["side"]), row["hash"])

        for batch in _batches(positions()):
            connection.executemany("""
                INSERT INTO book_positions (position_id, board, side) VALUES (?, ?, ?)
            """, [(position_id, board, side) for position_id, board, side, _ in batch])
            connection.executemany("INSERT INTO position_map (hash, position_id) VALUES (?, ?)",
                                   [(key, position_id) for position_id, _, _, key in batch])
        connection.commit()

        aggregate_rows = connection.execute("""
            SELECT before_map.position_id AS from_id, m.source, m.target,
                   after_map.position_id AS next_id, COUNT(*) AS samples,
                   SUM(CASE WHEN g.result = 1 THEN 1 ELSE 0 END) AS blue_wins,
                   SUM(CASE WHEN g.result = 0 THEN 1 ELSE 0 END) AS draws,
                   SUM(CASE WHEN g.result = -1 THEN 1 ELSE 0 END) AS red_wins,
                   COUNT(g.result) AS resolved_samples,
                   AVG(g.blue_rating) AS blue_rating_average,
                   COUNT(g.blue_rating) AS blue_rating_games,
                   AVG(g.red_rating) AS red_rating_average,
                   COUNT(g.red_rating) AS red_rating_games,
                   AVG((g.blue_rating + g.red_rating) / 2.0) AS average_rating,
                   COUNT(CASE WHEN g.blue_rating IS NOT NULL AND g.red_rating IS NOT NULL THEN 1 END) AS rating_games
            FROM archive.game_moves AS m
            JOIN archive.games AS g ON g.id = m.game_id
            JOIN position_map AS before_map ON before_map.hash = m.before_hash
            JOIN position_map AS after_map ON after_map.hash = m.after_hash
            GROUP BY m.before_hash, m.source, m.target, m.after_hash
        """)

        def moves() -> Iterable[tuple]:
            for row in aggregate_rows:
                yield (
                    int(row["from_id"]), square_index(row["source"]), square_index(row["target"]),
                    _notation_order(row["source"]), _notation_order(row["target"]), int(row["next_id"]),
                    int(row["samples"]), int(row["blue_wins"]), int(row["draws"]), int(row["red_wins"]),
                    int(row["resolved_samples"]), row["blue_rating_average"], int(row["blue_rating_games"]),
                    row["red_rating_average"], int(row["red_rating_games"]), row["average_rating"], int(row["rating_games"]),
                )

        for batch in _batches(moves()):
            connection.executemany("""
                INSERT INTO book_moves (
                    from_position_id, source_square, target_square, source_order, target_order, next_position_id, samples,
                    blue_wins, draws, red_wins, resolved_samples, blue_rating_average,
                    blue_rating_games, red_rating_average, red_rating_games, average_rating, rating_games
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
        connection.commit()

        connection.execute("""
            CREATE TEMP TABLE position_stats AS
            SELECT from_position_id AS position_id, SUM(samples) AS total_samples,
                   SUM(blue_rating_average * blue_rating_games) / NULLIF(SUM(blue_rating_games), 0) AS blue_rating_average,
                   SUM(blue_rating_games) AS blue_rating_games,
                   SUM(red_rating_average * red_rating_games) / NULLIF(SUM(red_rating_games), 0) AS red_rating_average,
                   SUM(red_rating_games) AS red_rating_games,
                   SUM(average_rating * rating_games) / NULLIF(SUM(rating_games), 0) AS average_rating,
                   SUM(rating_games) AS rating_games
            FROM book_moves GROUP BY from_position_id
        """)
        connection.execute("""
            UPDATE book_positions AS p SET
                total_samples = s.total_samples,
                blue_rating_average = s.blue_rating_average, blue_rating_games = s.blue_rating_games,
                red_rating_average = s.red_rating_average, red_rating_games = s.red_rating_games,
                average_rating = s.average_rating, rating_games = s.rating_games
            FROM position_stats AS s WHERE s.position_id = p.position_id
        """)
        connection.execute("""
            INSERT INTO book_roots (position_id, samples)
            SELECT position_map.position_id, COUNT(*)
            FROM archive.games AS g JOIN position_map ON position_map.hash = g.root_hash
            WHERE g.ply_count > 0 GROUP BY g.root_hash
        """)
        counts = connection.execute("""
            SELECT COUNT(*) AS games, COUNT(CASE WHEN blue_rating IS NOT NULL AND red_rating IS NOT NULL THEN 1 END) AS rated_games,
                   MAX(imported_at) AS last_updated
            FROM archive.games WHERE ply_count > 0
        """).fetchone()
        source_moves = connection.execute("SELECT COUNT(*) FROM archive.game_moves").fetchone()[0]
        source_positions = connection.execute("SELECT COUNT(*) FROM book_positions").fetchone()[0]
        roots = connection.execute("SELECT COUNT(*) FROM book_roots").fetchone()[0]
        version = book_version or uuid.uuid4().hex
        built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute("""
            INSERT INTO book_meta VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (BOOK_SCHEMA_VERSION, version, built_at, counts["games"], source_moves, source_positions,
                roots, counts["rated_games"], counts["last_updated"]))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Book foreign-key validation failed: {violations[:3]}")
        connection.commit()
        connection.execute("ANALYZE")
        connection.execute("VACUUM")
        # Turso imports SQLite files only when their persistent journal mode is
        # WAL.  Set it after VACUUM, which otherwise recreates the file header.
        journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
        if journal_mode != "wal":
            raise RuntimeError(f"Could not prepare book with journal_mode=WAL (got {journal_mode!r})")
        result = {"book_version": version, "games": int(counts["games"]), "moves": int(source_moves),
                  "positions": int(source_positions), "roots": int(roots), "last_updated": counts["last_updated"]}
    except Exception:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise
    else:
        connection.close()
        os.replace(temporary, output)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a compact production opening book from an archive SQLite database")
    parser.add_argument("--archive", default=str(DEFAULT_ARCHIVE), help="source archive SQLite file")
    parser.add_argument("--output", default=str(DEFAULT_BOOK), help="production book SQLite file")
    parser.add_argument("--book-version", help="optional release identifier")
    args = parser.parse_args()
    result = build_book(args.archive, args.output, book_version=args.book_version)
    print("Built opening book:", result, "| path:", Path(args.output).resolve())


if __name__ == "__main__":
    main()
