from datetime import timedelta

from review_insights import report_store


def test_store_roundtrip(valid_bundle, tmp_path):
    report_store.save_report(valid_bundle, root=tmp_path)

    loaded = report_store.load_saved_report("123", root=tmp_path)

    assert loaded == valid_bundle


def test_corrupt_report_is_unavailable(tmp_path):
    (tmp_path / "123.json").write_text("{bad json", encoding="utf-8")

    assert report_store.load_saved_report("123", root=tmp_path) is None


def test_report_for_another_organization_is_unavailable(valid_bundle, tmp_path):
    (tmp_path / "124.json").write_text(
        valid_bundle.model_dump_json(), encoding="utf-8"
    )

    assert report_store.load_saved_report("124", root=tmp_path) is None


def test_path_traversal_and_invalid_ids_are_rejected(valid_bundle, tmp_path):
    assert report_store.load_saved_report("../123", root=tmp_path) is None
    foreign = valid_bundle.model_copy(deep=True)
    foreign.collection.org.org_id = "../123"

    try:
        report_store.save_report(foreign, root=tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe organization id must not form a file path")

    assert not (tmp_path.parent / "123.json").exists()


def test_report_freshness_uses_collection_time(valid_bundle):
    collected = valid_bundle.collection.collected_at

    assert report_store.is_fresh_report(valid_bundle, collected + timedelta(hours=6))
    assert not report_store.is_fresh_report(
        valid_bundle, collected + timedelta(hours=6, seconds=1)
    )
    assert not report_store.is_fresh_report(
        valid_bundle, collected - timedelta(seconds=1)
    )
