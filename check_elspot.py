"""
fetch_elspot_v2.py

Step 1 of 2 (corrected): Fetches Elspotprices records only up to the
real-data cutoff (data after that point is discarded during
interpolation anyway, so there's no point fetching or waiting on it).
Saves to disk even if a later chunk unexpectedly fails, so a single bad
chunk can never lose everything already fetched.

Run with:
    nohup uv run python fetch_elspot_v2.py > fetch.log 2>&1 &
"""

from config import Config
from api.elspot_client import ElspotpricesClient
from exceptions import NoDataError
import pandas as pd
import pickle
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_PICKLE = "elspot_records_backup.pkl"
OUTPUT_JSON = "elspot_records_backup.json"

config = Config.from_env()
client = ElspotpricesClient(config.api)

start = pd.Timestamp(config.api.elspot_start)
# Stop at the cutoff -- anything after this gets filtered out during
# interpolation anyway, so fetching it would be wasted time and rate-limit budget.
end = pd.Timestamp(config.pipeline.real_data_cutoff)

all_records = []
current = start


def save_progress():
    with open(OUTPUT_PICKLE, "wb") as f:
        pickle.dump(all_records, f)
    with open(OUTPUT_JSON, "w") as f:
        json.dump(all_records, f, default=str)
    print(f"Saved {len(all_records)} records to {OUTPUT_PICKLE} and {OUTPUT_JSON}")


while current < end:
    chunk_end = min(current + pd.DateOffset(months=6), end)
    try:
        records = client.fetch(
            current.strftime("%Y-%m-%dT%H:%M"),
            chunk_end.strftime("%Y-%m-%dT%H:%M"),
        )
        all_records.extend(records)
        print(f"Fetched {len(records)} records for {current.date()} -> {chunk_end.date()} "
              f"(running total: {len(all_records)})")
    except NoDataError:
        print(f"No data for {current.date()} -> {chunk_end.date()}; stopping here.")
        break
    except Exception as exc:
        # Any other failure: save what we have before letting it crash, so
        # nothing already fetched is lost.
        print(f"Unexpected error on chunk {current.date()} -> {chunk_end.date()}: {exc}")
        save_progress()
        raise

    current = chunk_end
    # Save after every successful chunk too, not just at the very end --
    # cheap insurance against any future crash.
    save_progress()

print(f"\nTotal fetched: {len(all_records)} records")
save_progress()
print("\nDone. Nothing written to the database yet — run ingest_elspot.py next.")