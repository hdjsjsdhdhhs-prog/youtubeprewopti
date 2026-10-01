from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.domains.analysis.schemas import ThumbnailPrefilter
from app.domains.projects.models import ProjectStatus, QueryStatus, SearchType, TaxonomyLevel
from app.domains.youtube.schemas import ChannelFilterSet

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
RegionCode = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{2}$")]
Language = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
]


class _ProjectFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=5000)
    language: Language | None = None
    region_code: RegionCode | None = None
    results_per_query: int | None = Field(default=None, ge=1, le=500)
    search_depth: int | None = Field(default=None, ge=1, le=10)
    published_after: date | None = None
    videos_to_analyze: int | None = Field(default=None, ge=1, le=50)
    filter_settings: ChannelFilterSet | None = None  # saved channel filters of the project (§3, §48)
    prefilter_settings: ThumbnailPrefilter | None = None  # which thumbnails go to AI analysis


class ProjectCreate(_ProjectFields):
    name: Name


class ProjectUpdate(_ProjectFields):
    name: Name | None = None
    status: ProjectStatus | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    language: str | None
    region_code: str | None
    results_per_query: int
    search_depth: int
    published_after: date | None
    videos_to_analyze: int
    filter_settings: dict[str, Any]
    prefilter_settings: dict[str, Any]
    status: ProjectStatus
    is_demo: bool
    created_at: datetime
    updated_at: datetime
    queries_count: int = 0
    channels_count: int = 0


MAX_BULK_QUERIES = 5000
MAX_QUERY_LENGTH = 300


class QueryBulkImport(BaseModel):
    """Either ``queries`` (list) or ``text`` (one query per line, e.g. pasted from a spreadsheet).

    ``taxonomy_node_id`` tags every *new* query with a niche/topic (§2: remember by which niche a channel
    was found); ``search_type=channel`` searches channels by topic instead of videos by keyword."""

    model_config = ConfigDict(extra="forbid")
    queries: list[str] | None = Field(default=None, max_length=MAX_BULK_QUERIES)
    text: str | None = Field(default=None, max_length=MAX_BULK_QUERIES * (MAX_QUERY_LENGTH + 2))
    taxonomy_node_id: int | None = None
    search_type: SearchType = SearchType.VIDEO

    @model_validator(mode="after")
    def _exactly_one(self) -> QueryBulkImport:
        if (self.queries is None) == (self.text is None):
            raise ValueError("provide exactly one of 'queries' or 'text'")
        return self

    def lines(self) -> list[str]:
        return self.queries if self.queries is not None else (self.text or "").splitlines()

    @field_validator("text")
    @classmethod
    def _line_count(cls, v: str | None) -> str | None:
        if v is not None and len(v.splitlines()) > MAX_BULK_QUERIES:
            raise ValueError(f"at most {MAX_BULK_QUERIES} lines")
        return v


class QueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    status: QueryStatus
    taxonomy_node_id: int | None
    search_type: SearchType
    last_run_at: datetime | None
    results_count: int
    created_at: datetime


class RejectedLine(BaseModel):
    line: int  # 1-based
    value: str
    reason: str


class QueryBulkResult(BaseModel):
    created: int
    duplicates: int
    rejected: list[RejectedLine]
    items: list[QueryOut]


class QueryUpdate(BaseModel):
    """Only fields present in the body change; ``taxonomy_node_id: null`` removes the niche tag."""

    model_config = ConfigDict(extra="forbid")
    taxonomy_node_id: int | None = None
    search_type: SearchType | None = None


# --- taxonomy (niche > topic > subtopic) ---------------------------------------------------------
MAX_BULK_NODES = 2000


class TaxonomyNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    level: TaxonomyLevel
    name: str
    slug: str


class TaxonomyBulkImport(BaseModel):
    """One name per line under ``parent_id`` (omitted = top-level niches)."""

    model_config = ConfigDict(extra="forbid")
    parent_id: int | None = None
    text: str = Field(min_length=1, max_length=MAX_BULK_NODES * 202)

    @field_validator("text")
    @classmethod
    def _line_count(cls, v: str) -> str:
        if len(v.splitlines()) > MAX_BULK_NODES:
            raise ValueError(f"at most {MAX_BULK_NODES} lines")
        return v


class TaxonomyBulkResult(BaseModel):
    created: int
    duplicates: int
    rejected: list[RejectedLine]
    items: list[TaxonomyNodeOut]  # created + already existing nodes with these names


class ProjectNichesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    taxonomy_node_ids: list[int] = Field(max_length=500)
