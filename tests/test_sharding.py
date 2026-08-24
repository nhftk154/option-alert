from datetime import datetime, timedelta, timezone

from optionalert.sharding import get_shard_index, select_shard


def test_shard_index_deterministic_for_same_bucket():
    now = datetime(2024, 7, 15, 14, 3, tzinfo=timezone.utc)
    a = get_shard_index(now, n_shards=6, interval_minutes=10)
    b = get_shard_index(now, n_shards=6, interval_minutes=10)
    assert a == b


def test_shard_index_changes_across_buckets():
    base = datetime(2024, 7, 15, 14, 0, tzinfo=timezone.utc)
    indices = {get_shard_index(base + timedelta(minutes=10 * i), 6, 10) for i in range(6)}
    assert indices == {0, 1, 2, 3, 4, 5}


def test_select_shard_interleaves_not_contiguous():
    tickers = [f"T{i}" for i in range(12)]
    shard0 = select_shard(tickers, 0, 6)
    assert shard0 == ["T0", "T6"]


def test_full_coverage_across_six_consecutive_cycles_no_gaps_no_overlap():
    tickers = [f"T{i}" for i in range(300)]
    base = datetime(2024, 7, 15, 14, 0, tzinfo=timezone.utc)

    seen = []
    for i in range(6):
        now = base + timedelta(minutes=10 * i)
        idx = get_shard_index(now, n_shards=6, interval_minutes=10)
        seen.extend(select_shard(tickers, idx, 6))

    assert sorted(seen) == sorted(tickers)
    assert len(seen) == len(set(seen))
