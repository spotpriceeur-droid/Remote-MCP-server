"""
interpolation.py

Upsamples hourly per-zone price data (normalized Elspotprices records) to
15-minute resolution, and filters out any points at/after the real-data
cutoff so estimated values never collide with genuine 15-min measurements.

Operates entirely on the canonical schema (see schema.py), so it doesn't
care which raw API the records originally came from.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from config import PipelineConfig
from exceptions import InterpolationError
from schema import PRICE_DKK, PRICE_EUR, TIME_DK, TIME_UTC, ZONE

logger = logging.getLogger(__name__)

_PRICE_COLUMNS = [PRICE_EUR, PRICE_DKK]


class Interpolator:
    """Converts hourly, canonical-schema records into a 15-minute time series."""

    def __init__(self, config: PipelineConfig):
        self.config = config

    def to_15min(self, records: list[dict[str, Any]]) -> pd.DataFrame:
        """
        Groups records by zone, resamples each zone's time index to the
        target resolution, and linearly interpolates the price columns.

        Raises:
            InterpolationError: if the input can't be parsed/resampled
            (e.g. missing expected canonical columns).
        """
        try:
            df = pd.DataFrame(records)

            missing = [c for c in (ZONE, TIME_UTC, *_PRICE_COLUMNS) if c not in df.columns]
            if missing:
                raise InterpolationError(
                    f"Input records are missing required canonical field(s): {missing}. "
                    f"Did they go through schema.normalize_records() first?"
                )

            df[TIME_UTC] = pd.to_datetime(df[TIME_UTC])

            zone_frames = []
            for zone, zone_df in df.groupby(ZONE):
                zone_df = zone_df.sort_values(TIME_UTC).set_index(TIME_UTC)

                resampled = (
                    zone_df[_PRICE_COLUMNS]
                    .resample(self.config.target_resolution)
                    .interpolate(method="linear")
                )

                # Recompute local Danish time correctly across DST
                # transitions, rather than assuming a fixed UTC offset.
                local_time = (
                    resampled.index.tz_localize("UTC")
                    .tz_convert("Europe/Copenhagen")
                    .tz_localize(None)
                )
                resampled[TIME_DK] = local_time
                resampled[ZONE] = zone
                zone_frames.append(resampled.reset_index())

            return pd.concat(zone_frames, ignore_index=True)

        except InterpolationError:
            raise
        except Exception as exc:  # noqa: BLE001 - convert to domain error
            raise InterpolationError(f"Interpolation failed: {exc}") from exc

    def filter_before_cutoff(self, df: pd.DataFrame) -> pd.DataFrame:
        """Keeps only rows strictly before the configured real-data cutoff."""
        cutoff = pd.Timestamp(self.config.real_data_cutoff)
        filtered = df[df[TIME_UTC] < cutoff].copy()
        logger.info(
            "%d of %d interpolated rows are before cutoff %s and will be kept.",
            len(filtered), len(df), self.config.real_data_cutoff,
        )
        return filtered
