# Intransitive Openings

Independent community opening explorer for Intransitive/RPS2.

## Architecture

- `web/`: static frontend and its own SVG assets.
- `api/index.py`: single read-only Vercel Python entrypoint and API router.
- `opening_api.py`: shared endpoint and request logic.
- `opening_database.py`: local SQLite and remote Turso data access.
- `opening_crawl.py`: incremental public game crawler.
- `opening_server.py`: local development server.
- `opening_prepare.py`: migration and Turso compatibility check.
- `data/`: local SQLite location; database files are excluded from Git.

Production uses Vercel for the frontend and API, Turso for persistent data, and a
manually triggered GitHub Action for the crawler. API and crawler tokens are separate.

## Local development

Without Turso environment variables, everything uses the local database:

```text
python opening_server.py
```

Open `http://127.0.0.1:8877`. Import recent games with:

```text
python opening_crawl.py --pages 5 --delay .4
```

To test against Turso, install `requirements.txt`, set `TURSO_DATABASE_URL` and
`TURSO_AUTH_TOKEN`, and run the same commands. Never commit these credentials.

Run tests with:

```text
python -m unittest test_opening_database.py
```

See [DEPLOY.md](DEPLOY.md) for migration and publication.
