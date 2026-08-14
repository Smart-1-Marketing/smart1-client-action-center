# PostgreSQL Startup Hotfix

The startup crash was caused by `compat_row_factory()` assuming that every Psycopg statement
has a `cursor.description`. DDL such as `CREATE TABLE` has no returned columns, so Psycopg
passes a cursor whose description is `None`.

This release:
- handles result-less Psycopg commands correctly
- translates SQLite scalar `MAX(column, ?)` to PostgreSQL `GREATEST(column, %s)`
- makes duplicate Gmail/Chat/meeting inserts conflict-safe before PostgreSQL can raise
  a uniqueness error and abort the transaction
- preserves the SQLite fallback and the Postgres/background-worker architecture

Use the migration-phase `render.yaml` until the SQLite-to-Postgres migration succeeds.
