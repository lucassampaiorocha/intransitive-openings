"""Coletor cooperativo do histórico público de partidas do RPS2.

Exemplo: python opening_crawl.py --pages 5
Para retomar uma coleta grande, execute novamente com os mesmos argumentos:
partidas já armazenadas são ignoradas pelo banco.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from opening_database import ImportError, OpeningDatabase


API_ROOT = "https://meaf.us/rps2/api"
USER_AGENT = "IntransitiveOpenings/0.1 (community opening database)"
ROOT = Path(__file__).parent
LOCAL_DATABASE = ROOT / "data" / "openings_community.sqlite3"
LEGACY_DATABASE = ROOT.parent.parent / "openings_community.sqlite3"


def default_database_path() -> Path:
    configured = os.environ.get("OPENINGS_DATABASE")
    if configured:
        return Path(configured)
    return LOCAL_DATABASE if LOCAL_DATABASE.exists() or not LEGACY_DATABASE.exists() else LEGACY_DATABASE


def fetch_json(path: str, params: dict[str, str | int] | None = None) -> dict:
    query = "?" + urlencode(params) if params else ""
    request = Request(API_ROOT + path + query, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError("A API retornou um JSON inesperado")
    return data


def crawl(database: OpeningDatabase, first_page: int, pages: int, page_size: int, delay: float) -> dict[str, int]:
    imported = duplicates = failed = seen = 0
    for page in range(first_page, first_page + pages):
        listing = fetch_json("/past_games", {"page": page, "limit": page_size})
        games = listing.get("games", [])
        if not isinstance(games, list) or not games:
            break
        game_ids = [item.get("gameID") for item in games if isinstance(item, dict)
                    and isinstance(item.get("gameID"), str)]
        existing_ids = database.existing_game_ids(game_ids)
        for item in games:
            game_id = item.get("gameID") if isinstance(item, dict) else None
            if not isinstance(game_id, str):
                failed += 1
                continue
            seen += 1
            if game_id in existing_ids:
                duplicates += 1
                continue
            try:
                history = fetch_json("/game_history", {"gameID": game_id})
                _, was_imported = database.import_game(history)
                imported += int(was_imported)
                duplicates += int(not was_imported)
            except (HTTPError, URLError, TimeoutError, ValueError, ImportError) as error:
                failed += 1
                print(f"[{game_id}] falhou: {error}")
            time.sleep(delay)
        print(f"Página {page}: {seen} vistas, {imported} novas, {duplicates} repetidas, {failed} falhas")
        time.sleep(delay)
    return {"seen": seen, "imported": imported, "duplicates": duplicates, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Coletor de partidas públicas do RPS2")
    parser.add_argument("--database", default=str(default_database_path()), help="arquivo SQLite")
    parser.add_argument("--from-page", type=int, default=1, help="primeira página da API")
    parser.add_argument("--pages", type=int, default=1, help="quantidade de páginas a coletar")
    parser.add_argument("--all", action="store_true", help="coletar todas as páginas públicas a partir de --from-page")
    parser.add_argument("--page-size", type=int, default=10, help="partidas por página")
    parser.add_argument("--delay", type=float, default=.4, help="espera mínima entre requisições, em segundos")
    args = parser.parse_args()
    if args.from_page < 1 or args.pages < 1 or args.page_size < 1 or args.delay < .1:
        parser.error("páginas e page-size devem ser positivos; delay mínimo é 0.1")
    database_path = Path(args.database).resolve()
    if not os.environ.get("TURSO_DATABASE_URL"):
        database_path.parent.mkdir(parents=True, exist_ok=True)
    database = OpeningDatabase.from_environment(database_path)
    try:
        pages = args.pages
        if args.all:
            listing = fetch_json("/past_games", {"page": args.from_page, "limit": args.page_size})
            total_pages = int(listing.get("totalPages", 0))
            pages = max(0, total_pages - args.from_page + 1)
            if not pages:
                print("Não há páginas a coletar a partir desse ponto.")
                return
            print(f"Coletando {pages} páginas públicas; o processo pode demorar bastante.")
        result = crawl(database, args.from_page, pages, args.page_size, args.delay)
        print("Concluído:", result, "| banco:", database.summary())
    finally:
        database.close()


if __name__ == "__main__":
    main()
