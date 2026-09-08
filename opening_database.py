"""Banco de partidas e grafo de aberturas para Intransitive/RPS2.

Este módulo é deliberadamente independente do motor de análise. Ele conhece
somente as regras necessárias para reproduzir uma partida e criar uma chave
estável para cada posição.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlparse


BOARD_SIZE = 81
FILES = "ABCDEFGHI"
RPS_TO_VALUE = {"R": 1, "P": 2, "S": 3}
VALUE_TO_RPS = {value: key for key, value in RPS_TO_VALUE.items()}
SIDE_TO_VALUE = {"blue": 1, "red": -1}


class ImportError(ValueError):
    """O payload não representa uma partida que possamos reproduzir."""


@dataclass(frozen=True)
class RecordedMove:
    ply: int
    node_id: int
    parent_id: int | None
    side: int
    source: str
    target: str
    before_hash: str
    after_hash: str


@dataclass(frozen=True)
class DecodedGame:
    source_hash: str
    title: str
    source_url: str | None
    payload: dict[str, Any]
    setup: list[list[str]]
    initial_board: list[int]
    initial_side: int
    blue_result: int | None
    blue_player: str | None
    red_player: str | None
    blue_rating: float | None
    red_rating: float | None
    rated: int | None
    played_at: str | None
    root_hash: str
    moves: tuple[RecordedMove, ...]


def square_index(square: str) -> int:
    square = square.upper()
    if len(square) != 2 or square[0] not in FILES or square[1] not in "123456789":
        raise ImportError(f"Casa inválida: {square!r}")
    return (int(square[1]) - 1) * 9 + FILES.index(square[0])


def square_name(index: int) -> str:
    return f"{FILES[index % 9]}{index // 9 + 1}"


def move_notation(piece: int, source: str, target: str) -> str:
    """Notação compacta usada pela comunidade, como ``Se3-f3``."""
    return f"{VALUE_TO_RPS[abs(piece)]}{source.lower()}-{target.lower()}"


def position_hash(board: list[int], side: int) -> str:
    """Hash canônico compartilhável entre partidas e instalações."""
    state = ",".join(str(value) for value in board) + f"|{side}"
    return hashlib.sha256(state.encode("ascii")).hexdigest()


def _decode_token(value: str) -> dict[str, Any]:
    token = parse_qs(urlparse(value).query).get("workshop", [value])[0]
    token += "=" * (-len(token) % 4)
    try:
        decoded = zlib.decompress(base64.b64decode(token)).decode("utf-8")
        data = json.loads(decoded)
    except (ValueError, zlib.error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImportError("URL ou token workshop inválido") from error
    if not isinstance(data, dict):
        raise ImportError("O workshop precisa conter um objeto JSON")
    return data


def parse_source(source: str | dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Aceita URL, token puro ou JSON exportado do workshop."""
    if isinstance(source, dict):
        return source, None
    if not isinstance(source, str) or not source.strip():
        raise ImportError("Informe uma URL workshop, token ou JSON")
    source = source.strip()
    if source.startswith("{"):
        try:
            data = json.loads(source)
        except json.JSONDecodeError as error:
            raise ImportError("JSON inválido") from error
        if not isinstance(data, dict):
            raise ImportError("O JSON precisa ser um objeto")
        return data, None
    parsed = urlparse(source)
    source_url = source if (parsed.scheme in {"http", "https"} and parsed.hostname == "meaf.us"
                            and "workshop=" in source) else None
    return _decode_token(source), source_url


