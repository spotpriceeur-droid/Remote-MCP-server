"""
ingest_elspot.py

Step 2 of 2: Loads the Elspotprices records already saved to disk by
fetch_elspot.py, interpolates them to 15-minute resolution, and writes
them into TimescaleDB. Never calls Energi Data Service, so it's cheap
and safe to re-run as many times as needed if the DB write fails.

Requires TIMESCALE_HOST / TIMESCALE_PORT / TIMESCALE_DBNAME /
TIMESCALE_USER / TIMESCALE_PASSWORD / TIMESCALE_SSL to be exported in
this shell (same ones you used to confirm 'DB connection OK' earlier).

Run with:
    uv run python ingest_elspot.py
"""

from config import Config
from processing.interpolation import Interpolator
from storage.timescale_repository import TimescaleRepository
import pickle
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

INPUT_PICKLE =  "elspot_historical_backup.pkl" #"elspot_records_backup.pkl"

config = Config.from_env()

print(f"Loading records from {INPUT_PICKLE}...")
with open(INPUT_PICKLE, "rb") as f:
    all_records = pickle.load(f)
print(f"Loaded {len(all_records)} records")

interpolator = Interpolator(config.pipeline)
df_15min = interpolator.to_15min(all_records)
df_before_cutoff = interpolator.filter_before_cutoff(df_15min)
print(f"Rows before cutoff (to be written): {len(df_before_cutoff)}")

repo = TimescaleRepository(config.database)
written = repo.write_dataframe(df_before_cutoff, extra_fields={"source": "interpolated_from_elspotprices"})
print(f"Wrote {written} records to TimescaleDB")
repo.close()

print("\nDone. Verify with:")
print("  SELECT source, COUNT(*) FROM day_ahead_prices GROUP BY source;")