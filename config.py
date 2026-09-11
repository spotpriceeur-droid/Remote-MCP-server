"""
config.py

Centralized configuration for the Energi Data Service -> TimescaleDB
pipeline. All tunable values live here so nothing is hard-coded deeper
in the codebase. Adjust the values below (or override them via
environment variables in `from_env`) rather than editing the
client/storage/processing modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


@dataclass(frozen=True)
class APIConfig:
    dayahead_url: str = "https://api.energidataservice.dk/dataset/DayAheadPrices"
    elspot_url: str = "https://api.energidataservice.dk/dataset/Elspotprices"

    end: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M"))

    # DayAheadPrices only has 15-min resolution from this point onward.
    dayahead_start: str = "2026-01-01T00:00"

    # Elspotprices: pull the last 4 years of hourly history.
    elspot_start: str = field(
        default_factory=lambda: (
            pd.Timestamp.now() - pd.DateOffset(years=4)
        ).strftime("%Y-%m-%dT%H:%M")
    )

    page_limit: int = 20_000
    max_retries: int = 5
    retry_backoff_seconds: float = 2.0
    rate_limit_backoff_seconds: float = 15.0
    request_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class DatabaseConfig:
    """Connection settings for the (self-hosted, Docker) TimescaleDB instance."""

    host: str = "localhost"
    port: int = 5432
    dbname: str = "postgres"
    user: str = "postgres"
    # Must match the POSTGRES_PASSWORD you set when running the Docker
    # container. Overridable via TIMESCALE_PASSWORD env var (see from_env).
    password: str = "yourpassword"
    table_name: str = "day_ahead_prices"
    # Rows per INSERT ... ON CONFLICT batch.
    batch_size: int = 1000
    sslmode: str = "disable"


@dataclass(frozen=True)
class PipelineConfig:
    # Real 15-min DayAheadPrices data exists from this timestamp onward;
    # interpolated Elspotprices data is only ever uploaded BEFORE this
    # cutoff, so real measurements are never overwritten by estimates.
    real_data_cutoff: str = "2025-10-01T00:00"
    target_resolution: str = "15min"


@dataclass(frozen=True)
class Config:
    api: APIConfig = field(default_factory=APIConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

    @staticmethod
    def from_env() -> "Config":
        """
        Optional: build a Config, letting environment variables override
        the TimescaleDB connection settings. Useful for CI or running on
        a different machine without editing this file.
        """
        database = DatabaseConfig(
            host=os.environ.get("TIMESCALE_HOST", DatabaseConfig().host),
            port=int(os.environ.get("TIMESCALE_PORT", DatabaseConfig().port)),
            dbname=os.environ.get("TIMESCALE_DBNAME", DatabaseConfig().dbname),
            user=os.environ.get("TIMESCALE_USER", DatabaseConfig().user),
            password=os.environ.get("TIMESCALE_PASSWORD", DatabaseConfig().password),
            table_name=os.environ.get("TIMESCALE_TABLE", DatabaseConfig().table_name),
            sslmode=os.environ.get("TIMESCALE_SSL", "disable"),
        )
        return Config(database=database)
