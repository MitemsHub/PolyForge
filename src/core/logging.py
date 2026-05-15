from __future__ import annotations

from src.core.config import Settings
from src.utils.logging import configure_logging as _configure_logging


def configure_logging(settings: Settings) -> None:
    _configure_logging(settings)
