# Smart 1 Action Center — PostgreSQL + Worker Migration

## Why this architecture

The web dashboard and the communications scanner are now separate processes:

```text
Browser / Phone
      |
      v
smart1-client-action-center (WEB)
      |
      +--------> Render Postgres <--------+
      |                                  |
      |                           smart1-action-center-worker
      |                                  |
      |                                  +--> Gmail
      |                                  +--> Sent Mail
      |                                  +--> Google Chat
      |                                  +--> OpenAI
      |                                  +--> resolution checks
      |                                  +--> invoice detection
      |
      +--> fast dashboard/API requests
```

A heavy communications scan can restart the worker without taking down the dashboard.

## Phase 1 — migrate the existing SQLite data

Use the included `render.yaml` first.

It intentionally keeps the existing web-service disk mounted at:

```text
/var/data
```

and sets:

```text
MIGRATE_SQLITE_TO_POSTGRES=1
```

On the first web startup:

1. PostgreSQL tables are created.
2. `/var/data/tasks.db` is read.
3. Existing tasks, notes, payment/invoice state, OAuth credentials, training rules,
   Chat/Gmail state, sent monitors, and other application tables are copied.
4. PostgreSQL serial sequences are reset.
5. `sqlite_migration_complete=1` is saved in PostgreSQL.
6. Future web deploys see the marker and do not copy the SQLite file again.

The worker waits for that marker before scanning communications.

### What to look for in the web log

```text
SQLite -> PostgreSQL migration completed:
```

The line includes table row counts.

## Phase 2 — remove the old disk

After you have opened the dashboard and confirmed your tasks/invoices/history are present:

1. Replace `render.yaml` with the included `render-postgres-final.yaml`.
2. Sync/deploy the Blueprint.
3. Remove the old persistent disk from the web service if Render does not remove it automatically.
4. Keep PostgreSQL as the only application datastore.

The final Blueprint sets:

```text
MIGRATE_SQLITE_TO_POSTGRES=0
```

and does not define a web-service disk.

## Manual Sync behavior

The Sync button no longer runs Gmail/OpenAI work in the web service.

It creates a row in `sync_jobs`.

The worker sees the queued row, performs the communications sync, writes the result back,
and the browser polls the existing sync-status endpoint.

## Startup 30-day catch-up

The worker has:

```text
WORKER_STARTUP_SYNC=1
```

Each worker deploy/restart runs one immediate communications sync before beginning the
normal 15-minute schedule. The existing Gmail, Sent, and Chat configuration retains the
30-day lookback.

## Database connection

Both services receive `DATABASE_URL` from the Render Postgres managed PgBouncer connection:

```yaml
fromDatabase:
  name: smart1-action-center-db
  property: connectionPoolString
```

No database password is committed to GitHub.

## Recommended first deployment sequence

1. Commit this package.
2. In Render, sync the Blueprint using `render.yaml`.
3. Render creates `smart1-action-center-db`.
4. Render creates `smart1-action-center-worker`.
5. Web redeploys and migrates SQLite.
6. Worker waits until migration completes, then performs the startup communications scan.
7. Confirm the dashboard data.
8. Switch to `render-postgres-final.yaml` contents.
9. Remove the old disk.

Do not manually delete `/var/data/tasks.db` before step 7.
