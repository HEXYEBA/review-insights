import pytest

from review_insights.models import (
    Action,
    Evidence,
    EvidenceValidationError,
    ExcludedReview,
    Hypothesis,
    Review,
    Theme,
)
from review_insights.reporting import (
    render_markdown,
    theme_support_count,
    validate_evidence,
)


def test_valid_evidence_is_accepted(valid_draft, selected_reviews):
    validate_evidence(valid_draft, selected_reviews)
    assert theme_support_count(valid_draft.themes[0]) == 1


def test_unknown_review_id_is_rejected(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    bad.themes[0].supporting_review_ids = ["invented"]
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)


def test_fabricated_quote_is_rejected_without_rewriting_draft(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    bad.themes[0].evidence[0].quote = "экономит пять часов в неделю"
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)
    assert bad.themes[0].evidence[0].quote == "экономит пять часов в неделю"


def test_duplicate_theme_id_is_rejected(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    duplicate = bad.themes[0].model_copy(deep=True)
    bad.themes.append(duplicate)
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)


def test_malformed_theme_id_is_rejected(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    bad.themes[0].theme_id = "THEME-A"
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)


def test_duplicate_supporting_review_id_is_rejected(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    bad.themes[0].supporting_review_ids = ["r1", "r1"]
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)


def test_unknown_theme_in_action_is_rejected(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    bad.actions = [
        Action(title="Проверить очередь", source_theme_ids=["T02"], next_step="Сверить время.")
    ]
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)


def test_evidence_from_non_supporting_review_is_rejected(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    bad.themes[0].evidence = [Evidence(review_id="r2", quote="Цена высокая")]
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)


def test_excluded_review_cannot_support_a_theme(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    bad.excluded_reviews = [ExcludedReview(review_id="r1", reason="off_topic")]
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)


def test_duplicate_excluded_review_is_rejected(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    bad.excluded_reviews = [
        ExcludedReview(review_id="r2", reason="off_topic"),
        ExcludedReview(review_id="r2", reason="uninterpretable"),
    ]
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)


def test_selected_review_ids_must_be_unique(valid_draft, selected_reviews):
    with pytest.raises(EvidenceValidationError):
        validate_evidence(valid_draft, [*selected_reviews, selected_reviews[0]])


def test_every_selected_review_must_be_thematic_or_explicitly_excluded(
    valid_draft, selected_reviews
):
    unclassified = valid_draft.model_copy(deep=True)
    unclassified.excluded_reviews = []
    with pytest.raises(EvidenceValidationError, match="учтены"):
        validate_evidence(unclassified, selected_reviews)


def test_quote_matching_is_case_sensitive(valid_draft, selected_reviews):
    bad = valid_draft.model_copy(deep=True)
    bad.themes[0].evidence[0].quote = "Заказ ждал сорок минут"
    with pytest.raises(EvidenceValidationError):
        validate_evidence(bad, selected_reviews)


def test_markdown_escapes_quote_markup_and_links_checked_org_page(valid_bundle):
    bundle = valid_bundle.model_copy(deep=True)
    review = bundle.analyzed_reviews[0].model_copy(
        update={
            "text": "Заказ [ждал] <script> сорок минут.",
            "source_url": "https://yandex.com/maps/org/slug/123/reviews/?page=2",
        }
    )
    bundle.analyzed_reviews[0] = review
    bundle.collection = bundle.collection.model_copy(
        update={"reviews": [review, bundle.analyzed_reviews[1]]}
    )
    bundle.draft.themes[0].supporting_review_ids = [review.review_id]
    bundle.draft.themes[0].evidence = [
        Evidence(review_id=review.review_id, quote="Заказ [ждал] <script> сорок минут.")
    ]

    markdown = render_markdown(bundle)

    assert "> Заказ \\[ждал\\] &lt;script&gt; сорок минут." in markdown
    assert "[Страница источника](<https://yandex.ru/maps/org/slug/123/reviews/>)" in markdown
    assert "<script>" not in markdown


