"""Entry point: `python -m agent`."""
import asyncio
import logging
import sys

from .main import run


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


if __name__ == "__main__":
    _configure_logging()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
