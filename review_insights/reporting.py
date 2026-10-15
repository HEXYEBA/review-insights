"""Validate model evidence against the exact reviews supplied for analysis."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from review_insights.models import (
    EvidenceValidationError,
    ReportBundle,
    ReportDraft,
    Theme,
    Review,
    UrlError,
)
from review_insights.selection import normalize_text
from review_insights.urls import normalize_org_url


_THEME_ID = re.compile(r"^T(?:0[1-9]|10)$")


def _reject(message: str) -> None:
    raise EvidenceValidationError(message)


def _validate_theme_references(references: list[str], theme_ids: set[str], label: str) -> None:
    if len(references) != len(set(references)):
        _reject(f"{label}: повторяются ссылки на темы.")
    unknown = set(references) - theme_ids
    if unknown:
        _reject(f"{label}: есть ссылка на неизвестную тему.")


def validate_evidence(draft: ReportDraft, selected: list[Review]) -> None:
    """Reject any duplicate, unknown, or textually unsupported report reference."""
    selected_by_id: dict[str, Review] = {}
    for review in selected:
        if review.review_id in selected_by_id:
            _reject("Во входном наборе повторяется ID отзыва.")
        selected_by_id[review.review_id] = review

    theme_by_id: dict[str, Theme] = {}
    all_support_ids: set[str] = set()
    for theme in draft.themes:
        if not _THEME_ID.fullmatch(theme.theme_id):
            _reject("ID темы должен иметь формат T01–T10.")
        if theme.theme_id in theme_by_id:
            _reject("В отчёте повторяется ID темы.")
        theme_by_id[theme.theme_id] = theme

        support_ids = theme.supporting_review_ids
        if len(support_ids) != len(set(support_ids)):
            _reject("В теме повторяется ID подтверждающего отзыва.")
        unknown_support = set(support_ids) - set(selected_by_id)
        if unknown_support:
            _reject("Тема ссылается на отзыв вне выбранного набора.")
        all_support_ids.update(support_ids)

        for evidence in theme.evidence:
            if evidence.review_id not in support_ids:
                _reject("Цитата ссылается на отзыв, не указанный среди подтверждений темы.")
            source = selected_by_id[evidence.review_id]
            quote = normalize_text(evidence.quote)
            source_text = normalize_text(source.text)
            if not quote or quote not in source_text:
                _reject("Цитата не является точной подстрокой исходного отзыва.")

    theme_ids = set(theme_by_id)
    for action in draft.actions:
        _validate_theme_references(action.source_theme_ids, theme_ids, "Действие")
    for hypothesis in draft.marketing_hypotheses:
        _validate_theme_references(
            hypothesis.source_theme_ids, theme_ids, "Маркетинговая гипотеза"
        )

    excluded_ids: set[str] = set()
    for excluded in draft.excluded_reviews:
        if excluded.review_id in excluded_ids:
            _reject("В отчёте повторяется исключённый ID отзыва.")
        excluded_ids.add(excluded.review_id)
        if excluded.review_id not in selected_by_id:
            _reject("Исключённый отзыв отсутствует в выбранном наборе.")
    if excluded_ids & all_support_ids:
        _reject("Исключённый отзыв одновременно используется как подтверждение темы.")
    unaccounted_ids = set(selected_by_id) - all_support_ids - excluded_ids
    if unaccounted_ids:
        _reject(
            "Не все выбранные отзывы учтены: добавь их в подтверждения тем или явно исключи."
        )


def theme_support_count(theme: Theme) -> int:
    """Count unique reviewed IDs; never infer a count from narrative text."""
    return len(set(theme.supporting_review_ids))


_MOSCOW = ZoneInfo("Europe/Moscow")


def _safe_markdown(value: str) -> str:
    """Flatten untrusted text and neutralize Markdown/HTML control characters."""
    flattened = normalize_text(value)
    flattened = flattened.replace("\\", "\\\\")
    for character in ("`", "*", "_", "[", "]", "|", "~", "#"):
        flattened = flattened.replace(character, f"\\{character}")
    return flattened.replace("<", "&lt;").replace(">", "&gt;")


def _display_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_MOSCOW).strftime("%d.%m.%Y %H:%M МСК")


def _checked_source_url(review: Review, org_id: str) -> str:
    try:
        source = normalize_org_url(review.source_url)
    except UrlError as exc:
        raise EvidenceValidationError(
            "У подтверждающего отзыва некорректная ссылка на источник."
        ) from exc
    if source.org_id != org_id:
        raise EvidenceValidationError(
            "У подтверждающего отзыва ссылка ведёт на другую организацию."
        )
    return source.url


def _render_themes(
    themes: list[Theme], reviews_by_id: dict[str, Review], org_id: str
) -> list[str]:
    lines: list[str] = []
    if not themes:
        return ["Нет подтверждённых тем."]

    for theme in themes:
        lines.extend(
            [
                f"### {theme.theme_id} — {_safe_markdown(theme.title)}",
                "",
                _safe_markdown(theme.explanation),
                "",
                f"Подтверждающих отзывов: {theme_support_count(theme)}",
                "ID отзывов: " + ", ".join(f"`{review_id}`" for review_id in theme.supporting_review_ids),
                "",
                "Цитаты:",
            ]
        )
        for evidence in theme.evidence:
            review = reviews_by_id[evidence.review_id]
            quote = _safe_markdown(evidence.quote)
            source_url = _checked_source_url(review, org_id)
            lines.extend(
                [
                    "",
                    f"> {quote}",
                    "",
                    f"[Страница источника](<{source_url}>)",
                ]
            )
    return lines


def render_markdown(bundle: ReportBundle) -> str:
    """Render a Russian report with checked source links and escaped model text."""
    validate_evidence(bundle.draft, bundle.analyzed_reviews)
    try:
        organization = normalize_org_url(bundle.collection.org.url)
    except UrlError as exc:
        raise EvidenceValidationError("В отчёте указана некорректная ссылка организации.") from exc
    if organization.org_id != bundle.collection.org.org_id:
        raise EvidenceValidationError("Ссылка отчёта ведёт на другую организацию.")

    reviews_by_id = {review.review_id: review for review in bundle.analyzed_reviews}
    for review in bundle.analyzed_reviews:
        _checked_source_url(review, organization.org_id)

    place_strengths = [
        theme
        for theme in bundle.draft.themes
        if theme.kind == "strength" and theme.scope == "place"
    ]
    place_problems = [
        theme
        for theme in bundle.draft.themes
        if theme.kind == "problem" and theme.scope == "place"
    ]
    network_themes = [
        theme for theme in bundle.draft.themes if theme.scope == "network_or_other"
    ]
    unclear_themes = [
        theme for theme in bundle.draft.themes if theme.scope == "unclear"
    ]

    known_dates = [
        _as_moscow(review.published_at).date()
        for review in bundle.analyzed_reviews
        if review.published_at is not None
    ]
    period = (
        f"{min(known_dates).strftime('%d.%m.%Y')} — {max(known_dates).strftime('%d.%m.%Y')}"
        if known_dates
        else "неизвестен"
    )
    collection = bundle.collection
    lines = [
        f"# {_safe_markdown(collection.org_name)}",
        "",
        "## Данные сбора",
        "",
        f"- Страница организации: [Открыть в Яндекс Картах](<{organization.url}>)",
        f"- Собрано: {_display_datetime(collection.collected_at)}",
        f"- Анализ начат: {_display_datetime(bundle.analysis_started_at)}",
        f"- Модель: `{_safe_markdown(bundle.model_name)}`",
        f"- Собрано {len(collection.reviews)}; проанализировано {len(bundle.analyzed_reviews)}",
        f"- Период анализируемой выборки: {period}",
        f"- Причина остановки сбора: `{collection.stop_reason}`; сортировка: `{collection.sort_mode}`",
    ]
    source_counts: list[str] = []
    if collection.source_review_count is not None:
        source_counts.append(f"{collection.source_review_count} текстовых отзывов")
    if collection.source_rating_count is not None:
        source_counts.append(f"{collection.source_rating_count} оценок")
    if source_counts:
        lines.append("- На карточке указано: " + " / ".join(source_counts))
    if collection.source_rating is not None:
        lines.append(f"- Средняя оценка карточки: {collection.source_rating:g} из 5")
    lines.extend(
        [
            f"- Время анализа моделью: {bundle.analysis_seconds:.1f} с",
            "",
            "## Предупреждения",
            "",
        ]
    )
    if bundle.warnings:
        lines.extend(f"- {_safe_markdown(warning)}" for warning in bundle.warnings)
    else:
        lines.append("Предупреждений нет.")

    lines.extend(
        [
            "",
            "## Вывод",
            "",
            _safe_markdown(bundle.draft.summary),
            "",
            "## Сильные стороны этого места",
            "",
            *_render_themes(place_strengths, reviews_by_id, organization.org_id),
            "",
            "## Проблемы этого места",
            "",
            *_render_themes(place_problems, reviews_by_id, organization.org_id),
            "",
            "## О сети и других местах",
            "",
        ]
    )
    if network_themes or unclear_themes:
        if network_themes:
            lines.extend(
                [
                    "### Сеть или другое место",
                    "",
                    *_render_themes(network_themes, reviews_by_id, organization.org_id),
                    "",
                ]
            )
        if unclear_themes:
            lines.extend(
                [
                    "### Привязка к месту неясна",
                    "",
                    *_render_themes(unclear_themes, reviews_by_id, organization.org_id),
                ]
            )
    else:
        lines.append("Нет тем о сети, других местах или с неясной привязкой.")

    lines.extend(["", "## Действия руководителя", ""])
    if bundle.draft.actions:
        for action in bundle.draft.actions:
            references = ", ".join(f"`{theme_id}`" for theme_id in action.source_theme_ids)
            lines.extend(
                [
                    f"- **{_safe_markdown(action.title)}** — {_safe_markdown(action.next_step)}",
                    f"  - Основано на темах: {references}",
                ]
            )
    else:
        lines.append("Действия не предложены.")

    lines.extend(["", "## Маркетинговые гипотезы", ""])
    if bundle.draft.marketing_hypotheses:
        for hypothesis in bundle.draft.marketing_hypotheses:
            references = ", ".join(
                f"`{theme_id}`" for theme_id in hypothesis.source_theme_ids
            )
            lines.extend(
                [
                    f"- **Гипотеза:** {_safe_markdown(hypothesis.hypothesis)}",
                    f"  - Эксперимент: {_safe_markdown(hypothesis.experiment)}",
                    f"  - Метрика: {_safe_markdown(hypothesis.metric)}",
                    f"  - Основана на темах: {references}",
                ]
            )
    else:
        lines.append("Гипотезы не предложены.")

    lines.extend(["", "## Исключённые отзывы", ""])
    if bundle.draft.excluded_reviews:
        lines.extend(
            f"- {item.review_id} — {item.reason}"
            for item in bundle.draft.excluded_reviews
        )
        lines.extend(
            [
                "",
                "Отсутствие отзыва в этом списке не означает, что остальные отзывы были качественными.",
            ]
        )
    else:
        lines.append(
            "Модель не отметила отзывы как неинтерпретируемые или не по теме; "
            "это не означает, что остальные отзывы были качественными."
        )

    lines.extend(
        [
            "",
            "## Ограничения",
            "",
            "Отчёт отражает собранные отзывы, а не мнение всех клиентов или внутренние показатели организации.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _as_moscow(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_MOSCOW)