def _setup_board(setup: Any) -> tuple[list[int], list[list[str]]]:
    if not isinstance(setup, list):
        raise ImportError("O workshop não contém setup")
    board = [0] * BOARD_SIZE
    clean_setup: list[list[str]] = []
    for piece in setup:
        if not (isinstance(piece, list) and len(piece) == 3):
            raise ImportError("Peça inválida no setup")
        square, color, kind = piece
        if not isinstance(square, str) or color not in SIDE_TO_VALUE or kind not in RPS_TO_VALUE:
            raise ImportError("Peça inválida no setup")
        index = square_index(square)
        if board[index]:
            raise ImportError(f"Há duas peças em {square.upper()}")
        board[index] = SIDE_TO_VALUE[color] * RPS_TO_VALUE[kind]
        clean_setup.append([square.upper(), color, kind])
    return board, clean_setup


def standard_setup() -> list[list[str]]:
    """Posição inicial das partidas normais do RPS2."""
    return [
        ["B5", "blue", "P"], ["C5", "blue", "S"],
        ["B4", "blue", "R"], ["C4", "blue", "P"], ["D4", "blue", "S"],
        ["C3", "blue", "R"], ["D3", "blue", "P"], ["E3", "blue", "S"],
        ["D2", "blue", "R"], ["E2", "blue", "P"],
        ["E8", "red", "P"], ["F8", "red", "R"],
        ["E7", "red", "S"], ["F7", "red", "P"], ["G7", "red", "R"],
        ["F6", "red", "S"], ["G6", "red", "P"], ["H6", "red", "R"],
        ["G5", "red", "S"], ["H5", "red", "P"],
    ]


def _main_line(nodes_value: Any, cursor: Any) -> list[list[Any]]:
    if not isinstance(nodes_value, list):
        raise ImportError("O workshop não contém nodes")
    nodes = {node[0]: node for node in nodes_value
             if isinstance(node, list) and len(node) >= 5 and isinstance(node[0], int)}
    if not nodes:
        return []
    if cursor not in nodes:
        parents = {node[1] for node in nodes.values() if isinstance(node[1], int)}
        leaves = [node_id for node_id in nodes if node_id not in parents]
        if len(leaves) != 1:
            raise ImportError("Não foi possível identificar a linha principal do workshop")
        cursor = leaves[0]
    path: list[list[Any]] = []
    seen: set[int] = set()
    while cursor in nodes:
        if cursor in seen:
            raise ImportError("Ciclo inválido na árvore de lances")
        seen.add(cursor)
        node = nodes[cursor]
        path.append(node)
        cursor = node[1]
    path.reverse()
    return path


def _beats(attacker: int, defender: int) -> bool:
    return (attacker - defender) % 3 == 1


def _blue_result(payload: dict[str, Any]) -> int | None:
    """Resultado da partida na perspectiva azul: 1, 0, -1 ou desconhecido."""
    winner = payload.get("winner")
    players = payload.get("playernames")
    if isinstance(players, list) and len(players) == 2:
        if winner == players[0]:
            return 1
        if winner == players[1]:
            return -1
    if winner == "blue":
        return 1
    if winner == "red":
        return -1
    if winner in {"draw", "stagnation"} or (winner is None and payload.get("phase") == "done"):
        return 0
    return None


