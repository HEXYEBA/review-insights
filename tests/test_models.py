import pytest
from pydantic import ValidationError

from review_insights.models import Review


def test_review_allows_nullable_rating_and_date_but_requires_other_fields():
    item = Review(
        review_id="abc",
        text="Отлично",
        rating=None,
        published_at=None,
        date_label="",
        source_url="https://yandex.ru/maps/org/a/123/reviews/",
    )
    assert item.rating is None
    assert item.published_at is None


@pytest.mark.parametrize("rating", [0, 6, -1])
def test_review_rejects_rating_outside_one_through_five(rating):
    with pytest.raises(ValidationError):
        Review(
            review_id="abc",
            text="Отлично",
            rating=rating,
            published_at=None,
            date_label="",
            source_url="https://yandex.ru/maps/org/a/123/reviews/",
        )


def test_review_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Review(
            review_id="abc",
            text="Отлично",
            rating=None,
            published_at=None,
            date_label="",
            source_url="https://yandex.ru/maps/org/a/123/reviews/",
            author="не должно сохраняться",
        )
