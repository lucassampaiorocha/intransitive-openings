# Intransitive Openings

Independent community opening explorer for Intransitive/RPS2.

## Architecture

- `web/`: static frontend and its own SVG assets.
- `api/index.py`: single read-only Vercel Python entrypoint and API router.
- `opening_api.py`: shared endpoint and request logic.
- `opening_database.py`: the local/raw archive schema and importer.
- `opening_build.py`: offline archive-to-book builder.
- `opening_book.py`: compact read-only SQLite/libSQL opening-book access.
- `opening_crawl.py`: incremental public-game crawler for the archive only.
- `opening_server.py`: local development server.
- `opening_prepare.py`: legacy archive preparation helper; production books are prepared by `opening_build.py`.
- `data/`: local SQLite location; database files are excluded from Git.

Production uses Vercel for the frontend and API and Turso for the immutable,
precomputed opening book. The raw archive stays outside production and is the
only target for the crawler.

## Local development

Build a local production book after crawling the archive:

```text
python opening_build.py --archive data/openings_community.sqlite3 --output data/openings_book.sqlite3
python opening_server.py --database data/openings_book.sqlite3
```

Open `http://127.0.0.1:8877`. Import recent games with:

```text
python opening_crawl.py --database data/openings_community.sqlite3 --pages 5 --delay .4
```

To test against Turso, install `requirements.txt`, set `TURSO_DATABASE_URL` and
`TURSO_AUTH_TOKEN`, and run the same commands. Never commit these credentials.

Run tests with:

```text
python -m unittest test_opening_database.py test_opening_book.py
```

See [DEPLOY.md](DEPLOY.md) for migration and publication.