def _rating(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _player_metadata(payload: dict[str, Any]) -> tuple[str | None, str | None, float | None, float | None]:
    players = payload.get("playernames")
    if not (isinstance(players, list) and len(players) == 2 and all(isinstance(player, str) for player in players)):
        return None, None, None, None
    ratings = payload.get("ratingsBefore")
    ratings = ratings if isinstance(ratings, dict) else {}
    return players[0], players[1], _rating(ratings.get(players[0])), _rating(ratings.get(players[1]))


def _game_metadata(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    """Extrai tipo e horário da partida do histórico público."""
    options = payload.get("options")
    ranked = options.get("ranked") if isinstance(options, dict) else None
    rated = int(ranked) if isinstance(ranked, bool) else None
    started = payload.get("startTime")
    if not isinstance(started, str):
        return rated, None
    try:
        played_at = datetime.fromisoformat(started.replace("Z", "+00:00"))
        if played_at.tzinfo is None:
            played_at = played_at.replace(tzinfo=timezone.utc)
        return rated, played_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return rated, None


def _dict_row(cursor: Any, row: tuple[Any, ...]) -> dict[str, Any]:
    """Shared row representation for sqlite3 and the remote libSQL driver."""
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def _rating_summary(row: Mapping[str, Any], average_key: str, games_key: str) -> dict[str, float | int | None]:
    """Formato estável para o rating médio de uma posição ou de uma variante."""
    keys = set(row.keys())
    blue_key = "blue_rating" if "blue_rating" in keys else "blue"
    red_key = "red_rating" if "red_rating" in keys else "red"

    return _rating_values(row[average_key], row[blue_key], row[red_key], row[games_key])


def _rating_values(average: float | None, blue: float | None, red: float | None,
                   games: int | None) -> dict[str, float | int | None]:
    def rounded(value: float | None) -> float | None:
        return round(float(value), 1) if value is not None else None

    return {"average": rounded(average), "blue": rounded(blue), "red": rounded(red),
            "games": int(games or 0)}


def _record_move(board: list[int], side: int, ply: int, node_id: int, parent_id: int | None,
                 source_square: str, target_square: str) -> tuple[RecordedMove, list[int], int]:
    source_index = square_index(source_square)
    target_index = square_index(target_square)
    source_piece = board[source_index]
    target_piece = board[target_index]
    if not source_piece or source_piece * side <= 0:
        raise ImportError(f"Origem inválida em {source_square.upper()} no lance {ply}")
    if max(abs(source_index % 9 - target_index % 9), abs(source_index // 9 - target_index // 9)) != 1:
        raise ImportError(f"Lance não adjacente no lance {ply}")
    if target_piece and (target_piece * side > 0 or not _beats(abs(source_piece), abs(target_piece))):
        raise ImportError(f"Captura inválida em {target_square.upper()} no lance {ply}")
    before_hash = position_hash(board, side)
    after_board = list(board)
    after_board[target_index] = source_piece
    after_board[source_index] = 0
    after_side = -side
    after_hash = position_hash(after_board, after_side)
    return (RecordedMove(ply, node_id, parent_id, side, square_name(source_index), square_name(target_index),
                         before_hash, after_hash), after_board, after_side)


def _decode_workshop_game(payload: dict[str, Any], source_url: str | None) -> DecodedGame:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    board, setup = _setup_board(payload.get("setup"))
    path = _main_line(payload.get("nodes", []), payload.get("cursor"))
    side = 1 if not path or path[0][4] == "blue" else -1
    initial_board = list(board)
    initial_side = side
    root_hash = position_hash(initial_board, initial_side)
    moves: list[RecordedMove] = []

    for ply, node in enumerate(path, start=1):
        node_id, parent_id, source_square, target_square, color = node[:5]
        if color not in SIDE_TO_VALUE or not isinstance(source_square, str) or not isinstance(target_square, str):
            raise ImportError(f"Lance inválido no node {node_id}")
        node_side = SIDE_TO_VALUE[color]
        if node_side != side:
            raise ImportError(f"Alternância de turno inválida no node {node_id}")
        move, board, side = _record_move(board, side, ply, node_id,
                                          parent_id if isinstance(parent_id, int) else None,
                                          source_square, target_square)
        moves.append(move)

    title = payload.get("title")
    return DecodedGame(source_hash, title if isinstance(title, str) and title.strip() else "Partida sem título",
                       source_url, payload, setup, initial_board, initial_side, None, None, None, None, None, None, None,
                       root_hash, tuple(moves))


def _decode_game_history(payload: dict[str, Any], source_url: str | None = None) -> DecodedGame:
    """Converte a resposta pública /game_history do RPS2 para nosso formato."""
    game_id = payload.get("gameID")
    history = payload.get("moveHistory")
    if not isinstance(game_id, str) or not isinstance(history, list):
        raise ImportError("Histórico de partida RPS2 inválido")
    setup = standard_setup()
    board, setup = _setup_board(setup)
    initial_board = list(board)
    initial_side = 1
    moves: list[RecordedMove] = []
    side = initial_side
    for ply, item in enumerate(history, start=1):
        notation = item.get("move") if isinstance(item, dict) else item
        if not isinstance(notation, str) or len(notation) != 4:
            raise ImportError(f"Notação inválida no lance {ply}")
        move, board, side = _record_move(board, side, ply, ply, ply - 1 if ply > 1 else None,
                                          notation[:2], notation[2:])
        moves.append(move)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_hash = hashlib.sha256(f"rps2-game:{game_id}|{canonical}".encode("utf-8")).hexdigest()
    players = payload.get("playernames")
    title = f"Partida {game_id}"
    if isinstance(players, list) and len(players) == 2 and all(isinstance(player, str) for player in players):
        title += f" — {players[0]} vs {players[1]}"
    blue_player, red_player, blue_rating, red_rating = _player_metadata(payload)
    rated, played_at = _game_metadata(payload)
    return DecodedGame(source_hash, title, source_url or f"https://meaf.us/rps2/?gameID={game_id}", payload,
                       setup, initial_board, initial_side, _blue_result(payload),
                       blue_player, red_player, blue_rating, red_rating,
                       rated, played_at,
                       position_hash(initial_board, initial_side), tuple(moves))


def decode_game(source: str | dict[str, Any]) -> DecodedGame:
    payload, source_url = parse_source(source)
    if "gameID" in payload and "moveHistory" in payload:
        return _decode_game_history(payload, source_url)
    return _decode_workshop_game(payload, source_url)


class OpeningDatabase:
    """SQLite local, Turso remoto e consultas do explorador de aberturas."""

    def __init__(self, path: str | Path | None = None, *, connection: Any = None,
                 initialize: bool = True) -> None:
        self.path = Path(path) if path is not None else None
        self.remote = connection is not None
        self.connection = connection or sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = _dict_row
        if not self.remote:
            self.connection.execute("PRAGMA foreign_keys = ON")
        if initialize:
            self._create_schema()

    @classmethod
    def from_turso(cls, url: str | None = None, auth_token: str | None = None) -> "OpeningDatabase":
        url = url or os.environ.get("TURSO_DATABASE_URL")
        auth_token = auth_token or os.environ.get("TURSO_AUTH_TOKEN")
        if not url or not auth_token:
            raise RuntimeError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required")
        try:
            import turso_serverless
        except ImportError as error:
            raise RuntimeError("Install the production dependencies from requirements.txt") from error
        connection = turso_serverless.connect(url, auth_token=auth_token)
        return cls(connection=connection, initialize=False)

    @classmethod
    def from_environment(cls, local_path: str | Path) -> "OpeningDatabase":
        if os.environ.get("TURSO_DATABASE_URL") or os.environ.get("TURSO_AUTH_TOKEN"):
            return cls.from_turso()
        return cls(local_path)

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY,
                source_hash TEXT NOT NULL UNIQUE,
                source_url TEXT,
                title TEXT NOT NULL,
                setup_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                root_hash TEXT NOT NULL,
                ply_count INTEGER NOT NULL,
                result INTEGER CHECK (result IN (-1, 0, 1)),
                blue_player TEXT,
                red_player TEXT,
                blue_rating REAL,
                red_rating REAL,
                rated INTEGER CHECK (rated IN (0, 1)),
                played_at TEXT,
                imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS positions (
                hash TEXT PRIMARY KEY,
                board_json TEXT NOT NULL,
                side INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS game_moves (
                game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                ply INTEGER NOT NULL,
                node_id INTEGER NOT NULL,
                parent_id INTEGER,
                side INTEGER NOT NULL,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                before_hash TEXT NOT NULL REFERENCES positions(hash),
                after_hash TEXT NOT NULL REFERENCES positions(hash),
                PRIMARY KEY (game_id, ply)
            );
            CREATE INDEX IF NOT EXISTS idx_games_root ON games(root_hash);
            CREATE INDEX IF NOT EXISTS idx_moves_before ON game_moves(before_hash);
        """)
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(games)")}
        migrations = {"result": "INTEGER", "blue_player": "TEXT", "red_player": "TEXT",
                      "blue_rating": "REAL", "red_rating": "REAL", "rated": "INTEGER",
                      "played_at": "TEXT"}
        for column, definition in migrations.items():
            if column not in columns:
                self.connection.execute(f"ALTER TABLE games ADD COLUMN {column} {definition}")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_games_blue_player ON games(blue_player)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_games_red_player ON games(red_player)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_games_source_url ON games(source_url)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_games_rated ON games(rated)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_games_played_at ON games(played_at)")
        self.connection.execute("""CREATE INDEX IF NOT EXISTS idx_moves_opening
                                ON game_moves(before_hash, source, target, after_hash, game_id)""")
        self._backfill_game_metadata()
        self.connection.commit()

    def _backfill_game_metadata(self) -> None:
        """Completa metadados de históricos já guardados, quando existirem."""
        rows = self.connection.execute("""
            SELECT id, payload_json FROM games
            WHERE result IS NULL OR blue_player IS NULL OR red_player IS NULL
               OR blue_rating IS NULL OR red_rating IS NULL
               OR rated IS NULL OR played_at IS NULL
        """).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                if not isinstance(payload, dict):
                    continue
                result = _blue_result(payload)
                blue_player, red_player, blue_rating, red_rating = _player_metadata(payload)
                rated, played_at = _game_metadata(payload)
            except json.JSONDecodeError:
                continue
            self.connection.execute("""
                UPDATE games SET result = COALESCE(result, ?),
                    blue_player = COALESCE(blue_player, ?), red_player = COALESCE(red_player, ?),
                    blue_rating = COALESCE(blue_rating, ?), red_rating = COALESCE(red_rating, ?),
                    rated = COALESCE(rated, ?), played_at = COALESCE(played_at, ?)
                WHERE id = ?
            """, (result, blue_player, red_player, blue_rating, red_rating, rated, played_at, row["id"]))

    def import_game(self, source: str | dict[str, Any]) -> tuple[int, bool]:
        game = decode_game(source)
        existing = self.connection.execute("SELECT id FROM games WHERE source_hash = ?", (game.source_hash,)).fetchone()
        if existing:
            return int(existing["id"]), False

        board = list(game.initial_board)
        side = game.initial_side
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.connection:
            self._insert_position(board, side)
            cursor_board = board
            cursor_side = side
            result = self.connection.execute(
                """INSERT INTO games
                   (source_hash, source_url, title, setup_json, payload_json, root_hash, ply_count, result,
                    blue_player, red_player, blue_rating, red_rating, rated, played_at, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (game.source_hash, game.source_url, game.title, json.dumps(game.setup),
                 json.dumps(game.payload, ensure_ascii=False), game.root_hash, len(game.moves), game.blue_result,
                 game.blue_player, game.red_player, game.blue_rating, game.red_rating, game.rated, game.played_at, now),
            )
            game_id = int(result.lastrowid)
            for move in game.moves:
                source_index = square_index(move.source)
                target_index = square_index(move.target)
                cursor_board = list(cursor_board)
                cursor_board[target_index] = cursor_board[source_index]
                cursor_board[source_index] = 0
                cursor_side = -cursor_side
                self._insert_position(cursor_board, cursor_side)
                self.connection.execute(
                    """INSERT INTO game_moves
                       (game_id, ply, node_id, parent_id, side, source, target, before_hash, after_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (game_id, move.ply, move.node_id, move.parent_id, move.side, move.source,
                     move.target, move.before_hash, move.after_hash),
                )
        return game_id, True

    def existing_game_ids(self, game_ids: Iterable[str]) -> set[str]:
        """Return public game IDs already imported, using one indexed query."""
        ids = [game_id for game_id in game_ids if game_id]
        if not ids:
            return set()
        urls = [f"https://meaf.us/rps2/?gameID={game_id}" for game_id in ids]
        placeholders = ",".join("?" for _ in urls)
        rows = self.connection.execute(
            f"SELECT source_url FROM games WHERE source_url IN ({placeholders})", tuple(urls)
        ).fetchall()
        prefix = "https://meaf.us/rps2/?gameID="
        return {row["source_url"][len(prefix):] for row in rows
                if isinstance(row["source_url"], str) and row["source_url"].startswith(prefix)}

    def _insert_position(self, board: list[int], side: int) -> None:
        key = position_hash(board, side)
        self.connection.execute(
            "INSERT OR IGNORE INTO positions (hash, board_json, side) VALUES (?, ?, ?)",
            (key, json.dumps(board, separators=(",", ":")), side),
        )

    @staticmethod
    def _filter_clause(player: str | None, color: str | None = None, game_type: str | None = None,
                       date_from: str | None = None, date_to: str | None = None,
                       rating_min: float | None = None, rating_max: float | None = None,
                       rating_bands: tuple[str, ...] | None = None,
                       alias: str = "g") -> tuple[str, tuple[Any, ...]]:
        player = (player or "").strip()
        predicates: list[str] = []
        params: list[Any] = []
        if player:
            if color in {"blue", "red"}:
                predicates.append(f"{alias}.{color}_player = ? COLLATE NOCASE")
                params.append(player)
            else:
                predicates.append(f"({alias}.blue_player = ? COLLATE NOCASE OR {alias}.red_player = ? COLLATE NOCASE)")
                params.extend((player, player))
        if game_type == "rated":
            predicates.append(f"{alias}.rated = 1")
        elif game_type == "casual":
            predicates.append(f"{alias}.rated = 0")
        if date_from:
            predicates.append(f"{alias}.played_at >= ?")
            params.append(f"{date_from}T00:00:00+00:00")
        if date_to:
            end = date.fromisoformat(date_to) + timedelta(days=1)
            predicates.append(f"{alias}.played_at < ?")
            params.append(f"{end.isoformat()}T00:00:00+00:00")
        if rating_min is not None:
            predicates.append(f"({alias}.blue_rating + {alias}.red_rating) / 2.0 >= ?")
            params.append(rating_min)
        if rating_max is not None:
            predicates.append(f"({alias}.blue_rating + {alias}.red_rating) / 2.0 <= ?")
            params.append(rating_max)
        if rating_bands is not None:
            average = f"({alias}.blue_rating + {alias}.red_rating) / 2.0"
            ranges = {
                "under1200": f"{average} < 1200",
                "1200-1299": f"({average} >= 1200 AND {average} < 1300)",
                "1300-1399": f"({average} >= 1300 AND {average} < 1400)",
                "1400-1499": f"({average} >= 1400 AND {average} < 1500)",
                "1500plus": f"{average} >= 1500",
            }
            selected = [ranges[band] for band in rating_bands if band in ranges]
            predicates.append(f"({' OR '.join(selected)})" if selected else "0")
        return (f" WHERE {' AND '.join(predicates)}", tuple(params)) if predicates else ("", ())

    def summary(self, player: str | None = None, color: str | None = None, game_type: str | None = None,
                date_from: str | None = None, date_to: str | None = None,
                rating_min: float | None = None, rating_max: float | None = None,
                rating_bands: tuple[str, ...] | None = None) -> dict[str, int]:
        clause, params = self._filter_clause(player, color, game_type, date_from, date_to, rating_min, rating_max, rating_bands)
        played_clause = clause.replace(" WHERE ", " WHERE g.ply_count > 0 AND ") if clause else " WHERE g.ply_count > 0"
        row = self.connection.execute(f"""
            SELECT COUNT(*) AS games,
                   (SELECT COUNT(*) FROM game_moves AS m JOIN games AS g ON g.id = m.game_id {clause}) AS moves,
                   (SELECT COUNT(*) FROM (
                       SELECT m.before_hash AS hash FROM game_moves AS m JOIN games AS g ON g.id = m.game_id {clause}
                       UNION
                       SELECT m.after_hash AS hash FROM game_moves AS m JOIN games AS g ON g.id = m.game_id {clause}
                   )) AS positions,
                   COUNT(DISTINCT g.root_hash) AS roots,
                   COUNT(CASE WHEN g.blue_rating IS NOT NULL AND g.red_rating IS NOT NULL THEN 1 END) AS rated_games,
                   MAX(g.imported_at) AS last_updated
            FROM games AS g {played_clause}
        """, (*params, *params, *params, *params)).fetchone()
        return dict(row)

    def roots(self, limit: int = 20, player: str | None = None, color: str | None = None,
              game_type: str | None = None, date_from: str | None = None, date_to: str | None = None,
              rating_min: float | None = None, rating_max: float | None = None,
              rating_bands: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        clause, params = self._filter_clause(player, color, game_type, date_from, date_to, rating_min, rating_max, rating_bands)
        played_clause = clause.replace(" WHERE ", " WHERE g.ply_count > 0 AND ") if clause else " WHERE g.ply_count > 0"
        rows = self.connection.execute(f"""
            SELECT g.root_hash AS hash, COUNT(*) AS games, p.board_json, p.side
            FROM games AS g JOIN positions AS p ON p.hash = g.root_hash
            {played_clause} GROUP BY g.root_hash ORDER BY games DESC, g.root_hash LIMIT ?
        """, (*params, limit)).fetchall()
        return [{"hash": row["hash"], "games": row["games"], "board": json.loads(row["board_json"]),
                 "side": row["side"]} for row in rows]

    def header_summary(self, player: str | None = None, color: str | None = None,
                       game_type: str | None = None, date_from: str | None = None,
                       date_to: str | None = None, rating_min: float | None = None,
                       rating_max: float | None = None,
                       rating_bands: tuple[str, ...] | None = None) -> dict[str, Any]:
        """Small summary for the latency-sensitive initial page load."""
        clause, params = self._filter_clause(player, color, game_type, date_from, date_to,
                                             rating_min, rating_max, rating_bands)
        played_clause = clause.replace(" WHERE ", " WHERE g.ply_count > 0 AND ") if clause else " WHERE g.ply_count > 0"
        row = self.connection.execute(
            f"SELECT COUNT(*) AS games, MAX(g.imported_at) AS last_updated FROM games AS g {played_clause}",
            params,
        ).fetchone()
        return dict(row)

    def opening(self, position: str, limit: int = 30, player: str | None = None,
                color: str | None = None, game_type: str | None = None, date_from: str | None = None,
                date_to: str | None = None, rating_min: float | None = None,
                rating_max: float | None = None, rating_bands: tuple[str, ...] | None = None) -> dict[str, Any]:
        current = self.connection.execute("SELECT board_json, side FROM positions WHERE hash = ?", (position,)).fetchone()
        if not current:
            raise KeyError("Posição não encontrada")
        clause, params = self._filter_clause(player, color, game_type, date_from, date_to, rating_min, rating_max, rating_bands)
        move_clause = clause.replace(" WHERE ", " AND ")
        rows = self.connection.execute("""
            SELECT m.source, m.target, m.after_hash, COUNT(*) AS games,
                   SUM(CASE WHEN g.result = 1 THEN 1 ELSE 0 END) AS blue_wins,
                   SUM(CASE WHEN g.result = 0 THEN 1 ELSE 0 END) AS draws,
                   SUM(CASE WHEN g.result = -1 THEN 1 ELSE 0 END) AS red_wins,
                   COUNT(g.result) AS resolved,
                   AVG(g.blue_rating) AS blue_rating, AVG(g.red_rating) AS red_rating,
                   AVG((g.blue_rating + g.red_rating) / 2.0) AS average_rating,
                   COUNT(CASE WHEN g.blue_rating IS NOT NULL AND g.red_rating IS NOT NULL THEN 1 END) AS rated_games,
                   SUM(COUNT(*)) OVER () AS total_games,
                   SUM(SUM(g.blue_rating)) OVER () AS position_blue_sum,
                   SUM(COUNT(g.blue_rating)) OVER () AS position_blue_count,
                   SUM(SUM(g.red_rating)) OVER () AS position_red_sum,
                   SUM(COUNT(g.red_rating)) OVER () AS position_red_count,
                   SUM(SUM((g.blue_rating + g.red_rating) / 2.0)) OVER () AS position_average_sum,
                   SUM(COUNT(CASE WHEN g.blue_rating IS NOT NULL AND g.red_rating IS NOT NULL THEN 1 END)) OVER () AS position_rated_games
            FROM game_moves AS m JOIN games AS g ON g.id = m.game_id
            WHERE m.before_hash = ? {move_clause}
            GROUP BY m.source, m.target, m.after_hash
            ORDER BY games DESC, source, target LIMIT ?
        """.format(move_clause=move_clause), (position, *params, limit)).fetchall()
        total = int(rows[0]["total_games"]) if rows else 0
        if rows:
            summary = rows[0]
            rated_games = int(summary["position_rated_games"] or 0)
            blue_count = int(summary["position_blue_count"] or 0)
            red_count = int(summary["position_red_count"] or 0)
            position_ratings = _rating_values(
                float(summary["position_average_sum"]) / rated_games if rated_games else None,
                float(summary["position_blue_sum"]) / blue_count if blue_count else None,
                float(summary["position_red_sum"]) / red_count if red_count else None,
                rated_games,
            )
        else:
            position_ratings = _rating_values(None, None, None, 0)
        board = json.loads(current["board_json"])
        moves = [{"move": move_notation(board[square_index(row["source"])], row["source"], row["target"]), "source": row["source"],
                  "target": row["target"], "next": row["after_hash"], "games": row["games"],
                  "share": round(row["games"] * 100 / total, 1) if total else 0,
                  "wdl": {"blue": row["blue_wins"], "draws": row["draws"], "red": row["red_wins"]},
                  "resolved": row["resolved"], "ratings": _rating_summary(row, "average_rating", "rated_games")}
                 for row in rows]
        return {"hash": position, "board": json.loads(current["board_json"]), "side": current["side"],
                "total": total, "ratings": position_ratings, "moves": moves}

    def games(self, limit: int = 30, player: str | None = None, color: str | None = None,
              game_type: str | None = None, date_from: str | None = None, date_to: str | None = None,
              rating_min: float | None = None, rating_max: float | None = None,
              rating_bands: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        clause, params = self._filter_clause(player, color, game_type, date_from, date_to, rating_min, rating_max, rating_bands)
        rows = self.connection.execute(f"""
            SELECT id, title, source_url, root_hash, ply_count, imported_at
            FROM games AS g {clause} ORDER BY id DESC LIMIT ?
        """, (*params, limit)).fetchall()
        return [dict(row) for row in rows]


def import_sources(database: OpeningDatabase, sources: Iterable[str]) -> dict[str, int]:
    imported = duplicates = failed = 0
    for source in sources:
        try:
            _, was_imported = database.import_game(source)
            imported += int(was_imported)
            duplicates += int(not was_imported)
        except ImportError:
            failed += 1
    return {"imported": imported, "duplicates": duplicates, "failed": failed}
