"""Prepare and validate the local SQLite file before importing it into Turso."""

from __future__ import annotations

import argparse
from pathlib import Path

from opening_database import OpeningDatabase
from opening_server import default_database_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare the openings SQLite database for Turso")
    parser.add_argument("--database", default=str(default_database_path()), help="local SQLite file")
    args = parser.parse_args()
    path = Path(args.database).resolve()
    if not path.exists():
        parser.error(f"database not found: {path}")

    database = OpeningDatabase(path)
    try:
        connection = database.connection
        journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()["journal_mode"])
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        page_size = int(connection.execute("PRAGMA page_size").fetchone()["page_size"])
        auto_vacuum = int(connection.execute("PRAGMA auto_vacuum").fetchone()["auto_vacuum"])
        encoding = str(connection.execute("PRAGMA encoding").fetchone()["encoding"])
        connection.commit()
    finally:
        database.close()

    settings = {"journal_mode": journal_mode, "page_size": page_size,
                "auto_vacuum": auto_vacuum, "encoding": encoding}
    print("Prepared:", path)
    print("Settings:", settings)
    if journal_mode.lower() != "wal" or page_size != 4096 or auto_vacuum != 0 or encoding.upper() != "UTF-8":
        raise SystemExit("Database settings are not compatible with Turso upload requirements")


if __name__ == "__main__":
    main()
