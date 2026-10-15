"""Import all ORM models so that Alembic and relationship resolution see the full metadata."""

from app.core.db import Base
from app.domains.ai.models import AICall, AIModel, Budget, PromptTemplate
from app.domains.analysis.models import ChannelAnalysis, ScoringProfile, ThumbnailAnalysis
from app.domains.approvals.models import Approval
from app.domains.discovery.models import YouTubeQuotaLedger
from app.domains.favorites.models import Favorite
from app.domains.feedback.models import FeedbackEvent, ScoreOverride
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
from app.domains.leads.models import IdealLeadProfile, Lead, LeadScore, LeadStatusHistory, LeadTag, Tag
from app.domains.media.models import ImageAsset, ImageMetrics, Thumbnail
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
    "AICall", "AIModel", "Approval", "AuditLog", "Base", "Budget", "Channel", "ChannelAnalysis",
    "ChannelDiscovery", "ChannelMetrics",
    "ChannelStatsSnapshot", "Favorite", "FeedbackEvent", "IdealLeadProfile", "IdempotencyKey", "ImageAsset",
    "ImageMetrics", "JobRun", "Lead", "LeadScore", "LeadStatusHistory", "LeadTag", "ProjectChannel",
    "ProjectNiche", "PromptTemplate", "ScoreOverride", "ScoringProfile", "SearchProject",
    "SearchQuery", "Secret", "Tag", "TaxonomyNode", "Thumbnail", "ThumbnailAnalysis", "User", "UserSession",
    "Video",
    "VideoStatsSnapshot", "Workspace", "WorkspaceMember", "YouTubeQuotaLedger",
]
