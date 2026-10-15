import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from review_insights.models import (
    Action,
    CollectionResult,
    Evidence,
    ExcludedReview,
    Hypothesis,
    OrgRef,
    ReportBundle,
    ReportDraft,
    Review,
    Theme,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def isolate_test_diagnostics(monkeypatch, tmp_path):
    """Synthetic failures must not appear in the owner's actual error journal."""
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "diagnostics"))


@pytest.fixture(scope="session")
def browser():
    # Keep browser binaries inside this project's virtual environment even when
    # the parent shell has a PLAYWRIGHT_BROWSERS_PATH for another project.
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def pw_page(browser):
    page = browser.new_page(
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        viewport={"width": 1440, "height": 1000},
    )
    yield page
    page.close()


@pytest.fixture
def selected_reviews():
    return [
        Review(
            review_id="r1",
            text="Кофе вкусный, но заказ ждал сорок минут.",
            rating=3,
            published_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            date_label="1 октября",
            source_url="https://yandex.ru/maps/org/a/123/reviews/",
        ),
        Review(
            review_id="r2",
            text="Сотрудники вежливые. Цена высокая.",
            rating=4,
            published_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
            date_label="2 октября",
            source_url="https://yandex.ru/maps/org/a/123/reviews/",
        ),
    ]


@pytest.fixture
def collection(selected_reviews):
    return CollectionResult(
        org=OrgRef(org_id="123", url="https://yandex.ru/maps/org/a/123/reviews/"),
        org_name="Тестовое кафе",
        collected_at=datetime.now(timezone.utc),
        source_review_count=2,
        source_rating_count=10,
        source_rating=4.2,
        reviews=selected_reviews,
        stop_reason="exhausted",
        sort_mode="newest",
        warnings=[],
    )


@pytest.fixture
def empty_collection(collection):
    return collection.model_copy(update={"reviews": [], "source_review_count": 0})


@pytest.fixture
def valid_draft():
    return ReportDraft.model_validate(
        {
            "summary": "В одном отзыве отмечено долгое ожидание заказа.",
            "themes": [
                {
                    "theme_id": "T01",
                    "kind": "problem",
                    "scope": "place",
                    "title": "Долгое ожидание",
                    "explanation": "Автор отзыва сообщает о долгом ожидании заказа.",
                    "supporting_review_ids": ["r1"],
                    "evidence": [
                        {"review_id": "r1", "quote": "заказ ждал сорок минут"}
                    ],
                }
            ],
            "actions": [],
            "marketing_hypotheses": [],
            "excluded_reviews": [
                {"review_id": "r2", "reason": "uninterpretable"}
            ],
        }
    )


@pytest.fixture
def valid_bundle(collection, selected_reviews, valid_draft):
    return ReportBundle(
        collection=collection,
        analyzed_reviews=selected_reviews,
        model_name="gpt-6.1-sol",
        analysis_started_at=datetime.now(timezone.utc),
        analysis_seconds=0.1,
        draft=valid_draft,
        warnings=["Малая выборка"],
    )
