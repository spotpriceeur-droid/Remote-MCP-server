"""
logging_config.py

Central logging setup, called once from main.py. Keeping this in one place
means every module just does `logging.getLogger(__name__)` and gets
consistent formatting for free.
"""

from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