def test_markdown_separates_network_scope_from_current_place_problems(valid_bundle):
    bundle = valid_bundle.model_copy(deep=True)
    extra = Review(
        review_id="r3",
        text="Вежливые сотрудники.",
        rating=5,
        published_at=bundle.analyzed_reviews[1].published_at,
        date_label="2 октября",
        source_url=bundle.collection.org.url,
    )
    bundle.collection = bundle.collection.model_copy(
        update={
            "reviews": [*bundle.collection.reviews, extra],
            "source_review_count": 3,
        }
    )
    bundle.analyzed_reviews.append(extra)
    bundle.draft.themes.extend(
        [
            Theme(
                theme_id="T02",
                kind="problem",
                scope="network_or_other",
                title="Жалоба о доставке сети",
                explanation="Отзыв упоминает сервис вне этого места.",
                supporting_review_ids=["r1"],
                evidence=[Evidence(review_id="r1", quote="Кофе вкусный")],
            ),
            Theme(
                theme_id="T03",
                kind="strength",
                scope="place",
                title="Вежливый персонал",
                explanation="Отзыв отмечает вежливость.",
                supporting_review_ids=["r3"],
                evidence=[Evidence(review_id="r3", quote="Вежливые сотрудники")],
            ),
        ]
    )
    bundle.draft.actions = [
        Action(title="Проверить ожидание", source_theme_ids=["T01"], next_step="Замерить время.")
    ]
    bundle.draft.marketing_hypotheses = [
        Hypothesis(
            hypothesis="Проверить спрос на наборы",
            source_theme_ids=["T03"],
            experiment="Показать набор части посетителей.",
            metric="Доля покупок набора",
        )
    ]
    bundle.draft.excluded_reviews = [
        ExcludedReview(review_id="r2", reason="off_topic")
    ]

    markdown = render_markdown(bundle)
    problem_section = markdown.split("## Проблемы этого места", 1)[1].split("\n## ", 1)[0]
    network_section = markdown.split("## О сети и других местах", 1)[1].split("\n## ", 1)[0]

    assert "Долгое ожидание" in problem_section
    assert "Жалоба о доставке сети" not in problem_section
    assert "Жалоба о доставке сети" in network_section
    assert "Подтверждающих отзывов: 1" in markdown
    assert "Действия руководителя" in markdown
    assert "Маркетинговые гипотезы" in markdown
    assert "r2 — off_topic" in markdown
    assert "не означает, что остальные отзывы были качественными" in markdown


def test_markdown_reports_sample_period_and_counts(valid_bundle):
    markdown = render_markdown(valid_bundle)
    assert "Собрано 2; проанализировано 2" in markdown
    assert "2 текстовых отзывов / 10 оценок" in markdown
    assert "Период анализируемой выборки: 01.10.2026 — 02.10.2026" in markdown
    assert "не мнение всех клиентов" in markdown


def test_markdown_rejects_evidence_source_from_another_org(valid_bundle):
    bundle = valid_bundle.model_copy(deep=True)
    wrong_org_review = bundle.analyzed_reviews[0].model_copy(
        update={"source_url": "https://yandex.ru/maps/org/other/999/reviews/"}
    )
    bundle.analyzed_reviews[0] = wrong_org_review
    bundle.collection.reviews[0] = wrong_org_review

    with pytest.raises(EvidenceValidationError):
        render_markdown(bundle)


def test_markdown_escapes_model_text_and_flattens_newlines(valid_bundle):
    bundle = valid_bundle.model_copy(deep=True)
    bundle.draft.summary = "<img src=x>\n## Поддельный раздел"
    bundle.draft.themes[0].title = "[Открыть](https://example.com)"

    markdown = render_markdown(bundle)

    assert "<img src=x>" not in markdown
    assert "## Поддельный раздел" not in markdown
    assert "&lt;img src=x&gt; \\#\\# Поддельный раздел" in markdown
    assert "\\[Открыть\\]" in markdown
