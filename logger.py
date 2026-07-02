# utils/logger.py
"""
Centralized logging setup for the bot.
Every module gets its logger via get_logger(__name__) instead of calling
logging.getLogger() directly, so format and noisy third-party log levels
stay consistent everywhere without repeating setup code.
"""

import logging


def _configure_root_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )
    noisy_loggers = ("httpx", "httpcore", "telegram", "apscheduler", "hpack")
    for name in noisy_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)


_configure_root_logging()


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger scoped to the given module name.
    Usage: logger = get_logger(__name__)
    """
    return logging.getLogger(name)
