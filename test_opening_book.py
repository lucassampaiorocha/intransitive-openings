import tempfile
import unittest
from pathlib import Path

from opening_book import OpeningBookDatabase, pack_board, unpack_board
from opening_build import build_book
from opening_database import OpeningDatabase


GAME_ONE = {
    "gameID": "book-one", "playernames": ["Blue", "Red"], "winner": "Blue", "phase": "done",
    "ratingsBefore": {"Blue": 1200, "Red": 1400}, "options": {"ranked": True},
    "startTime": "2026-01-10T13:00:00-03:00", "moveHistory": [{"move": "D4E5"}, {"move": "G5G4"}],
}
GAME_TWO = {
    "gameID": "book-two", "playernames": ["Other", "Blue"], "winner": "Blue", "phase": "done",
    "ratingsBefore": {"Other": 1100, "Blue": 1300}, "options": {"ranked": False},
    "startTime": "2026-01-20T13:00:00-03:00", "moveHistory": [{"move": "D4E5"}, {"move": "G5G4"}],
}


class OpeningBookTests(unittest.TestCase):
    def test_pack_board_round_trip(self):
        board = [0, -3, -2, -1, 1, 2, 3] + [0] * 74
        self.assertEqual(unpack_board(pack_board(board)), board)

    def test_build_matches_archive_opening(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "archive.sqlite3"
            book_path = Path(directory) / "book.sqlite3"
            archive = OpeningDatabase(archive_path)
            try:
                archive.import_game(GAME_ONE)
                archive.import_game(GAME_TWO)
                archive_root = archive.roots()[0]
                expected = archive.opening(archive_root["hash"])
            finally:
                archive.close()

            result = build_book(archive_path, book_path, book_version="test-book")
            self.assertEqual(result["games"], 2)
            check = __import__("sqlite3").connect(book_path)
            try:
                self.assertEqual(check.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            finally:
                check.close()
            book = OpeningBookDatabase(book_path)
            try:
                self.assertEqual(book.summary()["book_version"], "test-book")
                root = book.roots()[0]
                actual = book.opening(root["id"])
                self.assertEqual(actual["board"], expected["board"])
                self.assertEqual(actual["side"], expected["side"])
                self.assertEqual(actual["total"], expected["total"])
                self.assertEqual(actual["ratings"], expected["ratings"])
                self.assertEqual(len(actual["moves"]), len(expected["moves"]))
                for actual_move, expected_move in zip(actual["moves"], expected["moves"]):
                    self.assertEqual(actual_move["move"], expected_move["move"])
                    self.assertEqual(actual_move["games"], expected_move["games"])
                    self.assertEqual(actual_move["wdl"], expected_move["wdl"])
                    self.assertEqual(actual_move["resolved"], expected_move["resolved"])
                    self.assertEqual(actual_move["ratings"], expected_move["ratings"])

                plan = book.connection.execute("""
                    EXPLAIN QUERY PLAN SELECT * FROM book_moves
                    WHERE from_position_id = ? ORDER BY samples DESC, source_order, target_order LIMIT 30
                """, (root["id"],)).fetchall()
                self.assertTrue(any("SEARCH book_moves USING INDEX book_moves_by_popularity" in row["detail"]
                                    for row in plan), plan)
            finally:
                book.close()


if __name__ == "__main__":
    unittest.main()
