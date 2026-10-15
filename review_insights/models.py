"""Validated data contracts shared by the application modules."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrgRef(StrictModel):
    org_id: str
    url: str


class Review(StrictModel):
    review_id: str
    text: str = Field(min_length=1)
    rating: int | None = Field(ge=1, le=5)
    published_at: datetime | None
    date_label: str
    source_url: str


class CollectionResult(StrictModel):
    org: OrgRef
    org_name: str
    collected_at: datetime
    source_review_count: int | None
    source_rating_count: int | None
    source_rating: float | None
    reviews: list[Review]
    stop_reason: Literal[
        "exhausted",
        "limit",
        "deadline",
        "blocked",
        "rate_limited",
        "load_error",
        "layout_changed",
        "no_new_reviews",
    ]
    sort_mode: Literal["newest", "default", "mixed"]
    warnings: list[str]


class Evidence(StrictModel):
    review_id: str
    quote: str = Field(min_length=1, max_length=240)


class Theme(StrictModel):
    theme_id: str
    kind: Literal["strength", "problem"]
    scope: Literal["place", "network_or_other", "unclear"]
    title: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=450)
    supporting_review_ids: list[str] = Field(min_length=1, max_length=200)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class Action(StrictModel):
    title: str = Field(min_length=1, max_length=140)
    source_theme_ids: list[str] = Field(min_length=1, max_length=10)
    next_step: str = Field(min_length=1, max_length=450)


class Hypothesis(StrictModel):
    hypothesis: str = Field(min_length=1, max_length=350)
    source_theme_ids: list[str] = Field(min_length=1, max_length=10)
    experiment: str = Field(min_length=1, max_length=450)
    metric: str = Field(min_length=1, max_length=140)


class ExcludedReview(StrictModel):
    review_id: str
    reason: Literal["off_topic", "uninterpretable"]


class ReportDraft(StrictModel):
    summary: str = Field(min_length=1, max_length=900)
    themes: list[Theme] = Field(max_length=10)
    actions: list[Action] = Field(max_length=5)
    marketing_hypotheses: list[Hypothesis] = Field(max_length=3)
    excluded_reviews: list[ExcludedReview] = Field(max_length=200)


class ReportBundle(StrictModel):
    collection: CollectionResult
    analyzed_reviews: list[Review]
    model_name: str
    analysis_started_at: datetime
    analysis_seconds: float
    draft: ReportDraft
    warnings: list[str]


class AnalysisPreview(StrictModel):
    """Actual intermediate data; a draft is not evidence-checked until verified."""
    stage: Literal["analysis", "checking", "repair", "verified"]
    selected_reviews: list[Review]
    draft: ReportDraft | None = None
    issue: str | None = None


class UrlError(Exception):
    """The submitted link is not a supported Yandex Maps organization URL."""


class CollectionError(Exception):
    """Public review collection failed before any usable result was available."""


class ModelAccessError(Exception):
    """Codex is not available through the signed-in ChatGPT session."""


class ModelTimeoutError(Exception):
    """A child process exceeded its configured deadline."""


class ModelResponseError(Exception):
    """The model process returned an unusable result."""


class InvalidModelOutputError(ModelResponseError):
    """A successful model process returned missing or invalid JSON."""


class EvidenceValidationError(Exception):
    """The draft contains unsupported evidence or broken references."""
