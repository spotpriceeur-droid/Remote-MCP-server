"""
fetch_elspot_historical.py

Backfill script: fetches Elspotprices records for the period
    2021-01-01 00:00  →  2022-09-15 23:00

This fills the gap before the main fetch_elspot_v2.py run, which
already covers 2022-09-16 onward.  Output is saved to separate pickle
and JSON files so it can be ingested independently without touching the
data already fetched.

Run with:
    nohup uv run python fetch_elspot_historical.py > fetch_historical.log 2>&1 &
"""

from config import Config
from api.elspot_client import ElspotpricesClient
from exceptions import NoDataError
import pandas as pd
import pickle
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_PICKLE = "elspot_historical_backup.pkl"
OUTPUT_JSON   = "elspot_historical_backup.json"

# ── Date range ────────────────────────────────────────────────────────────────
# Start: first hour of 2021
HISTORICAL_START = pd.Timestamp("2021-01-01 00:00")
# End:   last hour before the existing dataset begins (exclusive upper bound)
#        i.e. up to and including 2022-09-15 23:00
HISTORICAL_END   = pd.Timestamp("2022-09-16 00:00")   # chunk loop uses <, so
                                                        # the last fetched hour
                                                        # is 2022-09-15 23:xx

config = Config.from_env()
client = ElspotpricesClient(config.api)

all_records: list = []
current = HISTORICAL_START


def save_progress() -> None:
    with open(OUTPUT_PICKLE, "wb") as f:
        pickle.dump(all_records, f)
    with open(OUTPUT_JSON, "w") as f:
        json.dump(all_records, f, default=str)
    print(
        f"Saved {len(all_records)} records to "
        f"{OUTPUT_PICKLE} and {OUTPUT_JSON}"
    )


while current < HISTORICAL_END:
    chunk_end = min(current + pd.DateOffset(months=6), HISTORICAL_END)

    try:
        records = client.fetch(
            current.strftime("%Y-%m-%dT%H:%M"),
            chunk_end.strftime("%Y-%m-%dT%H:%M"),
        )
        all_records.extend(records)
        print(
            f"Fetched {len(records)} records for "
            f"{current.date()} -> {chunk_end.date()} "
            f"(running total: {len(all_records)})"
        )

    except NoDataError:
        print(
            f"No data for {current.date()} -> {chunk_end.date()}; "
            "stopping here."
        )
        break

    except Exception as exc:
        # Save before re-raising so nothing fetched so far is lost.
        print(
            f"Unexpected error on chunk "
            f"{current.date()} -> {chunk_end.date()}: {exc}"
        )
        save_progress()
        raise

    current = chunk_end
    save_progress()   # cheap insurance after every successful chunk


print(f"\nTotal fetched: {len(all_records)} records")
save_progress()
print(
    "\nDone. Run ingest_elspot.py (or your equivalent ingest script) "
    "against elspot_historical_backup.pkl to load into the database."
)