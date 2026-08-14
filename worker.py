import json
import os
import time
import traceback
from datetime import datetime

# Prevent app.py from starting its legacy in-process timer when imported.
os.environ["DISABLE_BACKGROUND_GMAIL_SYNC"] = "1"
os.environ.setdefault("MIGRATE_SQLITE_TO_POSTGRES", "0")

from app import (  # noqa: E402
    AUTO_GMAIL_SYNC_MINUTES,
    connect_db,
    get_setting,
    release_process_memory,
    sync_all_communications,
)
from db_backend import migration_ready, using_postgres  # noqa: E402


POLL_SECONDS = max(5, int(os.environ.get("WORKER_POLL_SECONDS", "10")))
STARTUP_SYNC = os.environ.get("WORKER_STARTUP_SYNC", "1") == "1"


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def wait_for_database_migration():
    if not using_postgres():
        return
    print("Worker waiting for PostgreSQL migration marker...")
    while not migration_ready():
        time.sleep(5)
    print("PostgreSQL migration marker found. Worker starting.")


def reset_interrupted_jobs():
    with connect_db() as con:
        con.execute(
            """
            UPDATE sync_jobs
            SET state='queued',
                started_at='',
                error='Worker restarted before the prior job completed.'
            WHERE state='running'
            """
        )


def claim_job():
    # Only one worker is configured, but FOR UPDATE SKIP LOCKED keeps this safe
    # if another worker is ever added.
    with connect_db() as con:
        job = con.execute(
            """
            SELECT * FROM sync_jobs
            WHERE state='queued'
            ORDER BY id ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        ).fetchone()
        if not job:
            return None
        con.execute(
            """
            UPDATE sync_jobs
            SET state='running',started_at=?,error=''
            WHERE id=?
            """,
            (now_iso(), job["id"]),
        )
        return dict(job)


def finish_job(job_id, result=None, error=""):
    with connect_db() as con:
        con.execute(
            """
            UPDATE sync_jobs
            SET state=?,
                finished_at=?,
                result_json=?,
                error=?
            WHERE id=?
            """,
            (
                "failed" if error else "done",
                now_iso(),
                json.dumps(result or {}, default=str),
                error,
                job_id,
            ),
        )


def run_sync(job_id=None, source="scheduled"):
    print(f"Starting {source} communications sync...")
    try:
        result = sync_all_communications()
        if job_id:
            finish_job(job_id, result=result)
        print(f"{source.capitalize()} sync complete: {result}")
        return result
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print(f"{source.capitalize()} sync failed: {detail}")
        traceback.print_exc()
        if job_id:
            finish_job(job_id, error=detail)
        return {"error": detail}
    finally:
        release_process_memory()


def main():
    wait_for_database_migration()
    reset_interrupted_jobs()

    if STARTUP_SYNC and get_setting("gmail_credentials", ""):
        # This performs the requested 30-day catch-up immediately after a worker
        # deploy/restart, using the app's 30-day Gmail/Sent/Chat settings.
        run_sync(source="startup")

    interval = max(60, AUTO_GMAIL_SYNC_MINUTES * 60)
    next_scheduled = time.monotonic() + interval

    while True:
        job = claim_job()
        if job:
            run_sync(job_id=job["id"], source="manual")
            next_scheduled = time.monotonic() + interval
            continue

        if time.monotonic() >= next_scheduled:
            if get_setting("gmail_credentials", ""):
                run_sync(source="scheduled")
            next_scheduled = time.monotonic() + interval

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
