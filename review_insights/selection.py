"""Stable review normalization, de-duplication, and input-budget selection."""

import hashlib
import unicodedata
from datetime import datetime, timezone

from review_insights.config import MAX_INPUT_CHARS, MAX_REVIEWS
from review_insights.models import Review


def normalize_text(value: str) -> str:
    """Normalize Unicode composition and collapse whitespace without rewriting text."""
    return " ".join(unicodedata.normalize("NFC", value).split())


def make_review_id(
    org_id: str, text: str, published_at: str, rating: int | None
) -> str:
    """Build a stable short identifier from the organization and review content."""
    date_component = str(published_at)
    rating_component = "" if rating is None else str(rating)
    payload = "\n".join(
        (str(org_id), date_component, rating_component, normalize_text(text))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def deduplicate_reviews(reviews: list[Review]) -> list[Review]:
    """Keep the first occurrence of each review ID, preserving input order."""
    unique: list[Review] = []
    seen: set[str] = set()
    for review in reviews:
        if review.review_id in seen:
            continue
        seen.add(review.review_id)
        unique.append(review)
    return unique


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def select_analysis_reviews(
    reviews: list[Review], char_budget: int = MAX_INPUT_CHARS
) -> list[Review]:
    """Select whole, unique reviews in descending date order under fixed limits."""
    if char_budget < 0:
        raise ValueError("char_budget must be non-negative")

    unique = deduplicate_reviews(reviews)
    known = [review for review in unique if review.published_at is not None]
    unknown = [review for review in unique if review.published_at is None]
    known.sort(key=lambda review: _utc_datetime(review.published_at), reverse=True)
    ordered = known + unknown

    selected: list[Review] = []
    remaining_chars = char_budget
    for review in ordered:
        if len(selected) >= MAX_REVIEWS:
            break
        text_length = len(review.text)
        if text_length > remaining_chars:
            continue
        selected.append(review)
        remaining_chars -= text_length
    return selected
