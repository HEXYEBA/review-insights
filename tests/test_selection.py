from datetime import datetime, timezone

from review_insights.models import Review
from review_insights.selection import (
    deduplicate_reviews,
    make_review_id,
    normalize_text,
    select_analysis_reviews,
)


def review(review_id, text, day=None):
    published_at = (
        datetime(2026, 10, day, tzinfo=timezone.utc) if day is not None else None
    )
    return Review(
        review_id=review_id,
        text=text,
        rating=5,
        published_at=published_at,
        date_label="дата неизвестна" if day is None else f"{day} октября",
        source_url="https://yandex.ru/maps/org/a/123/reviews/",
    )


def test_normalize_text_only_normalizes_unicode_and_whitespace():
    assert normalize_text("  Cafe\u0301\n  Ёж!  ") == "Café Ёж!"


def test_review_id_is_stable_and_short():
    first = make_review_id("123", "  вкусно\n и быстро ", "2026-10-01", 5)
    second = make_review_id("123", "вкусно и быстро", "2026-10-01", 5)
    assert first == second
    assert len(first) == 16
    assert all(ch in "0123456789abcdef" for ch in first)


def test_deduplication_keeps_first_review_with_each_id():
    first = review("same", "первая версия", 1)
    duplicate = review("same", "вторая версия", 2)
    assert deduplicate_reviews([first, duplicate]) == [first]


def test_same_text_at_different_dates_has_different_review_ids():
    old_id = make_review_id("123", "один текст", "2026-10-01", 5)
    new_id = make_review_id("123", "один текст", "2026-10-02", 5)
    assert old_id != new_id
    assert len(deduplicate_reviews([review(old_id, "один текст", 1), review(new_id, "один текст", 2)])) == 2


def test_selection_orders_dates_and_preserves_ties_then_unknown_dates():
    first_equal = review("equal-1", "первый", 3)
    second_equal = review("equal-2", "второй", 3)
    result = select_analysis_reviews(
        [review("old", "старый", 1), first_equal, review("unknown", "без даты"), second_equal]
    )
    assert [item.review_id for item in result] == [
        "equal-1",
        "equal-2",
        "old",
        "unknown",
    ]


def test_selection_caps_at_200_reviews():
    reviews = [review(f"id-{i}", f"текст {i}", (i % 28) + 1) for i in range(205)]
    assert len(select_analysis_reviews(reviews)) == 200


def test_budget_keeps_whole_reviews_in_date_order_and_skips_oversized_item():
    selected = select_analysis_reviews(
        [review("old", "12345", 1), review("new", "абв", 3), review("middle", "xy", 2)],
        char_budget=5,
    )
    assert [item.review_id for item in selected] == ["new", "middle"]
    assert [item.text for item in selected] == ["абв", "xy"]
