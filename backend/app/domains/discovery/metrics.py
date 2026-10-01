"""Channel performance/activity metrics (§3) — pure functions over the stored recent videos."""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

WINDOW = 50  # newest stored videos the metrics are based on (= one playlistItems page)
RECENT_N = 10  # "average views of the last N videos"
VELOCITY_DAYS = 30


@dataclass(frozen=True)
class VideoPoint:
    published_at: datetime | None
    views: int | None


@dataclass(frozen=True)
class Metrics:
    window_videos: int
    avg_views: int | None
    median_views: int | None
    last_video_views: int | None
    avg_views_recent: int | None
    views_to_subs_ratio: float | None
    median_views_to_subs_ratio: float | None
    videos_7d: int
    videos_30d: int
    videos_90d: int
    avg_days_between_uploads: float | None
    last_video_at: datetime | None
    oldest_window_video_at: datetime | None
    upload_consistency: float | None
    views_trend: float | None
    recent_views_velocity: float | None

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def compute_metrics(videos: list[VideoPoint], subscribers: int | None, now: datetime) -> Metrics:
    dated = [v for v in videos if v.published_at is not None]
    dated.sort(key=lambda v: v.published_at or now, reverse=True)
    window = dated[:WINDOW]
    views = [v.views for v in window if v.views is not None]
    times: list[datetime] = [v.published_at for v in window if v.published_at is not None]

    avg = statistics.fmean(views) if views else None
    median = statistics.median(views) if views else None
    recent = [v.views for v in window[:RECENT_N] if v.views is not None]
    subs = subscribers if subscribers and subscribers > 0 else None

    gaps = [(a - b).total_seconds() / 86400 for a, b in zip(times, times[1:], strict=False)]
    avg_gap = statistics.fmean(gaps) if gaps else None
    consistency = None
    if len(gaps) >= 2 and avg_gap and avg_gap > 0:
        # 1 - coefficient of variation of the gaps, clipped to [0, 1]: 1 = metronome, 0 = erratic.
        consistency = min(1.0, max(0.0, 1 - statistics.pstdev(gaps) / avg_gap))

    trend = None
    if len(views) >= 4:
        half = len(views) // 2
        newer, older = statistics.fmean(views[:half]), statistics.fmean(views[-half:])
        trend = newer / older - 1 if older > 0 else None

    velocity_samples = [
        v.views / max((now - v.published_at).total_seconds() / 86400, 1.0)
        for v in window
        if v.views is not None
        and v.published_at is not None
        and now - v.published_at <= timedelta(days=VELOCITY_DAYS)
    ]

    def within(days: int) -> int:
        return sum(1 for t in times if now - t <= timedelta(days=days))

    return Metrics(
        window_videos=len(window),
        avg_views=round(avg) if avg is not None else None,
        median_views=round(median) if median is not None else None,
        last_video_views=window[0].views if window else None,
        avg_views_recent=round(statistics.fmean(recent)) if recent else None,
        views_to_subs_ratio=_round(avg / subs) if avg is not None and subs else None,
        median_views_to_subs_ratio=_round(median / subs) if median is not None and subs else None,
        videos_7d=within(7),
        videos_30d=within(30),
        videos_90d=within(90),
        avg_days_between_uploads=_round(avg_gap, 2),
        last_video_at=times[0] if times else None,
        oldest_window_video_at=times[-1] if times else None,
        upload_consistency=_round(consistency, 3),
        views_trend=_round(trend),
        recent_views_velocity=_round(statistics.fmean(velocity_samples), 2) if velocity_samples else None,
    )
