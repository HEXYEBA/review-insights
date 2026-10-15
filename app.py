"""Minimal Streamlit interface for the Yandex Maps review MVP."""

import logging
import os
import re
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

from review_insights import service
from review_insights.config import LLM_TIMEOUT_S
from review_insights.clipboard_input import apply_clipboard_event, clipboard_controls
from review_insights.models import (
    AnalysisPreview,
    CollectionError,
    EvidenceValidationError,
    ModelAccessError,
    ModelResponseError,
    ModelTimeoutError,
    UrlError,
)
from review_insights.reporting import render_markdown
from review_insights.urls import normalize_org_url
from review_insights.ui import APP_CSS, BRAND_HTML, HERO_ART, ProcessView, render_analysis_preview, render_preview, render_process, text_html


KOFEMANIYA_URL = "https://yandex.ru/maps/org/kofemaniya/1242233919/reviews/"
MIEM_URL = "https://yandex.ru/maps/org/niu_vshe_moskovskiy_institut_elektroniki_i_matematiki_imeni_a_n_tikhonova/1544682531/reviews/"
_MOSCOW = ZoneInfo("Europe/Moscow")


@st.cache_resource
def _pipeline_lock() -> threading.Lock:
    return threading.Lock()


def _error_message(error: Exception) -> str:
    if isinstance(error, UrlError):
        return str(error)
    if isinstance(error, CollectionError):
        return str(error)
    if isinstance(error, ModelAccessError):
        return str(error)
    if isinstance(error, ModelTimeoutError):
        return (
            f"Модель не завершила анализ за {LLM_TIMEOUT_S} секунд. "
            "Готовый отчёт не получен. Если отчёт уже сохранялся, "
            "нажмите «Открыть сохранённый отчёт»."
        )
    if isinstance(error, ModelResponseError):
        return str(error)
    if isinstance(error, EvidenceValidationError):
        return "Отчёт не прошёл проверку цитат или ссылок на отзывы."
    return "Не удалось завершить анализ. Диагностика сохранена локально."


def _save_safe_diagnostic(error: Exception) -> bool:
    """Persist frame locations and exception type without message or locals."""
    artifacts = Path(os.environ.get("ARTIFACTS_DIR", "artifacts"))
    if not artifacts.is_absolute():
        artifacts = Path(__file__).resolve().parent / artifacts
    try:
        artifacts.mkdir(parents=True, exist_ok=True)
        frames = traceback.extract_tb(error.__traceback__)
        frame_text = "".join(
            f"  {Path(frame.filename).name}:{frame.lineno} in {frame.name}\n"
            for frame in frames
        )
        log_path = artifacts / "app-errors.log"
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                f"{datetime.now(timezone.utc).isoformat()} {type(error).__name__}\n"
                f"{frame_text}\n"
            )
        return True
    except OSError:
        logging.error("Could not save app diagnostic (%s)", type(error).__name__)
        return False


