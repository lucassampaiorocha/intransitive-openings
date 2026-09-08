# Turso and Vercel publication

Production consists of one Vercel project, one Turso database and one scheduled
GitHub Actions workflow. No VM, reverse proxy or public API domain is required.

## 1. Prepare the existing SQLite database

Stop every local crawler and opening server, put the database at
`data/openings_community.sqlite3`, then run:

```text
python opening_prepare.py
python -m unittest test_opening_database.py
```

The preparation command applies pending indexes and migrations, enables WAL,
checkpoints it and validates Turso's upload requirements.

## 2. Create the Turso database

Install the Turso CLI, authenticate, and upload the prepared file:

```text
turso auth login
turso db create intransitive-openings --from-file data/openings_community.sqlite3 --wait
turso db show intransitive-openings --url
```

The local file is below Turso's 2 GB CLI upload limit. Keep it as a backup until the
remote database has been verified.

## 3. Create isolated credentials

Create a read-only token for Vercel:

```text
turso db tokens create intransitive-openings --read-only --expiration never
```

Create a separate full-access token for the crawler:

```text
turso db tokens create intransitive-openings --expiration never
```

Store each printed token immediately. Do not put either token in `config.js`, Git,
logs, screenshots or browser code.

## 4. Configure GitHub Actions

In the GitHub repository, create Actions secrets:

- `TURSO_DATABASE_URL`: URL printed by `turso db show`.
- `TURSO_WRITE_TOKEN`: full-access crawler token.

The workflow `.github/workflows/openings-crawler.yml` runs hourly and can also be
started manually. It first checks existing game IDs in one query per page, avoiding
repeated game-history downloads.

## 5. Configure and deploy Vercel

Import the Git repository in Vercel and set Root Directory to `apps/openings`.
Use Framework Preset `Other`; `vercel.json` publishes `web/` and detects the Python
functions under `api/`.

Create these Vercel environment variables for Production, Preview and Development:

- `TURSO_DATABASE_URL`: the same database URL.
- `TURSO_AUTH_TOKEN`: the read-only token, never the crawler token.

Leave `window.OPENINGS_API_BASE` empty in `web/config.js`; the browser calls the API
on the same Vercel origin. Deploy, then verify:

```text
https://YOUR-PROJECT.vercel.app/api/health
https://YOUR-PROJECT.vercel.app/api/summary
```

Finally open the project root and apply several filters. The Config loading indicator
must remain visible until the API response completes.

## 6. Operations

Run the crawler manually from GitHub Actions after the first deploy. Monitor Turso's
rows-read and rows-written metrics: aggregate queries count every examined row. The
API sends CDN cache headers for 60-second shared caching to reduce repeated reads.

To rotate credentials, create a replacement token, update the matching secret, deploy
or rerun the workflow, and then revoke the old token.
