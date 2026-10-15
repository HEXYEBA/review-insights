from scripts import check_llm


def test_check_llm_prints_only_model_health_and_duration(monkeypatch, capsys):
    monkeypatch.setattr(check_llm, "run_codex_json", lambda *args, **kwargs: {"ok": True})
    assert check_llm.main() == 0
    output = capsys.readouterr().out
    assert "model_name:" in output
    assert "ok: true" in output.lower()
    assert "duration_seconds:" in output


def test_check_llm_returns_failure_for_false_probe(monkeypatch, capsys):
    monkeypatch.setattr(check_llm, "run_codex_json", lambda *args, **kwargs: {"ok": False})
    assert check_llm.main() == 1
    assert "ok: false" in capsys.readouterr().out.lower()
