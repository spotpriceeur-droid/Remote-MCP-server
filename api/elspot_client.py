"""
elspot_client.py

Client for the Elspotprices dataset (hourly resolution, ~4 years of
history). Fetches all price zones by not filtering on "PriceArea", and
normalizes raw fields (HourUTC, HourDK, PriceArea, SpotPriceEUR,
SpotPriceDKK) to the canonical schema.
"""

from __future__ import annotations

from typing import Any, Optional

from api.base_client import EnergiDataClient
from config import APIConfig
from schema import ELSPOT_FIELD_MAP, normalize_records


class ElspotpricesClient(EnergiDataClient):
    def __init__(self, config: APIConfig):
        super().__init__(config.elspot_url, config)

    def fetch(self, start: str, end: str, zone: Optional[str] = None) -> list[dict[str, Any]]:
        """Fetches Elspotprices records in the given range, normalized to
        the canonical schema (see schema.py). Omit `zone` to fetch all
        price zones; pass e.g. "DK1" to filter server-side."""
        extra_params = {"filter": f'{{"PriceArea":["{zone}"]}}'} if zone else None
        raw_records = self.fetch_all_records(start, end, extra_params=extra_params)
        return normalize_records(raw_records, ELSPOT_FIELD_MAP)
