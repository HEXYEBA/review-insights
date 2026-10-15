import io
import json
import os
import threading
import time

import pytest

from review_insights import collect_worker
from review_insights.models import CollectionResult, OrgRef


def make_result(org):
    return CollectionResult(
        org=org,
        org_name="Тестовое кафе",
        collected_at="2026-10-14T09:00:00Z",
        source_review_count=0,
        source_rating_count=0,
        source_rating=0,
        reviews=[],
        stop_reason="exhausted",
        sort_mode="default",
        warnings=[],
    )


def test_worker_reads_one_request_and_atomically_writes_snapshot(tmp_path, monkeypatch):
    request = {
        "url": "https://yandex.ru/maps/org/test/123/reviews/",
        "limit": 200,
        "timeout_s": 60,
        "result_path": str(tmp_path / "result.json"),
    }
    monkeypatch.setattr(collect_worker.sys, "stdin", io.StringIO(json.dumps(request)))
    seen = {}

    def fake_collect(org, *, limit, timeout_s, on_snapshot):
        seen.update(org=org, limit=limit, timeout_s=timeout_s)
        result = make_result(org)
        on_snapshot(result)
        return result

    monkeypatch.setattr(collect_worker, "collect_reviews", fake_collect)
    assert collect_worker.main() == 0

    result_path = tmp_path / "result.json"
    assert json.loads(result_path.read_text(encoding="utf-8"))["stop_reason"] == "exhausted"
    assert not list(tmp_path.glob("*.tmp"))
    assert seen["org"] == OrgRef(org_id="123", url=request["url"])
    assert seen["limit"] == 200
    assert seen["timeout_s"] == 60


@pytest.mark.skipif(os.name != "nt", reason="Windows holds open targets against replacement")
def test_snapshot_survives_a_brief_reader_lock_without_aborting_collection(tmp_path):
    path = tmp_path / "collection.json"
    old = make_result(OrgRef(org_id="123", url="https://yandex.ru/maps/org/a/123/reviews/"))
    collect_worker._write_snapshot(path, old)
    reader = path.open("rb")
    release = threading.Timer(0.06, reader.close)
    updated = old.model_copy(update={"org_name": "Обновлённое имя"})
    release.start()
    try:
        collect_worker._write_snapshot(path, updated)
    finally:
        release.join()
        reader.close()
    saved = CollectionResult.model_validate_json(path.read_text(encoding="utf-8"))
    assert saved.org_name == "Обновлённое имя"
    assert not list(tmp_path.glob("*.tmp"))


def test_snapshot_does_not_retry_disk_errors_or_destroy_previous_result(tmp_path, monkeypatch):
    path = tmp_path / "collection.json"
    old = make_result(OrgRef(org_id="123", url="https://yandex.ru/maps/org/a/123/reviews/"))
    collect_worker._write_snapshot(path, old)
    attempts = []

    def disk_error(*args):
        attempts.append(1)
        raise OSError("disk unavailable")

    monkeypatch.setattr(collect_worker.os, "replace", disk_error)
    with pytest.raises(OSError):
        collect_worker._write_snapshot(path, old.model_copy(update={"org_name": "Новое"}))
    assert attempts == [1]
    assert CollectionResult.model_validate_json(path.read_text(encoding="utf-8")) == old


@pytest.mark.skipif(os.name != "nt", reason="Windows reader sharing semantics")
def test_permanent_snapshot_lock_fails_in_bounded_time_and_keeps_previous_json(tmp_path):
    path = tmp_path / "collection.json"
    old = make_result(OrgRef(org_id="123", url="https://yandex.ru/maps/org/a/123/reviews/"))
    collect_worker._write_snapshot(path, old)
    started = time.monotonic()
    with path.open("rb"):
        with pytest.raises(PermissionError):
            collect_worker._write_snapshot(path, old.model_copy(update={"org_name": "Новое"}))
    assert time.monotonic() - started < 1.5
    assert CollectionResult.model_validate_json(path.read_text(encoding="utf-8")) == old
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("failure_stage", ["collect", "save"])
def test_worker_failure_emits_safe_frame_metadata_without_exception_message(tmp_path, monkeypatch, capsys, failure_stage):
    request = {"url": "https://yandex.ru/maps/org/a/123/reviews/", "limit": 200,
               "timeout_s": 60, "result_path": str(tmp_path / "collection.json")}
    monkeypatch.setattr(collect_worker.sys, "stdin", io.StringIO(json.dumps(request)))

    def broken_collect(*args, **kwargs):
        raise PermissionError("private-token-and-review-text")

    if failure_stage == "collect":
        monkeypatch.setattr(collect_worker, "collect_reviews", broken_collect)
    else:
        monkeypatch.setattr(collect_worker, "collect_reviews", lambda org, **kwargs: make_result(org))
        monkeypatch.setattr(collect_worker, "_write_snapshot", broken_collect)
    assert collect_worker.main() == 1
    output = capsys.readouterr().err
    assert "private-token-and-review-text" not in output
    line = next(line for line in output.splitlines() if line.startswith("COLLECTOR_FAILURE "))
    payload = json.loads(line.removeprefix("COLLECTOR_FAILURE "))
    assert payload["error_type"] == "PermissionError"
    assert any(frame["function"] == "broken_collect" for frame in payload["frames"])
