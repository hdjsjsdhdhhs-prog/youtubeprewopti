from datetime import UTC, datetime, timedelta

from app.domains.discovery.metrics import WINDOW, VideoPoint, compute_metrics

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _videos(spec: list[tuple[float, int | None]]) -> list[VideoPoint]:
    """(days ago, views)"""
    return [VideoPoint(NOW - timedelta(days=d), v) for d, v in spec]


def test_regular_channel():
    # every 7 days, newest first; views grow over time (newer = more)
    vids = _videos([(1, 8000), (8, 7000), (15, 6000), (22, 5000), (29, 4000), (36, 3000)])
    m = compute_metrics(vids, subscribers=10_000, now=NOW)
    assert m.window_videos == 6
    assert m.avg_views == 5500 and m.median_views == 5500 and m.last_video_views == 8000
    assert m.views_to_subs_ratio == 0.55 and m.median_views_to_subs_ratio == 0.55
    assert (m.videos_7d, m.videos_30d, m.videos_90d) == (1, 5, 6)
    assert m.avg_days_between_uploads == 7.0 and m.upload_consistency == 1.0
    assert m.views_trend == round(7000 / 4000 - 1, 4)  # newer half 8000/7000/6000 vs older 5000/4000/3000
    assert m.last_video_at == NOW - timedelta(days=1) and m.oldest_window_video_at == NOW - timedelta(days=36)
    # velocity: videos of the last 30 days, views per day of age
    assert m.recent_views_velocity == round((8000 / 1 + 7000 / 8 + 6000 / 15 + 5000 / 22 + 4000 / 29) / 5, 2)


def test_irregular_and_sparse_inputs():
    m = compute_metrics(_videos([(2, 100), (3, None), (60, 50)]), subscribers=0, now=NOW)
    assert m.avg_views == 75 and m.views_to_subs_ratio is None  # 0 subscribers → no ratio
    assert m.upload_consistency is not None and 0 <= m.upload_consistency < 0.5
    assert m.views_trend is None  # fewer than 4 data points

    empty = compute_metrics([VideoPoint(None, 10)], subscribers=None, now=NOW)
    assert empty.window_videos == 0 and empty.avg_views is None and empty.last_video_at is None
    assert (empty.videos_30d, empty.upload_consistency, empty.recent_views_velocity) == (0, None, None)


def test_window_uses_newest_videos_only():
    vids = _videos([(d, 1000 if d <= WINDOW else 1) for d in range(1, WINDOW + 20)])
    m = compute_metrics(list(reversed(vids)), subscribers=1000, now=NOW)  # input order does not matter
    assert m.window_videos == WINDOW and m.avg_views == 1000 and m.last_video_at == NOW - timedelta(days=1)