def _state_defaults() -> None:
    defaults = {
        "input_url": "",
        "input_revision": 0,
        "report_bundle": None,
        "report_markdown": None,
        "report_is_saved": False,
        "error_message": None,
        "process_view": None,
        "input_feedback": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _render_theme_group(title: str, themes, reviews_by_id) -> None:
    st.subheader(title)
    if not themes:
        st.caption("Нет подтверждённых тем.")
        return

    for theme in themes:
        safe_title = re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", theme.title)
        label = f"{safe_title} · отзывов: {len(set(theme.supporting_review_ids))}"
        with st.expander(label):
            st.markdown(text_html(theme.explanation), unsafe_allow_html=True)
            st.caption("Подтверждающие отзывы: " + ", ".join(theme.supporting_review_ids))
            for evidence in theme.evidence:
                st.markdown(text_html(evidence.quote, css_class="ri-quote"), unsafe_allow_html=True)
                review = reviews_by_id[evidence.review_id]
                source = normalize_org_url(review.source_url)
                st.markdown(f"[Страница источника](<{source.url}>)")


def _render_report(bundle, markdown: str, *, saved: bool = False) -> None:
    collection = bundle.collection
    st.subheader("📋 Результат анализа")
    if saved:
        collected_at = collection.collected_at
        if collected_at.tzinfo is None or collected_at.utcoffset() is None:
            collected_at = collected_at.replace(tzinfo=timezone.utc)
        saved_date = collected_at.astimezone(_MOSCOW).strftime("%d.%m.%Y %H:%M МСК")
        st.info(f"Сохранённый отчёт. Отзывы собраны {saved_date}")
    st.markdown(text_html(collection.org_name, css_class="ri-action"), unsafe_allow_html=True)
    checked_org = normalize_org_url(collection.org.url)
    st.link_button("Открыть карточку в Яндекс Картах", checked_org.url)

    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("💬 Собрано отзывов", len(collection.reviews))
    metric2.metric("🧠 В анализе", len(bundle.analyzed_reviews))
    metric3.metric("⭐ Оценка на Картах", f"{collection.source_rating:g} / 5" if collection.source_rating is not None else "Нет данных")
    stats = []
    if collection.source_review_count is not None:
        stats.append(f"на карточке {collection.source_review_count} текстовых отзывов")
    if collection.source_rating_count is not None:
        stats.append(f"{collection.source_rating_count} оценок")
    st.caption("; ".join(stats))
    st.caption(f"Модель: {bundle.model_name}. Время анализа: {bundle.analysis_seconds:.0f} с.")

    known_dates = [review.published_at for review in bundle.analyzed_reviews if review.published_at]
    if known_dates:
        dates = [
            (
                value.replace(tzinfo=timezone.utc)
                if value.tzinfo is None or value.utcoffset() is None
                else value
            )
            .astimezone(_MOSCOW)
            .date()
            for value in known_dates
        ]
        period = f"{min(dates):%d.%m.%Y} — {max(dates):%d.%m.%Y}"
    else:
        period = "неизвестен"
    st.caption(f"Период анализируемой выборки: {period}")
    if collection.stop_reason in {"deadline", "blocked", "rate_limited", "load_error", "layout_changed"}:
        st.warning("Собрана частичная выборка: загрузка остановилась до получения всех доступных отзывов. Подробности — ниже.")
    if bundle.warnings:
        with st.expander(f"ℹ️ Что учитывать при чтении · {len(bundle.warnings)}"):
            for warning in bundle.warnings:
                st.warning(warning)

    with st.expander("🔍 Что означает сверка отчёта?"):
        quotes = sum(len(theme.evidence) for theme in bundle.draft.themes)
        st.markdown("Сверяем цитаты, которые ИИ включил в отчёт, с исходными текстами отзывов. Каждая выдержка должна встречаться в соответствующем отзыве. Также проверяем ссылки на темы и то, что выбранные отзывы учтены или явно исключены.")
        st.caption(f"Сверка пройдена: {quotes} цитат, {len(bundle.draft.themes)} тем, {len(bundle.analyzed_reviews)} отзывов. Это проверка опоры отчёта на источники, а не правдивости самих отзывов.")

    st.subheader("💡 Главное из отзывов")
    st.markdown(text_html(bundle.draft.summary, css_class="ri-copy ri-summary"), unsafe_allow_html=True)
    reviews_by_id = {review.review_id: review for review in bundle.analyzed_reviews}
    themes = bundle.draft.themes
    _render_theme_group(
        "👍 За что хвалят это место",
        [theme for theme in themes if theme.kind == "strength" and theme.scope == "place"],
        reviews_by_id,
    )
    _render_theme_group(
        "⚠️ Что стоит исправить",
        [theme for theme in themes if theme.kind == "problem" and theme.scope == "place"],
        reviews_by_id,
    )
    _render_theme_group(
        "🏢 О сети и других местах",
        [theme for theme in themes if theme.scope == "network_or_other"],
        reviews_by_id,
    )
    _render_theme_group(
        "❔ Привязка к месту неясна",
        [theme for theme in themes if theme.scope == "unclear"],
        reviews_by_id,
    )

    st.subheader("🎯 Что делать руководителю")
    if bundle.draft.actions:
        for index, action in enumerate(bundle.draft.actions, start=1):
            st.markdown(text_html(f"{index}. {action.title}", css_class="ri-action"), unsafe_allow_html=True)
            st.markdown(text_html(action.next_step), unsafe_allow_html=True)
            st.caption("Темы: " + ", ".join(action.source_theme_ids))
    else:
        st.caption("Действия не предложены.")

    st.subheader("🧪 Что проверить экспериментом")
    if bundle.draft.marketing_hypotheses:
        for hypothesis in bundle.draft.marketing_hypotheses:
            st.markdown(text_html(hypothesis.hypothesis, css_class="ri-action"), unsafe_allow_html=True)
            st.markdown(text_html(f"Как проверить: {hypothesis.experiment}"), unsafe_allow_html=True)
            st.markdown(text_html(f"Что измерять: {hypothesis.metric}"), unsafe_allow_html=True)
            st.caption("Темы: " + ", ".join(hypothesis.source_theme_ids))
    else:
        st.caption("Гипотезы не предложены.")

    with st.expander(f"🗂️ Исключённые отзывы · {len(bundle.draft.excluded_reviews)}"):
        for excluded in bundle.draft.excluded_reviews:
            reason = {"off_topic": "не по теме", "uninterpretable": "смысл неясен"}[excluded.reason]
            st.text(f"{excluded.review_id} — {reason}")
        st.caption("Отсутствие отзыва в этом списке не означает, что остальные отзывы были качественными.")

    st.caption(
        "Отчёт отражает собранные отзывы, а не мнение всех клиентов или внутренние показатели организации."
    )
    st.download_button(
        "Скачать отчёт",
        data=markdown.encode("utf-8"),
        file_name=f"report-{checked_org.org_id}.md",
        mime="text/markdown; charset=utf-8",
        key="download_report",
        on_click="ignore",
    )


def main() -> None:
    st.set_page_config(page_title="Что говорят об организации", page_icon="💬", layout="wide")
    _state_defaults()
    st.html(APP_CSS)

    st.html(BRAND_HTML)
    with st.container(key="hero"):
        hero_text, hero_art = st.columns([1.45, 1], gap="large", vertical_alignment="center")
        with hero_text:
            st.title("Что говорят об организации")
            st.markdown("Услышьте клиентов. Найдите сильные стороны, точки роста и конкретные действия — с цитатами из отзывов.")
        with hero_art:
            st.html(HERO_ART)

    st.caption("Попробуйте на примере или вставьте полную ссылку на Яндекс Карты.")

    with st.container(key="examples"):
        example_col1, example_col2 = st.columns(2)
        with example_col1:
            if st.button("Кофемания", key="example_kofemaniya"):
                st.session_state.input_url = KOFEMANIYA_URL
                st.session_state.input_revision += 1
                st.session_state.report_bundle = None
                st.session_state.report_markdown = None
                st.session_state.report_is_saved = False
                st.session_state.error_message = None
                st.session_state.process_view = None
                st.session_state.input_feedback = None
        with example_col2:
            if st.button("МИЭМ ВШЭ", key="example_miem"):
                st.session_state.input_url = MIEM_URL
                st.session_state.input_revision += 1
                st.session_state.report_bundle = None
                st.session_state.report_markdown = None
                st.session_state.report_is_saved = False
                st.session_state.error_message = None
                st.session_state.process_view = None
                st.session_state.input_feedback = None

    clipboard_event = clipboard_controls(key="clipboard_controls", default=None)
    apply_clipboard_event(clipboard_event, st.session_state)

    with st.form("analysis_form"):
        submitted_url = st.text_input("Полная ссылка на Яндекс Карты", value=st.session_state.input_url,
                                     key=f"input_url_{st.session_state.input_revision}")
        button1, button2, button3 = st.columns([1, 1, 1.3])
        with button1:
            submitted = st.form_submit_button("Проанализировать", key="analyze_submit", type="primary", width="stretch")
        with button2:
            refresh_submitted = st.form_submit_button("Обновить с Яндекса", key="refresh_submit", width="stretch")
        with button3:
            open_saved_submitted = st.form_submit_button("Открыть сохранённый отчёт", key="open_saved_submit", width="stretch")
        st.caption("До 200 отзывов в сборе · до 50 в анализе. Свежий анализ — несколько минут. Сохранённый отчёт открывается сразу.")

    if submitted or refresh_submitted or open_saved_submitted:
        st.session_state.input_url = submitted_url

    if st.session_state.input_feedback and not (submitted or refresh_submitted or open_saved_submitted):
        st.warning(st.session_state.input_feedback)

    process_slot = st.empty()
    preview_slot = st.empty()
    report_slot = st.empty()

    def draw_process() -> None:
        view = st.session_state.process_view
        if view is not None:
            with process_slot.container():
                components.html(render_process(view), height=(290 if view.stage == 2 else 240) if not view.cached else 65)

    def update_progress(label: str) -> None:
        st.session_state.process_view.update(label)
        draw_process()

    def update_preview(collection) -> None:
        preview_slot.markdown(render_preview(collection), unsafe_allow_html=True)

    def update_insight(event: AnalysisPreview) -> None:
        preview_slot.markdown(render_analysis_preview(event), unsafe_allow_html=True)

    draw_process()

    if submitted or refresh_submitted:
        st.session_state.report_bundle = None
        st.session_state.report_markdown = None
        st.session_state.report_is_saved = False
        st.session_state.error_message = None
        st.session_state.process_view = None
        process_slot.empty()
        report_slot.empty()
        try:
            normalize_org_url(st.session_state.input_url)
        except UrlError as error:
            st.session_state.error_message = str(error)
        else:
            lock = _pipeline_lock()
            if not lock.acquire(blocking=False):
                st.session_state.error_message = "Сейчас уже выполняется анализ. Дождитесь завершения."
            else:
                try:
                    st.session_state.process_view = ProcessView()
                    draw_process()
                    bundle, from_cache = service.get_report(
                        st.session_state.input_url,
                        refresh=refresh_submitted,
                        progress=update_progress,
                        preview=update_preview,
                        insight=update_insight,
                    )
                    markdown = render_markdown(bundle)
                    st.session_state.report_bundle = bundle
                    st.session_state.report_markdown = markdown
                    st.session_state.report_is_saved = from_cache
                    st.session_state.process_view.complete(cached=from_cache)
                except (
                    UrlError,
                    CollectionError,
                    EvidenceValidationError,
                    ModelAccessError,
                    ModelTimeoutError,
                    ModelResponseError,
                ) as error:
                    if isinstance(error, (ModelAccessError, ModelTimeoutError, ModelResponseError)):
                        _save_safe_diagnostic(error)
                    st.session_state.error_message = _error_message(error)
                except Exception as error:
                    saved = _save_safe_diagnostic(error)
                    st.session_state.error_message = (
                        "Не удалось завершить анализ. Диагностика сохранена локально."
                        if saved
                        else "Не удалось завершить анализ, и диагностику не удалось сохранить."
                    )
                finally:
                    lock.release()
                    if st.session_state.error_message:
                        st.session_state.process_view.fail()
                    draw_process()
                    preview_slot.empty()

    if open_saved_submitted:
        st.session_state.process_view = None
        process_slot.empty()
        report_slot.empty()
        st.session_state.report_bundle = None
        st.session_state.report_markdown = None
        st.session_state.report_is_saved = False
        st.session_state.error_message = None
        try:
            org = normalize_org_url(st.session_state.input_url)
        except UrlError as error:
            st.session_state.error_message = str(error)
        else:
            bundle = service.report_store.load_saved_report(org.org_id)
            if bundle is None:
                st.session_state.error_message = (
                    "Для этой организации сохранённого отчёта пока нет"
                )
            else:
                try:
                    markdown = render_markdown(bundle)
                except EvidenceValidationError as error:
                    st.session_state.error_message = _error_message(error)
                else:
                    st.session_state.report_bundle = bundle
                    st.session_state.report_markdown = markdown
                    st.session_state.report_is_saved = True
                    st.session_state.process_view = ProcessView()
                    st.session_state.process_view.complete(cached=True)
                    draw_process()

    if st.session_state.error_message:
        st.error(st.session_state.error_message)

    if st.session_state.report_bundle is not None and st.session_state.report_markdown:
        with report_slot.container():
            _render_report(
                st.session_state.report_bundle,
                st.session_state.report_markdown,
                saved=st.session_state.report_is_saved,
            )


if __name__ == "__main__":
    main()
