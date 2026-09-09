"""
scheduler.py

Standalone process that periodically refreshes TimescaleDB by calling the
same ingestion logic exposed as the `refresh_database` MCP tool in
server.py -- without needing an MCP client (Claude Desktop, Claude Code,
etc.) connected and asking for it.

Also runs a daily TimescaleDB backup job (pg_dump -> gzip -> /app/backups,
with old backups pruned after BACKUP_KEEP_DAYS days) so a lost or
corrupted volume doesn't mean lost data.

Run this as its own process/container, *separate* from the MCP server
process:

    uv run scheduler.py
    # or
    python scheduler.py

Because it's decoupled from server.py's `mcp.run(transport="stdio")`
(which only starts when an MCP client launches it and blocks on stdio),
this script keeps refreshing the database on a fixed schedule regardless
of whether any MCP client is connected -- e.g. as a systemd service, a
Docker container's entrypoint, or a plain cron-triggered invocation.

Note on `refresh_database`: in this version of the `mcp` SDK, the
`@mcp.tool()` decorator registers the function with the FastMCP server
but returns the original function unchanged, so `refresh_database` here
is just a normal Python function -- no `.fn` unwrapping needed. (Some
other FastMCP versions instead bind the name to a `Tool` wrapper object,
in which case you'd call `refresh_database.fn()` to reach the underlying
function, as noted below for portability.)
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from server import refresh_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REFRESH_INTERVAL_HOURS = 6

BACKUP_DIR = Path("/app/backups")
BACKUP_KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "7"))
DB_HOST = os.environ.get("TIMESCALE_HOST", "timescaledb")
DB_PORT = os.environ.get("TIMESCALE_PORT", "5432")
DB_NAME = os.environ.get("TIMESCALE_DBNAME", "postgres")
DB_USER = os.environ.get("TIMESCALE_USER", "postgres")
DB_PASSWORD = os.environ.get("TIMESCALE_PASSWORD", "")


def _scheduled_refresh() -> None:
    logger.info("Running scheduled DB refresh...")
    try:
        # If your `mcp` version wraps tools in a `Tool` object instead of
        # returning the bare function, use `refresh_database.fn()` instead.
        result = refresh_database()
        logger.info(result)
    except Exception:  # noqa: BLE001 - never let one failed refresh kill the scheduler
        logger.exception("Scheduled DB refresh failed")


def _scheduled_backup() -> None:
    logger.info("Running scheduled DB backup...")
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        dump_path = BACKUP_DIR / f"timescaledb_{timestamp}.sql"
        gz_path = dump_path.with_suffix(dump_path.suffix + ".gz")

        env = os.environ.copy()
        env["PGPASSWORD"] = DB_PASSWORD

        with open(dump_path, "wb") as f:
            subprocess.run(
                [
                    "pg_dump",
                    "-h", DB_HOST,
                    "-p", DB_PORT,
                    "-U", DB_USER,
                    "-d", DB_NAME,
                ],
                stdout=f,
                env=env,
                check=True,
                timeout=600,
            )

        with open(dump_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        dump_path.unlink()

        size_kb = gz_path.stat().st_size / 1024
        logger.info("Backup written: %s (%.1f KB)", gz_path.name, size_kb)

        _prune_old_backups()
    except Exception:  # noqa: BLE001 - never let a failed backup kill the scheduler
        logger.exception("Scheduled DB backup failed")


def _prune_old_backups() -> None:
    cutoff = datetime.utcnow() - timedelta(days=BACKUP_KEEP_DAYS)
    for backup_file in BACKUP_DIR.glob("timescaledb_*.sql.gz"):
        if datetime.utcfromtimestamp(backup_file.stat().st_mtime) < cutoff:
            backup_file.unlink()
            logger.info("Pruned old backup: %s", backup_file.name)


if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(
        _scheduled_refresh,
        trigger="interval",
        hours=REFRESH_INTERVAL_HOURS,
        id="refresh_database",
        max_instances=1,       # don't let a slow refresh overlap with the next tick
        coalesce=True,         # if the process was asleep past a tick, run once, not N times
    )
    scheduler.add_job(
        _scheduled_backup,
        trigger="cron",
        hour=3,
        minute=0,
        id="backup_database",
        max_instances=1,
        coalesce=True,
    )

    logger.info(
        "Scheduler starting: refresh_database every %s hours, "
        "backup_database daily at 03:00 UTC (keeping %s days).",
        REFRESH_INTERVAL_HOURS,
        BACKUP_KEEP_DAYS,
    )

    # Run once immediately on startup so a freshly (re)started container
    # doesn't wait a full interval before the DB is populated.
    _scheduled_refresh()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
