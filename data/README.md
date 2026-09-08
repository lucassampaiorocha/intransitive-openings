# Runtime data

Place `openings_community.sqlite3` here as the local archive, then generate
`openings_book.sqlite3` with `python opening_build.py`. The book is the file used
by the local server and uploaded to Turso; the archive is never deployed.

Database files, WAL files and shared-memory files are excluded from version control.
