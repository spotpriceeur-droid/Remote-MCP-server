"""
server.py

MCP server exposing your Energi Data Service / TimescaleDB pipeline as
tools an MCP client (Claude Desktop, Claude Code, etc.) can call directly
in conversation.

Built on top of the existing pipeline code (api/, storage/, processing/,
config.py, schema.py, exceptions.py) — this file adds no new data-fetching
logic of its own beyond wiring those pieces together for read-oriented,
on-demand use instead of the original write-everything-to-Timescale batch
job in main.py.

Design notes
------------
- TimescaleDB is OPTIONAL at query time. If it's reachable, tools read
  historical data from it (fast, no API calls). If it's not running (or
  not configured), every tool transparently falls back to fetching
  directly from Energi Data Service instead. Either way you get an answer.
- "Most recent data" handling: DayAheadPrices only has real (non-
  interpolated) observations from 2025-10-01 onward, so for any window
  that extends past that cutoff, this server always tops up with a fresh
  direct API call for the tail end — even when serving the rest of the
  range from TimescaleDB — so results reflect prices published since the
  last time you ran the pipeline.
- Negative-price counting deliberately uses raw observations, not
  interpolated ones: Elspotprices (hourly) before 2025-10-01, DayAheadPrices
  (native 15-min) from 2025-10-01 onward. Interpolated points are useful
  for a continuous series but would distort "how many hours were
  negative" by inventing values between real observations.

Usage:
    uv run server.py
or, once added to an MCP client's config, the client starts it for you.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any, Optional

import pandas as pd

from api.dayahead_client import DayAheadPricesClient
from api.elspot_client import ElspotpricesClient
from config import Config
from exceptions import NoDataError, PipelineError
from schema import PRICE_EUR, TIME_DK, TIME_UTC, ZONE
from storage.timescale_repository import TimescaleRepository

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("energi-dk")

CUTOFF = pd.Timestamp("2025-10-01T00:00")
KNOWN_ZONES = ["DK1", "DK2", "SE1", "SE2", "SE3", "SE4", "NO1", "NO2", "NO3", "NO4", "NO5", "FI"]


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _get_repository(config: Config) -> Optional[TimescaleRepository]:
    """Tries to connect to TimescaleDB; returns None (not an exception) if
    it's unreachable, so callers can fall back to the live API instead."""
    try:
        return TimescaleRepository(config.database)
    except PipelineError as exc:
        logger.warning("TimescaleDB unavailable, will use Energi Data Service directly: %s", exc)
        return None


