"""
schema.py

Canonical field names used internally throughout the pipeline, plus the
per-dataset mapping from each API's raw field names to these canonical
names.

DayAheadPrices and Elspotprices describe the same kind of data (zone,
UTC/Danish timestamps, EUR/DKK price) but use different raw field names:

    DayAheadPrices : TimeUTC, TimeDK, PriceArea, DayAheadPriceEUR, DayAheadPriceDKK
    Elspotprices   : HourUTC, HourDK, PriceArea, SpotPriceEUR,    SpotPriceDKK

Every record is normalized to the canonical schema below immediately after
fetching, so everything downstream (interpolation, Firestore writes) only
ever has to deal with ONE set of field names.
"""

from __future__ import annotations

from typing import Any

ZONE = "zone"
TIME_UTC = "time_utc"
TIME_DK = "time_dk"
PRICE_EUR = "price_eur"
PRICE_DKK = "price_dkk"

CANONICAL_FIELDS = [ZONE, TIME_UTC, TIME_DK, PRICE_EUR, PRICE_DKK]

# raw API field name -> canonical field name
DAYAHEAD_FIELD_MAP: dict[str, str] = {
    "PriceArea": ZONE,
    "TimeUTC": TIME_UTC,
    "TimeDK": TIME_DK,
    "DayAheadPriceEUR": PRICE_EUR,
    "DayAheadPriceDKK": PRICE_DKK,
}

ELSPOT_FIELD_MAP: dict[str, str] = {
    "PriceArea": ZONE,
    "HourUTC": TIME_UTC,
    "HourDK": TIME_DK,
    "SpotPriceEUR": PRICE_EUR,
    "SpotPriceDKK": PRICE_DKK,
}


def normalize_record(record: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
    """Renames a raw API record's keys to the canonical schema. Unmapped
    raw fields are dropped; missing raw fields are simply absent from the
    result (rather than raising), since not every dataset guarantees every
    field on every record."""
    return {
        canonical_key: record[raw_key]
        for raw_key, canonical_key in field_map.items()
        if raw_key in record
    }


def normalize_records(
    records: list[dict[str, Any]], field_map: dict[str, str]
) -> list[dict[str, Any]]:
    return [normalize_record(r, field_map) for r in records]
