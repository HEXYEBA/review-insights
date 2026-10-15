from streamlit.testing.v1 import AppTest
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

from review_insights import service
from review_insights.models import CollectionError, ModelTimeoutError
from review_insights.urls import normalize_org_url


KOFEMANIYA_URL = "https://yandex.ru/maps/org/kofemaniya/1242233919/reviews/"
MIEM_URL = "https://yandex.ru/maps/org/niu_vshe_moskovskiy_institut_elektroniki_i_matematiki_imeni_a_n_tikhonova/1544682531/reviews/"
APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _element_by_key(elements, key):
    return next(element for element in elements if element.key == key or
                (key == "input_url" and element.key.startswith("input_url_")))


def _visible_text(app):
    values = []
    for collection_name in ("title", "header", "subheader", "markdown", "text", "caption", "success", "info", "warning", "error"):
        values.extend(
            str(element.value)
            for element in getattr(app, collection_name)
            if getattr(element, "value", None) is not None
        )
    return "\n".join(values)


def _bundle_for_url(bundle, url):
    matching = bundle.model_copy(deep=True)
    matching.collection.org = normalize_org_url(url)
    for review in matching.collection.reviews + matching.analyzed_reviews:
        review.source_url = matching.collection.org.url
    return matching


def test_app_renders_minimal_controls_and_two_examples():
    app = AppTest.from_file(str(APP_PATH)).run()

    assert not app.exception
    assert any(element.key.startswith("input_url_") for element in app.text_input)
    assert {element.key for element in app.button} >= {
        "example_kofemaniya",
        "example_miem",
        "analyze_submit",
        "refresh_submit",
        "open_saved_submit",
    }
    assert app.title[0].value == "Что говорят об организации"


def test_open_saved_button_submits_the_current_form_url():
    app = AppTest.from_file(str(APP_PATH)).run()

    open_saved = _element_by_key(app.button, "open_saved_submit")

    assert open_saved.form_id == "analysis_form"


@pytest.mark.parametrize(
    ("button_key", "expected_url"),
    [
        ("example_kofemaniya", KOFEMANIYA_URL),
        ("example_miem", MIEM_URL),
    ],
)
def test_example_button_only_fills_url(monkeypatch, button_key, expected_url):
    calls = []
    monkeypatch.setattr(service, "run_pipeline", lambda *args, **kwargs: calls.append(args))
    app = AppTest.from_file(str(APP_PATH)).run()

    _element_by_key(app.button, button_key).click().run()

    assert _element_by_key(app.text_input, "input_url").value == expected_url
    assert calls == []


def test_submit_runs_pipeline_once_and_rerun_uses_session_result(
    monkeypatch, valid_bundle
):
    calls = []
    coffee_bundle = _bundle_for_url(valid_bundle, KOFEMANIYA_URL)

    def fake_get_report(url, refresh=False, progress=None, preview=None, insight=None):
        calls.append((url, refresh))
        if progress:
            progress("Собираем отзывы…")
            progress("Анализируем 2 отзыва…")
        return coffee_bundle, False

    monkeypatch.setattr(service, "get_report", fake_get_report)
    app = AppTest.from_file(str(APP_PATH)).run()
    _element_by_key(app.text_input, "input_url").set_value(KOFEMANIYA_URL)
    _element_by_key(app.button, "analyze_submit").click().run()

    assert calls == [(KOFEMANIYA_URL, False)]
    assert coffee_bundle.collection.org.org_id == "1242233919"
    assert coffee_bundle.draft.summary in _visible_text(app)
    assert len(app.get("download_button")) == 1

    app.run()
    assert calls == [(KOFEMANIYA_URL, False)]
    assert len(app.get("download_button")) == 1


def test_cached_analyze_shows_saved_collection_date(monkeypatch, valid_bundle):
    coffee_bundle = _bundle_for_url(valid_bundle, KOFEMANIYA_URL)
    monkeypatch.setattr(service, "get_report", lambda *args, **kwargs: (coffee_bundle, True))

    def forbidden(*args, **kwargs):
        raise AssertionError("the cache-hit UI must not start a fresh pipeline")

    monkeypatch.setattr(service, "run_pipeline", forbidden)
    app = AppTest.from_file(str(APP_PATH)).run()
    _element_by_key(app.text_input, "input_url").set_value(KOFEMANIYA_URL)
    _element_by_key(app.button, "analyze_submit").click().run()

    assert "Сохранённый отчёт. Отзывы собраны" in _visible_text(app)
    assert len(app.get("download_button")) == 1


