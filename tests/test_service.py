import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from review_insights import service
from review_insights import report_store
from review_insights.models import (
    CollectionError,
    CollectionResult,
    Evidence,
    ExcludedReview,
    ModelTimeoutError,
    OrgRef,
    ReportDraft,
    Review,
    Theme,
)


ORG_URL = "https://yandex.ru/maps/org/test/123/reviews/"


def test_pipeline_prepares_real_analysis_preview_before_model(monkeypatch, collection, valid_draft):
    events = []
    monkeypatch.setattr(service, "run_collection", lambda org: collection)

    def model(collected, selected, *, progress=None, insight=None):
        assert events[0].stage == "analysis"
        assert events[0].selected_reviews == selected
        assert events[0].draft is None
        return valid_draft

    monkeypatch.setattr(service, "analyze_reviews", model)
    result = service.run_pipeline(ORG_URL, insight=events.append)
    assert len(result.analyzed_reviews) == 2


def test_collection_previews_only_valid_matching_snapshots(monkeypatch):
    observed = []

    def worker(args, *, stdin_text, on_tick, **kwargs):
        path = Path(json.loads(stdin_text)["result_path"])
        path.write_text('{"partial":', encoding="utf-8")
        on_tick()
        wrong = result_with_one_review(OrgRef(org_id="999", url=ORG_URL))
        path.write_text(wrong.model_dump_json(), encoding="utf-8")
        on_tick()
        right = result_with_one_review(OrgRef(org_id="123", url=ORG_URL))
        path.write_text(right.model_dump_json(), encoding="utf-8")
        on_tick()
        on_tick()
        return 0

    monkeypatch.setattr(service, "run_child", worker)
    service.run_collection(OrgRef(org_id="123", url=ORG_URL), preview=observed.append)
    assert len(observed) == 1
    assert observed[0].reviews[0].text == "Проверочный отзыв."
    assert observed[0].org.org_id == "123"


def test_partial_worker_failure_keeps_safe_diagnostic_after_private_files_are_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    write_result = fake_child_with_result({})

    def failed_worker(args, **kwargs):
        write_result(args, **kwargs)
        kwargs["stderr_path"].write_text('COLLECTOR_FAILURE ' + json.dumps({
            "error_type": "PermissionError",
            "message": "private-review-and-secret",
            "frames": [{"file": "collect_worker.py", "line": 42, "function": "_write_snapshot"}],
        }), encoding="utf-8")
        return 1

    monkeypatch.setattr(service, "run_child", failed_worker)
    result = service.run_collection(OrgRef(org_id="123", url=ORG_URL))
    assert len(result.reviews) == 1
    assert result.stop_reason == "load_error"
    text = (tmp_path / "collection-errors.jsonl").read_text(encoding="utf-8")
    assert "private-review-and-secret" not in text
    record = json.loads(text)
    assert record["error_type"] == "PermissionError"
    assert record["review_count"] == 1
    assert record["frames"] == [{"file": "collect_worker.py", "line": 42, "function": "_write_snapshot"}]


def test_live_report_forwards_collection_preview_before_model_and_saves_result(
    monkeypatch, tmp_path, collection, valid_draft
):
    events = []
    monkeypatch.setattr(report_store, "REPORTS_DIR", tmp_path)

    def worker(args, *, stdin_text, on_tick, **kwargs):
        Path(json.loads(stdin_text)["result_path"]).write_text(collection.model_dump_json(), encoding="utf-8")
        on_tick()
        return 0

    def model(collected, selected, *, progress=None):
        assert events[0] == ("preview", 2)
        events.append(("model", len(selected)))
        return valid_draft

    monkeypatch.setattr(service, "run_child", worker)
    monkeypatch.setattr(service, "analyze_reviews", model)
    bundle, cached = service.get_report(
        ORG_URL, refresh=True,
        preview=lambda snapshot: events.append(("preview", len(snapshot.reviews))),
    )
    assert not cached
    assert events[-1] == ("model", 2)
    assert report_store.load_saved_report("123") == bundle