def _fetch_direct(config: Config, start: pd.Timestamp, end: pd.Timestamp, zone: Optional[str]) -> list[dict[str, Any]]:
    """Fetches raw (non-interpolated) canonical records directly from
    Energi Data Service for [start, end), splitting the request across
    Elspotprices (hourly, before the cutoff) and DayAheadPrices (native
    15-min, from the cutoff onward) as needed."""
    records: list[dict[str, Any]] = []

    if start < CUTOFF:
        elspot_end = min(end, CUTOFF)
        client = ElspotpricesClient(config.api)
        try:
            records.extend(client.fetch(start.strftime("%Y-%m-%dT%H:%M"), elspot_end.strftime("%Y-%m-%dT%H:%M"), zone=zone))
        except NoDataError:
            pass

    if end > CUTOFF:
        dayahead_start = max(start, CUTOFF)
        client = DayAheadPricesClient(config.api)
        try:
            records.extend(client.fetch(dayahead_start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M"), zone=zone))
        except NoDataError:
            pass

    return records


def _get_records_with_latest_topup(
    config: Config, start: pd.Timestamp, end: pd.Timestamp, zone: Optional[str]
) -> tuple[list[dict[str, Any]], str]:
    """
    Core data-gathering routine shared by the analysis tools: reads
    historical data from TimescaleDB where possible, then always tops up
    the tail with a direct API call so results include the most recent
    prices published, even if the DB hasn't been refreshed recently.

    Returns (records, note) where `note` describes what happened, for
    transparency in the tool's response.
    """
    repo = _get_repository(config)

    if repo is None:
        records = _fetch_direct(config, start, end, zone)
        return records, "TimescaleDB not reachable; fetched the entire range directly from Energi Data Service."

    try:
        db_records = repo.fetch_range(start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M"), zone=zone)
        db_max_ts = repo.latest_timestamp(zone=zone)
    finally:
        repo.close()

    if not db_records:
        records = _fetch_direct(config, start, end, zone)
        return records, "No matching rows in TimescaleDB for this range; fetched directly from Energi Data Service instead."

    # Top up anything newer than what's in the DB, up to `end`.
    topup_start = pd.Timestamp(db_max_ts) if db_max_ts is not None else start
    note = f"{len(db_records)} record(s) from TimescaleDB."
    if topup_start < end:
        fresh = _fetch_direct(config, topup_start, end, zone)
        if fresh:
            # Avoid double-counting the boundary timestamp.
            existing_ts = {r[TIME_UTC] for r in db_records}
            fresh = [r for r in fresh if r[TIME_UTC] not in existing_ts]
            db_records.extend(fresh)
            note += f" Topped up {len(fresh)} more recent record(s) directly from the API (through {end.strftime('%Y-%m-%d %H:%M')})."

    return db_records, note


def _to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df[TIME_UTC] = pd.to_datetime(df[TIME_UTC])
    df[TIME_DK] = pd.to_datetime(df[TIME_DK])
    df = df.sort_values(TIME_UTC).drop_duplicates(subset=[ZONE, TIME_UTC])
    return df


def _parse_ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

@mcp.tool()
def negative_price_summary(zone: str, start: str, end: str) -> str:
    """
    Counts negative day-ahead electricity prices for a Danish/Nordic price
    zone over a date range, and shows which hours of the day they cluster
    in. Always includes the most recently published prices, topping up
    from Energi Data Service directly even if TimescaleDB hasn't been
    refreshed recently.

    Args:
        zone: Price zone code, e.g. "DK1", "DK2", "SE3", "NO2", "FI".
        start: Range start, e.g. "2026-01-01" or "2026-01-01T00:00".
        end: Range end (exclusive), e.g. "2026-08-30" or "now" for the
            current time.
    """
    zone = zone.strip().upper()
    if zone not in KNOWN_ZONES:
        return f"Unrecognized zone '{zone}'. Known zones: {', '.join(KNOWN_ZONES)}."

    config = Config.from_env()
    start_ts = _parse_ts(start)
    end_ts = pd.Timestamp.now() if end.strip().lower() == "now" else _parse_ts(end)

    records, note = _get_records_with_latest_topup(config, start_ts, end_ts, zone)
    df = _to_dataframe(records)
    if df.empty:
        return f"No price data found for {zone} between {start_ts} and {end_ts}."

    negative = df[df[PRICE_EUR] < 0]
    total = len(df)
    neg_count = len(negative)
    pct = (neg_count / total * 100) if total else 0.0

    hour_counts = Counter(negative[TIME_DK].dt.hour)
    hour_lines = [
        f"  {hour:02d}:00–{hour:02d}:59  {count} occurrence(s)"
        for hour, count in sorted(hour_counts.items(), key=lambda kv: -kv[1])
    ]

    # Note on units: intervals may mix 1-hour (pre-2025-10-01) and 15-min
    # (from 2025-10-01) resolution, so also report an "hour-equivalent" figure.
    pre_cutoff_neg = negative[negative[TIME_UTC] < CUTOFF]
    post_cutoff_neg = negative[negative[TIME_UTC] >= CUTOFF]
    hour_equivalent = len(pre_cutoff_neg) * 1.0 + len(post_cutoff_neg) * 0.25

    lines = [
        f"Negative day-ahead prices for {zone}, {start_ts.date()} to {end_ts.date()}:",
        f"  Data source: {note}",
        f"  Total price observations: {total}",
        f"  Negative observations: {neg_count} ({pct:.2f}%)",
        f"  Hour-equivalent negative time: ~{hour_equivalent:.1f} hours "
        f"(hourly intervals pre-2025-10-01 count as 1h; 15-min intervals from "
        f"2025-10-01 onward count as 0.25h each)",
        "",
        "Breakdown by hour of day (local Danish time), most-affected first:",
    ]
    lines.extend(hour_lines if hour_lines else ["  (no negative observations in this range)"])

    return "\n".join(lines)


@mcp.tool()
def fetch_latest_prices(zone: str, hours: int = 48) -> str:
    """
    Fetches the most recent day-ahead electricity prices for a zone,
    straight from Energi Data Service (bypasses TimescaleDB entirely, so
    this is always current as of the moment you call it).

    Args:
        zone: Price zone code, e.g. "DK1", "DK2".
        hours: How many hours back from now to fetch (default 48).
    """
    zone = zone.strip().upper()
    if zone not in KNOWN_ZONES:
        return f"Unrecognized zone '{zone}'. Known zones: {', '.join(KNOWN_ZONES)}."

    config = Config.from_env()
    end_ts = pd.Timestamp.now()
    start_ts = end_ts - pd.Timedelta(hours=hours)

    records = _fetch_direct(config, start_ts, end_ts, zone)
    df = _to_dataframe(records)
    if df.empty:
        return f"No recent price data available for {zone} in the last {hours} hours."

    lines = [f"Latest {zone} day-ahead prices (local time, EUR/MWh):"]
    for _, row in df.iterrows():
        marker = "  <- NEGATIVE" if row[PRICE_EUR] < 0 else ""
        lines.append(f"  {row[TIME_DK].strftime('%Y-%m-%d %H:%M')}  {row[PRICE_EUR]:.2f}{marker}")

    return "\n".join(lines)


@mcp.tool()
def query_prices(zone: str, start: str, end: str) -> str:
    """
    Returns summary statistics (min/max/mean price, record count) plus a
    sample of records for a price zone and date range. Reads from
    TimescaleDB when available and tops up with the latest data from
    Energi Data Service directly, same as negative_price_summary.

    Args:
        zone: Price zone code, e.g. "DK1", "DK2".
        start: Range start, e.g. "2026-06-01".
        end: Range end (exclusive), e.g. "2026-06-08" or "now".
    """
    zone = zone.strip().upper()
    if zone not in KNOWN_ZONES:
        return f"Unrecognized zone '{zone}'. Known zones: {', '.join(KNOWN_ZONES)}."

    config = Config.from_env()
    start_ts = _parse_ts(start)
    end_ts = pd.Timestamp.now() if end.strip().lower() == "now" else _parse_ts(end)

    records, note = _get_records_with_latest_topup(config, start_ts, end_ts, zone)
    df = _to_dataframe(records)
    if df.empty:
        return f"No price data found for {zone} between {start_ts} and {end_ts}."

    lines = [
        f"{zone} prices, {start_ts} to {end_ts}:",
        f"  Data source: {note}",
        f"  Records: {len(df)}",
        f"  Min: {df[PRICE_EUR].min():.2f} EUR/MWh",
        f"  Max: {df[PRICE_EUR].max():.2f} EUR/MWh",
        f"  Mean: {df[PRICE_EUR].mean():.2f} EUR/MWh",
        f"  Negative observations: {(df[PRICE_EUR] < 0).sum()}",
        "",
        "First 5 records:",
    ]
    for _, row in df.head(5).iterrows():
        lines.append(f"  {row[TIME_DK]}  {row[PRICE_EUR]:.2f} EUR/MWh")
    lines.append("Last 5 records:")
    for _, row in df.tail(5).iterrows():
        lines.append(f"  {row[TIME_DK]}  {row[PRICE_EUR]:.2f} EUR/MWh")

    return "\n".join(lines)


@mcp.tool()
def refresh_database() -> str:
    """
    Runs the full ingestion pipeline (DayAheadPrices + interpolated
    Elspotprices, all zones) into TimescaleDB, bringing the database up to
    the current moment. Requires TimescaleDB to be running (see README).
    Use this before other tools if you want subsequent queries to hit the
    database instead of falling back to live API calls every time.
    """
    from processing.interpolation import Interpolator

    config = Config.from_env()
    try:
        repo = TimescaleRepository(config.database)
    except PipelineError as exc:
        return f"Could not connect to TimescaleDB: {exc}"

    results = []
    try:
        dayahead_client = DayAheadPricesClient(config.api)
        records = dayahead_client.fetch(config.api.dayahead_start, config.api.end)
        written = repo.write_records(records, extra_fields={"source": "dayahead_real"})
        results.append(f"DayAheadPrices: {written} records written.")
    except PipelineError as exc:
        results.append(f"DayAheadPrices stage failed: {exc}")

    try:
        elspot_client = ElspotpricesClient(config.api)
        interpolator = Interpolator(config.pipeline)
        records = elspot_client.fetch(config.api.elspot_start, config.api.end)
        df_15min = interpolator.to_15min(records)
        df_before_cutoff = interpolator.filter_before_cutoff(df_15min)
        written = repo.write_dataframe(df_before_cutoff, extra_fields={"source": "interpolated_from_elspotprices"})
        results.append(f"Elspotprices (interpolated): {written} records written.")
    except PipelineError as exc:
        results.append(f"Elspotprices stage failed: {exc}")

    repo.close()
    return "Pipeline refresh complete.\n" + "\n".join(f"  - {r}" for r in results)


@mcp.tool()
def support_this_service() -> str:
    """
    Returns a link where users can support/donate to keep this service
    (hosting, database, and maintenance) running. Configure the link via
    the DONATE_URL environment variable.
    """
    donate_url = os.environ.get("DONATE_URL", "").strip()
    if not donate_url:
        return "This service doesn't have a donation link configured yet."
    return (
        "If this service has been useful to you, you can support its "
        f"hosting and upkeep here: {donate_url}\n"
        "Thank you for considering it!"
    )


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8000"))
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )

        # Health check endpoint for Render
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from starlette.applications import Starlette

        async def health(request):
            return JSONResponse({"status": "ok"})
        async def refresh(request):
          # Bearer token check
          auth = request.headers.get("authorization", "")
          token = os.environ.get("MCP_BEARER_TOKEN", "")
          if auth != f"Bearer {token}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
          try:
            result = refresh_database()
            return JSONResponse({"status": "ok", "result": result})
          except Exception as e:
              return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)

      
        mcp_app = mcp.streamable_http_app()

        from starlette.routing import Mount
        app = Starlette(routes=[
                Route("/health", health),
                Route("/refresh", refresh, methods=["POST"]),  # ← ఈ line add చేయండి
                Mount("/", app=mcp_app),])

        import uvicorn
        logger.info(
            "Starting MCP server with streamable-http transport on %s:%s",
            mcp.settings.host,
            mcp.settings.port,
        )
        uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port)
    else:
        mcp.run(transport="stdio")
