"""
dayahead_client.py

Client for the DayAheadPrices dataset (native 15-minute resolution,
available from ~2025-10-01 onward). Fetches all price zones by simply
not filtering on "PriceArea", and normalizes raw fields (TimeUTC, TimeDK,
PriceArea, DayAheadPriceEUR, DayAheadPriceDKK) to the canonical schema.
"""

from __future__ import annotations

from typing import Any, Optional

from api.base_client import EnergiDataClient
from config import APIConfig
from schema import DAYAHEAD_FIELD_MAP, normalize_records


class DayAheadPricesClient(EnergiDataClient):
    def __init__(self, config: APIConfig):
        super().__init__(config.dayahead_url, config)

    def fetch(self, start: str, end: str, zone: Optional[str] = None) -> list[dict[str, Any]]:
        """Fetches DayAheadPrices records in the given range, normalized
        to the canonical schema (see schema.py). Omit `zone` to fetch all
        price zones; pass e.g. "DK1" to filter server-side."""
        extra_params = {"filter": f'{{"PriceArea":["{zone}"]}}'} if zone else None
        raw_records = self.fetch_all_records(start, end, extra_params=extra_params)
        return normalize_records(raw_records, DAYAHEAD_FIELD_MAP)
