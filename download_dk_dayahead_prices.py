#!/usr/bin/env python3
"""Download DK1 and DK2 day-ahead prices (2025 and 2026) from Energi Data Service.

Usage:  python download_dk_dayahead_prices.py
Needs:  pip install requests pandas

Output (in the current folder):
  dayahead_DK1_DK2_2025.csv   (native resolution: hourly, 15-min from 2025-10-01)
  dayahead_DK1_DK2_2026.csv
  dayahead_DK1_DK2_2025_hourly.csv   (hourly averages, wide format)
  dayahead_DK1_DK2_2026_hourly.csv
"""
import json
import requests
import pandas as pd

URL = "https://api.energidataservice.dk/dataset/DayAheadPrices"


def fetch(start, end):
    params = {
        "start": start,
        "end": end,
        "filter": json.dumps({"PriceArea": ["DK1", "DK2"]}),
        "sort": "TimeUTC asc",
        "limit": 0,
        "timezone": "utc",
    }
    r = requests.get(URL, params=params, timeout=120)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["records"])
    df["TimeUTC"] = pd.to_datetime(df["TimeUTC"], utc=True)
    return df[["TimeUTC", "TimeDK", "PriceArea", "DayAheadPriceEUR"]]


def main():
    for year in (2025, 2026):
        df = fetch(f"{year}-01-01T00:00", f"{year + 1}-01-01T00:00")
        df.to_csv(f"dayahead_DK1_DK2_{year}.csv", index=False)

        hourly = (
            df.set_index("TimeUTC")
            .groupby("PriceArea")["DayAheadPriceEUR"]
            .resample("1h")
            .mean()
            .unstack(0)
            .rename(columns={"DK1": "DK1_EUR_per_MWh", "DK2": "DK2_EUR_per_MWh"})
        )
        hourly.to_csv(f"dayahead_DK1_DK2_{year}_hourly.csv")
        print(year, "rows:", len(df), "| hourly rows:", len(hourly))


if __name__ == "__main__":
    main()
