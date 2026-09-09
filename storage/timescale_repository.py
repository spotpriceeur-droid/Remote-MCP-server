"""
timescale_repository.py

Handles all TimescaleDB interaction: connecting via psycopg2, creating the
hypertable if it doesn't already exist, and writing price records with an
UPSERT (INSERT ... ON CONFLICT DO UPDATE) -- so re-running the pipeline
never creates duplicates, it just updates matching rows instead.

Assumes a TimescaleDB instance is reachable (e.g. the local Docker
container from the README), NOT Firebase/Firestore.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from config import DatabaseConfig
from exceptions import DatabaseConnectionError, DatabaseWriteError
from schema import PRICE_DKK, PRICE_EUR, TIME_DK, TIME_UTC, ZONE

logger = logging.getLogger(__name__)


class TimescaleRepository:
    """Writes canonical-schema price records into a TimescaleDB hypertable."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.conn = self._connect()
        self._ensure_table()

    def _connect(self):
        try:
            return psycopg2.connect(
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.dbname,
                user=self.config.user,
                password=self.config.password,
            )
        except psycopg2.OperationalError as exc:
            raise DatabaseConnectionError(
                f"Could not connect to TimescaleDB at "
                f"{self.config.host}:{self.config.port}/{self.config.dbname}. "
                f"Is the Docker container running? Check with "
                f"'docker ps' -- start it with 'docker start timescaledb' "
                f"if it's stopped. Original error: {exc}"
            ) from exc

    def _ensure_table(self) -> None:
        """Creates the table + hypertable on first run; a no-op afterwards."""
        create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS {self.config.table_name} (
                zone TEXT NOT NULL,
                time_utc TIMESTAMPTZ NOT NULL,
                time_dk TIMESTAMPTZ,
                price_eur DOUBLE PRECISION,
                price_dkk DOUBLE PRECISION,
                source TEXT,
                PRIMARY KEY (zone, time_utc)
            );
        """
        create_hypertable_sql = f"""
            SELECT create_hypertable(
                '{self.config.table_name}', 'time_utc', if_not_exists => TRUE
            );
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(create_table_sql)
                cur.execute(create_hypertable_sql)
            self.conn.commit()
        except Exception as exc:  # noqa: BLE001 - convert to domain error
            self.conn.rollback()
            raise DatabaseConnectionError(
                f"Failed to create/verify table '{self.config.table_name}': {exc}. "
                f"Is the timescaledb extension available in this database? "
                f"(It's built into the timescale/timescaledb Docker image.)"
            ) from exc

    def write_records(
        self,
        records: list[dict[str, Any]],
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> int:
        """
        Writes a list of canonical-schema record dicts, upserting on
        (zone, time_utc) so records are updated in place on re-runs
        instead of duplicating.
        """
        if not records:
            logger.warning("write_records called with an empty list; nothing to do.")
            return 0

        extra_fields = extra_fields or {}
        source = extra_fields.get("source")

        rows = []
        for record in records:
            try:
                zone = record[ZONE]
                time_utc = pd.Timestamp(record[TIME_UTC]).isoformat()
            except KeyError as exc:
                raise DatabaseWriteError(
                    f"Record missing required canonical field {exc}; "
                    f"was it normalized via schema.normalize_records()?"
                ) from exc

            time_dk = pd.Timestamp(record[TIME_DK]).isoformat() if TIME_DK in record else None
            rows.append((
                zone,
                time_utc,
                time_dk,
                record.get(PRICE_EUR),
                record.get(PRICE_DKK),
                source,
            ))

        upsert_sql = f"""
            INSERT INTO {self.config.table_name}
                (zone, time_utc, time_dk, price_eur, price_dkk, source)
            VALUES %s
            ON CONFLICT (zone, time_utc) DO UPDATE SET
                time_dk = EXCLUDED.time_dk,
                price_eur = EXCLUDED.price_eur,
                price_dkk = EXCLUDED.price_dkk,
                source = EXCLUDED.source;
        """

        total_written = 0
        try:
            with self.conn.cursor() as cur:
                for start in range(0, len(rows), self.config.batch_size):
                    batch = rows[start:start + self.config.batch_size]
                    execute_values(cur, upsert_sql, batch)
                    self.conn.commit()
                    total_written += len(batch)
                    logger.info("Upserted %d rows so far...", total_written)
        except Exception as exc:  # noqa: BLE001 - convert to domain error
            self.conn.rollback()
            raise DatabaseWriteError(f"Batch upsert failed: {exc}") from exc

        logger.info(
            "Finished writing %d records to table '%s'.",
            total_written, self.config.table_name,
        )
        return total_written

    def write_dataframe(
        self,
        df: pd.DataFrame,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> int:
        """Convenience wrapper: writes a DataFrame by converting rows to dicts first."""
        if df.empty:
            logger.warning("write_dataframe called with an empty DataFrame; nothing to do.")
            return 0

        records = df.to_dict(orient="records")
        return self.write_records(records, extra_fields=extra_fields)

    def fetch_range(
        self,
        start: str,
        end: str,
        zone: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Reads canonical-schema records already stored in TimescaleDB for
        [start, end), optionally filtered to a single zone. Used by
        read-side consumers (e.g. the MCP server) that want historical
        data without re-fetching it from Energi Data Service.

        Returns records sorted by time_utc ascending, with the same
        canonical field names used everywhere else (zone, time_utc,
        time_dk, price_eur, price_dkk), plus 'source'.
        """
        where_clauses = ["time_utc >= %s", "time_utc < %s"]
        params: list[Any] = [start, end]
        if zone:
            where_clauses.append("zone = %s")
            params.append(zone)

        query = f"""
            SELECT zone, time_utc, time_dk, price_eur, price_dkk, source
            FROM {self.config.table_name}
            WHERE {" AND ".join(where_clauses)}
            ORDER BY time_utc ASC;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            self.conn.rollback()
            raise DatabaseWriteError(f"Query against {self.config.table_name} failed: {exc}") from exc

        return [
            {
                ZONE: r[0],
                TIME_UTC: r[1],
                TIME_DK: r[2],
                PRICE_EUR: r[3],
                PRICE_DKK: r[4],
                "source": r[5],
            }
            for r in rows
        ]

    def latest_timestamp(self, zone: Optional[str] = None) -> Optional[Any]:
        """Returns the max time_utc currently stored (optionally per zone),
        or None if the table is empty. Used to figure out how far back an
        API top-up fetch needs to go to bring the DB up to 'now'."""
        where = "WHERE zone = %s" if zone else ""
        params = [zone] if zone else []
        query = f"SELECT MAX(time_utc) FROM {self.config.table_name} {where};"
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params)
                (result,) = cur.fetchone()
                return result
        except Exception as exc:  # noqa: BLE001
            self.conn.rollback()
            raise DatabaseWriteError(f"Query against {self.config.table_name} failed: {exc}") from exc

    def close(self) -> None:
        self.conn.close()
