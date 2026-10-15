from datetime import datetime, timezone
from pathlib import Path

from review_insights.models import CollectionResult, OrgRef, Review
from scripts import smoke_collect


def sample_result(org):
    return CollectionResult(
        org=org,
        org_name="Тестовое кафе",
        collected_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        source_review_count=1,
        source_rating_count=1,
        source_rating=5,
        reviews=[
            Review(
                review_id="private-review",
                text="Текст отзыва не должен попадать в консоль.",
                rating=5,
                published_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                date_label="1 октября",
                source_url=org.url,
            )
        ],
        stop_reason="exhausted",
        sort_mode="newest",
        warnings=[],
    )


def test_smoke_command_saves_json_inside_artifacts_and_prints_only_summary(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        smoke_collect, "run_collection", lambda org: sample_result(org)
    )
    assert smoke_collect.main(
        ["--url", "https://yandex.ru/maps/org/test/123/", "--out", "sample.json"],
        artifacts_dir=tmp_path,
    ) == 0

    output = capsys.readouterr().out
    saved = tmp_path / "sample.json"
    assert saved.exists()
    assert "collected_count: 1" in output
    assert "Текст отзыва не должен попадать в консоль" not in output


def test_smoke_command_rejects_output_outside_artifacts(tmp_path, capsys):
    assert smoke_collect.main(
        ["--url", "https://yandex.ru/maps/org/test/123/", "--out", "../outside.json"],
        artifacts_dir=tmp_path / "artifacts",
    ) == 1
    assert not (tmp_path / "outside.json").exists()
    assert "outside.json" not in capsys.readouterr().out
