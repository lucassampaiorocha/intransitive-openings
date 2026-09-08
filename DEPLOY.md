# Turso and Vercel publication

Production consists of one Vercel project and one versioned Turso opening book.
No VM, reverse proxy or public API domain is required.

## 1. Build the production opening book

The crawler's `data/openings_community.sqlite3` file is the private archive; do
not upload it. Stop the crawler, then build a fresh immutable production file:

```text
python opening_build.py --archive data/openings_community.sqlite3 --output data/openings_book.sqlite3
python -m unittest test_opening_database.py test_opening_book.py
```

The builder validates foreign keys, runs `ANALYZE` and `VACUUM`, and finishes
with `journal_mode=WAL`, which Turso requires for `--from-file` uploads.

## 2. Create the Turso database

Install the Turso CLI, authenticate, and upload the prepared file:

```text
turso auth login
turso db create intransitive-openings-YYYYMMDD --from-file data/openings_book.sqlite3 --wait
turso db show intransitive-openings-YYYYMMDD --url
```

Keep the generated file until the remote database has been verified. Use a new,
versioned database for each release, then switch Vercel to it; this makes rollback
an environment-variable change.

## 3. Create isolated credentials

Create a read-only token for Vercel:

```text
turso db tokens create intransitive-openings-YYYYMMDD --read-only --expiration never
```

Store the printed token immediately. Do not put it in `config.js`, Git,
logs, screenshots or browser code.

## 4. Collect archive updates

The crawler must write to a persistent archive location, not the Turso production
book. Run a book build and this deployment flow deliberately after new archive
data is collected.

## 5. Configure and deploy Vercel

Import the Git repository in Vercel and set Root Directory to `apps/openings`.
Use Framework Preset `Other`; `vercel.json` publishes `web/`, while `pyproject.toml`
declares `api.index:handler` as the single Python entrypoint.

Create these Vercel environment variables for Production, Preview and Development:

- `TURSO_DATABASE_URL`: the same database URL.
- `TURSO_AUTH_TOKEN`: the read-only token for the selected book.

Leave `window.OPENINGS_API_BASE` empty in `web/config.js`; the browser calls the API
on the same Vercel origin. Deploy, then verify:

```text
https://YOUR-PROJECT.vercel.app/api/health
https://YOUR-PROJECT.vercel.app/api/summary
```

Finally open the project root and navigate several continuations. The move list,
WDL bars, board state, and variation history should all update normally.

## 6. Operations

Monitor Turso rows-read metrics after deployment. Normal navigation performs one
primary-key position lookup and one indexed outgoing-move lookup; it never scans
games or individual move occurrences.

To rotate credentials, create a replacement token, update the matching Vercel
variable, deploy, and then revoke the old token.
