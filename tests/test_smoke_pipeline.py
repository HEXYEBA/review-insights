import json

import pytest

from scripts import smoke_pipeline
from review_insights.models import CollectionError


def test_smoke_pipeline_writes_report_and_prints_only_safe_metadata(
    monkeypatch, tmp_path, valid_bundle, capsys
):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    def fake_pipeline(url, progress):
        assert url == "https://yandex.ru/maps/org/a/123/reviews/"
        progress("Собираем отзывы…")
        progress("Анализируем 2 отзыва…")
        return valid_bundle

    monkeypatch.setattr(smoke_pipeline, "run_pipeline", fake_pipeline)

    assert (
        smoke_pipeline.main(
            ["--url", "https://yandex.ru/maps/org/a/123/", "--out-prefix", "unit-run"]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["org_id"] == "123"
    assert output["collected_count"] == 2
    assert output["analyzed_count"] == 2
    assert output["stages"] == ["Собираем отзывы…", "Анализируем 2 отзыва…"]
    assert "Кофе вкусный" not in json.dumps(output, ensure_ascii=False)
    assert (tmp_path / "artifacts" / "unit-run.json").is_file()
    assert (tmp_path / "artifacts" / "unit-run.md").is_file()


def test_smoke_pipeline_failure_leaves_existing_report_untouched(
    monkeypatch, tmp_path, capsys
):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    json_path = artifacts / "unit-run.json"
    markdown_path = artifacts / "unit-run.md"
    json_path.write_text("previous json", encoding="utf-8")
    markdown_path.write_text("previous markdown", encoding="utf-8")
    monkeypatch.setenv("ARTIFACTS_DIR", str(artifacts))

    def fail_pipeline(url, progress):
        raise CollectionError("Не удалось прочитать отзывы")

    monkeypatch.setattr(smoke_pipeline, "run_pipeline", fail_pipeline)

    assert (
        smoke_pipeline.main(
            ["--url", "https://yandex.ru/maps/org/a/123/", "--out-prefix", "unit-run"]
        )
        == 1
    )
    assert json_path.read_text(encoding="utf-8") == "previous json"
    assert markdown_path.read_text(encoding="utf-8") == "previous markdown"
    assert "CollectionError" in capsys.readouterr().err


def test_smoke_pipeline_rejects_a_path_as_output_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))

    with pytest.raises(SystemExit):
        smoke_pipeline.main(
            ["--url", "https://yandex.ru/maps/org/a/123/", "--out-prefix", "../outside"]
        )