def result_with_one_review(org):
    review = Review(
        review_id="review-1",
        text="Проверочный отзыв.",
        rating=5,
        published_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        date_label="1 октября",
        source_url=org.url,
    )
    return CollectionResult(
        org=org,
        org_name="Тестовое кафе",
        collected_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        source_review_count=1,
        source_rating_count=1,
        source_rating=5,
        reviews=[review],
        stop_reason="exhausted",
        sort_mode="newest",
        warnings=[],
    )


def test_get_report_uses_fresh_saved_report_without_pipeline(monkeypatch, valid_bundle):
    monkeypatch.setattr(
        report_store,
        "load_saved_report",
        lambda org_id: valid_bundle if org_id == "123" else None,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("fresh cached reports must not collect or call the model")

    monkeypatch.setattr(service, "run_pipeline", forbidden)

    result, from_cache = service.get_report(ORG_URL, insight=forbidden, preview=forbidden, progress=forbidden)

    assert result == valid_bundle
    assert from_cache is True


def test_get_report_refreshes_an_expired_saved_report(monkeypatch, valid_bundle):
    stale = valid_bundle.model_copy(deep=True)
    stale.collection.collected_at = datetime.now(timezone.utc) - timedelta(hours=7)
    fresh = valid_bundle.model_copy(deep=True)
    loaded = []
    saved = []

    monkeypatch.setattr(
        report_store,
        "load_saved_report",
        lambda org_id: loaded.append(org_id) or stale,
    )
    monkeypatch.setattr(service, "run_pipeline", lambda url, progress=None: fresh)
    monkeypatch.setattr(report_store, "save_report", lambda bundle: saved.append(bundle))

    result, from_cache = service.get_report(ORG_URL)

    assert loaded == ["123"]
    assert result == fresh
    assert from_cache is False
    assert saved == [fresh]


def test_failed_refresh_preserves_the_previous_saved_report(
    monkeypatch, valid_bundle, tmp_path
):
    monkeypatch.setattr(report_store, "REPORTS_DIR", tmp_path)
    report_store.save_report(valid_bundle)

    def fail_refresh(*args, **kwargs):
        raise CollectionError("refresh failed")

    monkeypatch.setattr(service, "run_pipeline", fail_refresh)

    with pytest.raises(CollectionError, match="refresh failed"):
        service.get_report(ORG_URL, refresh=True)

    assert report_store.load_saved_report("123") == valid_bundle


def fake_child_with_result(captured, stop_with_timeout=False):
    def run_child(args, *, stdin_text, cwd, timeout_s, env, stderr_path, stdout_path=None):
        request = json.loads(stdin_text)
        captured.update(args=args, request=request, env=env, timeout_s=timeout_s)
        org = OrgRef(org_id="123", url=ORG_URL)
        snapshot = result_with_one_review(org)
        (request_path := __import__("pathlib").Path(request["result_path"])).write_text(
            snapshot.model_dump_json(), encoding="utf-8"
        )
        if stop_with_timeout:
            raise ModelTimeoutError("synthetic timeout")
        return 0

    return run_child


def test_run_collection_uses_private_worker_and_removes_api_secrets(monkeypatch):
    captured = {}
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setattr(service, "run_child", fake_child_with_result(captured))

    result = service.run_collection(OrgRef(org_id="123", url=ORG_URL))

    assert result.org.org_id == "123"
    assert result.reviews[0].text == "Проверочный отзыв."
    assert captured["request"]["result_path"].endswith(".json")
    assert captured["timeout_s"] == 65
    assert "OPENAI_API_KEY" not in captured["env"]
    assert captured["env"]["PLAYWRIGHT_BROWSERS_PATH"] == "0"


def test_run_collection_preserves_partial_reviews_after_parent_timeout(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        service,
        "run_child",
        fake_child_with_result(captured, stop_with_timeout=True),
    )

    result = service.run_collection(OrgRef(org_id="123", url=ORG_URL))

    assert result.stop_reason == "deadline"
    assert result.reviews[0].review_id == "review-1"
    assert any("врем" in warning.lower() for warning in result.warnings)


@pytest.mark.skipif(os.name != "nt", reason="Windows holds open files against deletion")
@pytest.mark.parametrize("limited", [False, True])
def test_collection_result_survives_locked_worker_stderr(monkeypatch, tmp_path, limited):
    monkeypatch.setattr(service.tempfile, "tempdir", str(tmp_path))
    handles = []
    write_result = fake_child_with_result({})

    def worker_with_inherited_stderr(args, **kwargs):
        code = write_result(args, **kwargs)
        if limited:
            request = json.loads(kwargs["stdin_text"])
            snapshot = result_with_one_review(OrgRef(org_id="123", url=ORG_URL))
            snapshot.reviews = []
            snapshot.stop_reason = "rate_limited"
            Path(request["result_path"]).write_text(
                snapshot.model_dump_json(), encoding="utf-8"
            )
        # Reproduce a Windows child still holding the inherited log at teardown.
        handles.append(kwargs["stderr_path"].open("ab"))
        return code

    monkeypatch.setattr(service, "run_child", worker_with_inherited_stderr)
    try:
        if limited:
            with pytest.raises(CollectionError, match="HTTP 429"):
                service.run_collection(OrgRef(org_id="123", url=ORG_URL))
        else:
            result = service.run_collection(OrgRef(org_id="123", url=ORG_URL))
            assert result.reviews[0].review_id == "review-1"
    finally:
        for handle in handles:
            handle.close()


def test_run_collection_does_not_return_model_data_when_worker_fails(monkeypatch, tmp_path):
    def failed_child(args, *, stdin_text, cwd, timeout_s, env, stderr_path, stdout_path=None):
        stderr_path.write_text("synthetic private diagnostic", encoding="utf-8")
        return 1

    monkeypatch.setattr(service, "run_child", failed_child)
    with pytest.raises(CollectionError) as error:
        service.run_collection(OrgRef(org_id="123", url=ORG_URL))
    assert "synthetic private diagnostic" not in str(error.value)


def test_run_collection_explains_rate_limited_source(monkeypatch):
    def rate_limited_child(args, *, stdin_text, cwd, timeout_s, env, stderr_path, stdout_path=None):
        request = json.loads(stdin_text)
        org = OrgRef(org_id="123", url=ORG_URL)
        result = result_with_one_review(org).model_copy(
            update={"reviews": [], "stop_reason": "rate_limited"}
        )
        __import__("pathlib").Path(request["result_path"]).write_text(
            result.model_dump_json(), encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(service, "run_child", rate_limited_child)

    with pytest.raises(CollectionError) as error:
        service.run_collection(OrgRef(org_id="123", url=ORG_URL))

    assert "HTTP 429" in str(error.value)
    assert "Повторите позже" in str(error.value)


def test_empty_collection_does_not_call_model(monkeypatch, empty_collection):
    monkeypatch.setattr(service, "run_collection", lambda org: empty_collection)

    def forbidden(*args, **kwargs):
        raise AssertionError("LLM must not run")

    monkeypatch.setattr(service, "analyze_reviews", forbidden)
    with pytest.raises(CollectionError):
        service.run_pipeline("https://yandex.ru/maps/org/a/123/reviews/")


def test_partial_collection_continues_and_pipeline_reports_stages(
    monkeypatch, selected_reviews, valid_draft
):
    extra_reviews = [
        Review(
            review_id=f"extra-{index}",
            text=f"Дополнительный отзыв {index}.",
            rating=4,
            published_at=datetime(2026, 10, 3 + index, tzinfo=timezone.utc),
            date_label="",
            source_url=ORG_URL,
        )
        for index in range(19)
    ]
    partial = CollectionResult(
        org=OrgRef(org_id="123", url=ORG_URL),
        org_name="Тестовое кафе",
        collected_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        source_review_count=119,
        source_rating_count=808,
        source_rating=4.5,
        reviews=[selected_reviews[0], *extra_reviews],
        stop_reason="no_new_reviews",
        sort_mode="mixed",
        warnings=["Источник не сообщил полный порядок страниц."],
    )
    stages = []
    analyzed = {}
    monkeypatch.setattr(service, "run_collection", lambda org: partial)

    def fake_analyze(collection, selected, *, progress=None):
        analyzed["selected"] = selected
        if progress:
            progress("Проверяем цитаты и ссылки…")
        return ReportDraft(
            summary="Synthetic partial report.",
            themes=[
                Theme(
                    theme_id="T01",
                    kind="strength",
                    scope="place",
                    title="Пример",
                    explanation="Синтетическая тема теста.",
                    supporting_review_ids=[review.review_id for review in selected],
                    evidence=[
                        Evidence(
                            review_id=selected[0].review_id,
                            quote=selected[0].text[:20],
                        )
                    ],
                )
            ],
            actions=[],
            marketing_hypotheses=[],
            excluded_reviews=[],
        )

    monkeypatch.setattr(service, "analyze_reviews", fake_analyze)
    bundle = service.run_pipeline(ORG_URL, progress=stages.append)

    assert len(analyzed["selected"]) == 20
    assert bundle.collection.stop_reason == "no_new_reviews"
    assert "Источник не сообщил полный порядок страниц." in bundle.warnings
    assert stages == [
        "Собираем отзывы…", "Анализируем 20 отзывов…", "Проверяем цитаты и ссылки…"
    ]
    assert not any("%" in warning for warning in bundle.warnings)


def test_pipeline_warns_when_selection_is_smaller_than_collection(
    monkeypatch, collection, selected_reviews, valid_draft
):
    stages = []
    monkeypatch.setattr(service, "run_collection", lambda org: collection)
    monkeypatch.setattr(
        service,
        "select_analysis_reviews",
        lambda reviews: [selected_reviews[0]],
    )
    draft_without_unselected_exclusion = valid_draft.model_copy(deep=True)
    draft_without_unselected_exclusion.excluded_reviews = []
    monkeypatch.setattr(
        service,
        "analyze_reviews",
        lambda collection, selected, **kwargs: draft_without_unselected_exclusion,
    )

    bundle = service.run_pipeline(ORG_URL, progress=stages.append)

    assert any("Собрано 2, проанализировано 1" in warning for warning in bundle.warnings)
    assert any("малая выборка" in warning.lower() for warning in bundle.warnings)
    assert stages == ["Собираем отзывы…", "Анализируем 1 отзыв…"]


def test_empty_analysis_selection_does_not_call_model(monkeypatch, collection):
    monkeypatch.setattr(service, "run_collection", lambda org: collection)
    monkeypatch.setattr(service, "select_analysis_reviews", lambda reviews: [])

    def forbidden(*args, **kwargs):
        raise AssertionError("LLM must not run")

    monkeypatch.setattr(service, "analyze_reviews", forbidden)
    with pytest.raises(CollectionError):
        service.run_pipeline(ORG_URL)


def test_pipeline_analyzes_only_50_newest_reviews(monkeypatch):
    org = OrgRef(org_id="123", url=ORG_URL)
    reviews = [
        Review(
            review_id=f"r{index}",
            text=f"Review text {index}.",
            rating=4,
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
            + timedelta(days=index),
            date_label="",
            source_url=ORG_URL,
        )
        for index in range(60)
    ]
    collected = CollectionResult(
        org=org,
        org_name="Test cafe",
        collected_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        source_review_count=60,
        source_rating_count=60,
        source_rating=4.5,
        reviews=reviews,
        stop_reason="exhausted",
        sort_mode="newest",
        warnings=[],
    )
    captured = {}
    monkeypatch.setattr(service, "run_collection", lambda org: collected)

    def fake_analyze(collection, selected, *, progress=None):
        captured["selected"] = selected
        return ReportDraft(
            summary="Analysis summary.",
            themes=[],
            actions=[],
            marketing_hypotheses=[],
            excluded_reviews=[
                ExcludedReview(review_id=review.review_id, reason="uninterpretable")
                for review in selected
            ],
        )

    monkeypatch.setattr(service, "analyze_reviews", fake_analyze)

    bundle = service.run_pipeline(ORG_URL)

    assert len(bundle.collection.reviews) == 60
    assert len(captured["selected"]) == 50
    assert captured["selected"][0].review_id == "r59"
    assert captured["selected"][-1].review_id == "r10"
    assert any("Лимит одного анализа — 50" in warning for warning in bundle.warnings)
