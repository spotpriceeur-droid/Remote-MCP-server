"""
exceptions.py

Custom exception hierarchy for the Energi Data Service -> Firebase pipeline.
Using specific exception types (instead of letting raw requests/firebase
exceptions bubble up) makes failures easier to catch, log, and handle
differently at the call site (e.g. retry vs. abort vs. skip-and-continue).
"""


class PipelineError(Exception):
    """Base class for all pipeline-specific errors."""


class APIRequestError(PipelineError):
    """Raised when a request to Energi Data Service fails permanently
    (i.e. after retries have been exhausted)."""


class NoDataError(PipelineError):
    """Raised when an API call returns zero records for the requested range."""


class InterpolationError(PipelineError):
    """Raised when hourly-to-15-minute interpolation fails."""


class DatabaseConnectionError(PipelineError):
    """Raised when the database (TimescaleDB) cannot be reached or the
    table/hypertable cannot be created/verified."""


class DatabaseWriteError(PipelineError):
    """Raised when writing records to the database fails."""
