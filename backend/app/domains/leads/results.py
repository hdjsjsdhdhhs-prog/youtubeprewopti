"""Result of a rescore — a module of its own so other domains (feedback) can return it without importing
the whole leads schema module (which imports theirs)."""

from __future__ import annotations

from pydantic import BaseModel


class RescoreResult(BaseModel):
    leads_created: int  # channels of the workspace's projects that had no lead yet
    leads_analyzed: int  # DISCOVERED → ANALYZED (thumbnails audited)
    scores_changed: int  # leads whose score changed (a new score row)
    scores_unchanged: int
    system_tags_changed: int = 0  # system tags added + removed by their rules (Phase 4)
