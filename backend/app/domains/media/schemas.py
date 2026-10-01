from __future__ import annotations

from pydantic import BaseModel

from app.domains.jobs.schemas import JobRunOut


class ThumbnailIngestResult(BaseModel):
    job: JobRunOut | None  # None => every thumbnail is downloaded and has current metrics
    created: bool  # False => an identical ingestion job is already active (returned)
    items: int
    remaining: int  # not included because of the per-job cap; start again after this job


class ThumbnailStatsOut(BaseModel):
    videos: int
    with_thumbnail: int
    downloaded: int
    pending: int
    failed: int
    with_metrics: int
    metrics_algo_version: int
