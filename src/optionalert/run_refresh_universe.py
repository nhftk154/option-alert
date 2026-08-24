"""Entrypoint for refresh_universe.yml. Only writes data/universe_cache.json —
the git add/commit/push happens in the workflow YAML, not here, so git
operations stay out of the frequent-run (scan.yml) codepath entirely."""

import logging
import sys

from .universe import build_universe, save_universe_cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    logger.info("building S&P universe by market cap...")
    universe = build_universe()
    logger.info(
        "universe built: %d equities, %d metals, %d crypto",
        len(universe.equities), len(universe.metals), len(universe.crypto),
    )
    if len(universe.equities) < 100:
        logger.error("only %d equities resolved - refusing to overwrite cache", len(universe.equities))
        return 1
    save_universe_cache(universe)
    logger.info("wrote universe cache")
    return 0


if __name__ == "__main__":
    sys.exit(main())
