"""
base_client.py

Shared HTTP + pagination + retry logic for Energi Data Service endpoints.
Both DayAheadPricesClient and ElspotpricesClient inherit from this, so
the pagination/retry behavior only needs to be written and tested once.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from config import APIConfig
from exceptions import APIRequestError, NoDataError

logger = logging.getLogger(__name__)


class EnergiDataClient:
    """Base client for the Energi Data Service REST API."""

    def __init__(self, url: str, config: APIConfig):
        self.url = url
        self.config = config
        self.session = requests.Session()

    def _get_with_retry(self, params: dict[str, Any]) -> dict[str, Any]:
        """Performs a GET request, retrying on transient network errors.
        429 responses get a longer, Retry-After-aware backoff since they
        mean "you're going too fast", not "something is broken"."""
        last_exception: Optional[Exception] = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.session.get(
                    self.url,
                    params=params,
                    timeout=self.config.request_timeout_seconds,
                )
                response.raise_for_status()
                return response.json()

            except requests.RequestException as exc:
                last_exception = exc
                status = getattr(exc.response, "status_code", None)

                if status == 429:
                    retry_after = None
                    if exc.response is not None:
                        retry_after = exc.response.headers.get("Retry-After")
                    wait_seconds = (
                        float(retry_after) if retry_after else
                        self.config.rate_limit_backoff_seconds * attempt
                    )
                    logger.warning(
                        "Rate limited by %s (attempt %d/%d); waiting %.1fs before retrying.",
                        self.url, attempt, self.config.max_retries, wait_seconds,
                    )
                    if attempt < self.config.max_retries:
                        time.sleep(wait_seconds)
                    continue

                logger.warning(
                    "Request to %s failed (attempt %d/%d): %s",
                    self.url, attempt, self.config.max_retries, exc,
                )
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_backoff_seconds * attempt)

        raise APIRequestError(
            f"Failed to fetch data from {self.url} after "
            f"{self.config.max_retries} attempts"
        ) from last_exception

    def fetch_all_records(
        self,
        start: str,
        end: str,
        extra_params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetches every record between `start` and `end`, following pagination
        automatically. Omitting a "PriceArea" key in extra_params returns
        data for ALL price zones.

        Raises:
            APIRequestError: if a page fails after all retries.
            NoDataError: if the range returns zero records overall.
        """
        all_records: list[dict[str, Any]] = []
        offset = 0
        extra_params = extra_params or {}

        while True:
            params = {
                "start": start,
                "end": end,
                "limit": self.config.page_limit,
                "offset": offset,
                **extra_params,
            }

            data = self._get_with_retry(params)
            records = data.get("records", [])

            if not records:
                break

            all_records.extend(records)
            logger.info(
                "Downloaded %d records so far from %s...",
                len(all_records), self.url,
            )

            if len(records) < self.config.page_limit:
                break

            offset += self.config.page_limit

        if not all_records:
            raise NoDataError(
                f"No records returned from {self.url} for range {start} -> {end}"
            )

        return all_records
