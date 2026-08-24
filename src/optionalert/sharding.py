"""Stateless sharding: which slice of the 300-name universe a given run
scans. Derived purely from wall-clock time, so nothing needs to be persisted
across GitHub Actions runs just to know "whose turn it is"."""

from datetime import datetime, timezone


def get_shard_index(now: datetime | None = None, n_shards: int = 6, interval_minutes: int = 10) -> int:
    now = now or datetime.now(timezone.utc)
    bucket = int(now.timestamp() // (interval_minutes * 60))
    return bucket % n_shards


def select_shard(tickers: list[str], shard_index: int, n_shards: int) -> list[str]:
    """Interleaved slice, not a contiguous block: since `tickers` is market-cap
    sorted, tickers[shard_index::n_shards] keeps each shard a mix of large and
    mid caps rather than segregating all mega-caps into shard 0."""
    return tickers[shard_index::n_shards]
