import base64
import json
import tempfile
import unittest
import zlib
from pathlib import Path
from urllib.parse import urlencode

from opening_api import execute_request
from opening_database import OpeningDatabase, decode_game


def workshop_url(payload):
    token = base64.b64encode(zlib.compress(json.dumps(payload).encode())).decode().rstrip("=")
    return "https://meaf.us/rps2/?" + urlencode({"workshop": token})


PAYLOAD = {
    "title": "Partida de teste",
    "setup": [["B2", "blue", "R"], ["C3", "red", "S"], ["C2", "red", "P"]],
    "nodes": [[1, None, "B2", "C3", "blue", ""], [2, 1, "C2", "B2", "red", ""]],
    "cursor": 2,
}

GAME_HISTORY = {
    "gameID": "example1",
    "playernames": ["BluePlayer", "RedPlayer"],
    "winner": "BluePlayer",
    "phase": "done",
    "ratingsBefore": {"BluePlayer": 1200, "RedPlayer": 1400},
    "options": {"ranked": True},
    "startTime": "2026-01-10T13:00:00-03:00",
    "moveHistory": [{"move": "D4E5"}, {"move": "G5G4"}],
}

SECOND_GAME_HISTORY = {
    "gameID": "example2",
    "playernames": ["AnotherBlue", "BluePlayer"],
    "winner": "BluePlayer",
    "phase": "done",
    "ratingsBefore": {"AnotherBlue": 1100, "BluePlayer": 1300},
    "options": {"ranked": False},
    "startTime": "2026-01-20T13:00:00-03:00",
    "moveHistory": [{"move": "D4E5"}, {"move": "G5G4"}],
}

ZERO_PLY_GAME = {
    "gameID": "example-zero",
    "playernames": ["BluePlayer", "RedPlayer"],
    "winner": "RedPlayer",
    "phase": "done",
    "moveHistory": [],
}


class OpeningDatabaseTests(unittest.TestCase):
    def test_decode_replays_moves(self):
        game = decode_game(workshop_url(PAYLOAD))
        self.assertEqual(game.title, "Partida de teste")
        self.assertEqual([move.source + "-" + move.target for move in game.moves], ["B2-C3", "C2-B2"])
        self.assertNotEqual(game.moves[0].before_hash, game.moves[0].after_hash)

    def test_import_is_idempotent_and_builds_opening(self):
        with tempfile.TemporaryDirectory() as directory:
            database = OpeningDatabase(Path(directory) / "openings.sqlite3")
            try:
                game_id, imported = database.import_game(workshop_url(PAYLOAD))
                self.assertTrue(imported)
                self.assertEqual(game_id, 1)
                _, imported_again = database.import_game(workshop_url(PAYLOAD))
                self.assertFalse(imported_again)
                self.assertEqual(database.summary()["games"], 1)
                self.assertEqual(execute_request(database, "health", {})["database"], "sqlite")
                self.assertEqual(execute_request(database, "summary", {})["games"], 1)
                root = database.roots()[0]
                opening = database.opening(root["hash"])
                self.assertEqual(opening["moves"][0]["move"], "Rb2-c3")
                self.assertEqual(opening["moves"][0]["games"], 1)
            finally:
                database.close()

    def test_decode_public_game_history(self):
        game = decode_game(GAME_HISTORY)
        self.assertEqual(game.title, "Partida example1 — BluePlayer vs RedPlayer")
        self.assertEqual([move.source + "-" + move.target for move in game.moves], ["D4-E5", "G5-G4"])
        self.assertEqual(game.initial_side, 1)
        self.assertEqual(game.blue_result, 1)
        self.assertEqual((game.blue_player, game.red_player), ("BluePlayer", "RedPlayer"))
        self.assertEqual((game.blue_rating, game.red_rating), (1200.0, 1400.0))
        self.assertEqual((game.rated, game.played_at), (1, "2026-01-10T16:00:00+00:00"))

    def test_opening_wdl_is_grouped_by_color(self):
        with tempfile.TemporaryDirectory() as directory:
            database = OpeningDatabase(Path(directory) / "openings.sqlite3")
            try:
                database.import_game(GAME_HISTORY)
                root = database.roots()[0]
                first_move = database.opening(root["hash"])["moves"][0]
                self.assertEqual(first_move["move"], "Sd4-e5")
                self.assertEqual(first_move["wdl"], {"blue": 1, "draws": 0, "red": 0})
                self.assertEqual(first_move["resolved"], 1)
                self.assertEqual(first_move["ratings"], {"average": 1300.0, "blue": 1200.0, "red": 1400.0, "games": 1})
            finally:
                database.close()

    def test_player_filter_matches_either_color(self):
        with tempfile.TemporaryDirectory() as directory:
            database = OpeningDatabase(Path(directory) / "openings.sqlite3")
            try:
                database.import_game(GAME_HISTORY)
                database.import_game(SECOND_GAME_HISTORY)
                self.assertEqual(database.existing_game_ids(["example1", "missing"]), {"example1"})
                root = database.roots()[0]["hash"]
                self.assertEqual(database.summary("BluePlayer")["games"], 2)
                self.assertEqual(database.opening(root, player="BluePlayer")["moves"][0]["games"], 2)
                self.assertEqual(database.summary("BluePlayer", "blue")["games"], 1)
                self.assertEqual(database.summary("BluePlayer", "red")["games"], 1)
                self.assertEqual(database.opening(root, player="BluePlayer", color="red")["moves"][0]["games"], 1)
                self.assertEqual(database.summary(game_type="rated")["games"], 1)
                self.assertEqual(database.summary(game_type="casual")["games"], 1)
                self.assertEqual(database.summary(date_from="2026-01-15")["games"], 1)
                self.assertEqual(database.summary(rating_min=1250)["games"], 1)
                self.assertEqual(database.summary(rating_bands=("1200-1299",))["games"], 1)
                self.assertEqual(database.summary(rating_bands=("1200-1299", "1300-1399"))["games"], 2)
                self.assertEqual(database.summary(rating_bands=())["games"], 0)
                self.assertEqual(database.summary("missing")["games"], 0)
            finally:
                database.close()

    def test_summary_and_roots_ignore_zero_ply_games(self):
        with tempfile.TemporaryDirectory() as directory:
            database = OpeningDatabase(Path(directory) / "openings.sqlite3")
            try:
                database.import_game(GAME_HISTORY)
                database.import_game(ZERO_PLY_GAME)
                self.assertEqual(database.summary()["games"], 1)
                self.assertEqual(database.roots()[0]["games"], 1)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
