"""Import all ORM models so that Alembic and relationship resolution see the full metadata."""

from app.core.db import Base
from app.domains.discovery.models import YouTubeQuotaLedger
from app.domains.identity.models import (
    AuditLog,
    IdempotencyKey,
    Secret,
    User,
    UserSession,
    Workspace,
    WorkspaceMember,
)
from app.domains.jobs.models import JobRun
from app.domains.media.models import ImageAsset, Thumbnail
from app.domains.projects.models import (
    ChannelDiscovery,
    ProjectChannel,
    ProjectNiche,
    SearchProject,
    SearchQuery,
    TaxonomyNode,
)
from app.domains.youtube.models import (
    Channel,
    ChannelMetrics,
    ChannelStatsSnapshot,
    Video,
    VideoStatsSnapshot,
)

__all__ = [
    "AuditLog", "Base", "Channel", "ChannelDiscovery", "ChannelMetrics", "ChannelStatsSnapshot",
    "IdempotencyKey", "ImageAsset", "JobRun", "ProjectChannel", "ProjectNiche", "SearchProject",
    "SearchQuery", "Secret", "TaxonomyNode", "Thumbnail", "User", "UserSession", "Video",
    "VideoStatsSnapshot", "Workspace", "WorkspaceMember", "YouTubeQuotaLedger",
]
