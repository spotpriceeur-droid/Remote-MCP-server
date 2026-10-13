#!/usr/bin/env python3
"""Export DK1 and DK2 prices for 2025 from the configured TimescaleDB.

Usage: python download_dk_dayahead_prices.py
Requires TIMESCALE_HOST, TIMESCALE_PORT, TIMESCALE_DBNAME,
TIMESCALE_USER, and TIMESCALE_PASSWORD. Set TIMESCALE_SSL=require for Aiven.

Output (in the current folder):
  dayahead_DK1_DK2_2025.csv         (database resolution, includes source)
  dayahead_DK1_DK2_2025_hourly.csv  (hourly EUR averages, wide format)
"""
import pandas as pd

from config import Config
from storage.timescale_repository import TimescaleRepository

START_UTC = "2025-01-01T00:00:00+00:00"
END_UTC = "2026-01-01T00:00:00+00:00"
ZONES = ("DK1", "DK2")


def fetch_from_database(repo: TimescaleRepository) -> pd.DataFrame:
    records = []
    for zone in ZONES:
        zone_records = repo.fetch_range(START_UTC, END_UTC, zone)
        if not zone_records:
            raise RuntimeError(f"No 2025 records found in the database for {zone}.")
        records.extend(zone_records)

    df = pd.DataFrame(records).rename(columns={
        "zone": "PriceArea",
        "time_utc": "TimeUTC",
        "price_eur": "DayAheadPriceEUR",
        "price_dkk": "DayAheadPriceDKK",
        "source": "Source",
    })
    df["TimeUTC"] = pd.to_datetime(df["TimeUTC"], utc=True)
    df["TimeDK"] = df["TimeUTC"].dt.tz_convert("Europe/Copenhagen")
    return df[[
        "TimeUTC", "TimeDK", "PriceArea", "DayAheadPriceEUR",
        "DayAheadPriceDKK", "Source",
    ]].sort_values(["TimeUTC", "PriceArea"]).reset_index(drop=True)


def main():
    repo = TimescaleRepository(Config.from_env().database)
    try:
        df = fetch_from_database(repo)
    finally:
        repo.close()

    df.to_csv("dayahead_DK1_DK2_2025.csv", index=False)
    hourly = (
        df.set_index("TimeUTC")
        .groupby("PriceArea")["DayAheadPriceEUR"]
        .resample("1h")
        .mean()
        .unstack(0)
        .rename(columns={"DK1": "DK1_EUR_per_MWh", "DK2": "DK2_EUR_per_MWh"})
    )
    hourly.to_csv("dayahead_DK1_DK2_2025_hourly.csv")
    print("2025 rows:", len(df), "| hourly rows:", len(hourly))


if __name__ == "__main__":
    main()