def test_saved_report_opens_and_downloads_without_collection_or_model(monkeypatch, valid_bundle):
    coffee_bundle = _bundle_for_url(valid_bundle, KOFEMANIYA_URL)
    lookups = []

    def load_saved(org_id):
        lookups.append(org_id)
        return coffee_bundle if org_id == "1242233919" else None

    def forbidden(*args, **kwargs):
        raise AssertionError("opening a saved report must not collect or call the model")

    monkeypatch.setattr(service.report_store, "load_saved_report", load_saved)
    monkeypatch.setattr(service, "get_report", forbidden)
    monkeypatch.setattr(service, "run_pipeline", forbidden)
    app = AppTest.from_file(str(APP_PATH)).run()
    _element_by_key(app.text_input, "input_url").set_value(KOFEMANIYA_URL)

    _element_by_key(app.button, "open_saved_submit").click().run()

    expected_date = coffee_bundle.collection.collected_at.astimezone(
        ZoneInfo("Europe/Moscow")
    ).strftime("%d.%m.%Y %H:%M МСК")
    assert lookups == ["1242233919"]
    assert f"Сохранённый отчёт. Отзывы собраны {expected_date}" in _visible_text(app)
    assert coffee_bundle.draft.summary in _visible_text(app)
    assert len(app.get("download_button")) == 1

    app.run()
    assert lookups == ["1242233919"]


def test_open_saved_report_reports_missing_file_without_network(monkeypatch):
    monkeypatch.setattr(service.report_store, "load_saved_report", lambda org_id: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("missing saved reports must not trigger live work")

    monkeypatch.setattr(service, "get_report", forbidden)
    app = AppTest.from_file(str(APP_PATH)).run()
    _element_by_key(app.text_input, "input_url").set_value(KOFEMANIYA_URL)

    _element_by_key(app.button, "open_saved_submit").click().run()

    assert "Для этой организации сохранённого отчёта пока нет" in _visible_text(app)


def test_model_timeout_names_the_failed_stage_and_keeps_saved_report_available(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    def timed_out(*args, **kwargs):
        raise ModelTimeoutError("synthetic private subprocess details")

    monkeypatch.setattr(service, "get_report", timed_out)
    app = AppTest.from_file(str(APP_PATH)).run()
    _element_by_key(app.text_input, "input_url").set_value(KOFEMANIYA_URL)
    _element_by_key(app.button, "refresh_submit").click().run()

    message = " ".join(str(element.value) for element in app.error)
    assert "Модель" in message
    assert "300" in message
    assert "Открыть сохранённый отчёт" in message
    assert "synthetic private" not in message
    assert _element_by_key(app.button, "open_saved_submit") is not None
    diagnostic = (tmp_path / "app-errors.log").read_text(encoding="utf-8")
    assert "ModelTimeoutError" in diagnostic
    assert "synthetic private" not in diagnostic


def test_invalid_url_is_rejected_before_pipeline_call(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "run_pipeline", lambda *args, **kwargs: calls.append(args))
    app = AppTest.from_file(str(APP_PATH)).run()
    _element_by_key(app.text_input, "input_url").set_value("https://example.com/not-a-card")
    _element_by_key(app.button, "analyze_submit").click().run()

    assert calls == []
    assert "полную ссылку" in _visible_text(app).lower()


def test_failed_new_run_clears_old_report_and_releases_lock(monkeypatch, valid_bundle):
    calls = []
    coffee_bundle = _bundle_for_url(valid_bundle, KOFEMANIYA_URL)

    def fake_get_report(url, refresh=False, progress=None, preview=None, insight=None):
        calls.append((url, refresh))
        if len(calls) == 2:
            raise CollectionError(
                "Яндекс Карты временно ограничили загрузку страницы "
                "(HTTP 429). Повторите позже."
            )
        return coffee_bundle, False

    monkeypatch.setattr(service, "get_report", fake_get_report)
    app = AppTest.from_file(str(APP_PATH)).run()
    url_input = _element_by_key(app.text_input, "input_url")
    submit = _element_by_key(app.button, "analyze_submit")

    url_input.set_value(KOFEMANIYA_URL)
    submit.click().run()
    assert coffee_bundle.draft.summary in _visible_text(app)

    _element_by_key(app.text_input, "input_url").set_value(MIEM_URL)
    _element_by_key(app.button, "refresh_submit").click().run()
    assert "HTTP 429" in _visible_text(app)
    assert "Повторите позже" in _visible_text(app)
    assert coffee_bundle.draft.summary not in _visible_text(app)

    _element_by_key(app.text_input, "input_url").set_value(KOFEMANIYA_URL)
    _element_by_key(app.button, "analyze_submit").click().run()
    assert calls == [
        (KOFEMANIYA_URL, False),
        (MIEM_URL, True),
        (KOFEMANIYA_URL, False),
    ]
    assert coffee_bundle.draft.summary in _visible_text(app)


def test_report_topics_are_named_before_opening_evidence(monkeypatch, valid_bundle):
    monkeypatch.setattr(service, "get_report", lambda *args, **kwargs: (valid_bundle, False))
    app = AppTest.from_file(str(APP_PATH)).run()
    _element_by_key(app.text_input, "input_url").set_value(KOFEMANIYA_URL)
    _element_by_key(app.button, "analyze_submit").click().run()
    assert not app.exception
    labels = " ".join(str(element.label) for element in app.expander)
    assert valid_bundle.draft.themes[0].title in labels
    assert len(app.metric) >= 3
