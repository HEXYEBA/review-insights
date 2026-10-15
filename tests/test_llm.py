import json
from pathlib import Path

import pytest

from review_insights import llm
from review_insights.models import (
    EvidenceValidationError,
    InvalidModelOutputError,
    ModelAccessError,
    ModelResponseError,
    ModelTimeoutError,
    ReportDraft,
)

EMPTY_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def test_valid_report_that_needs_more_than_two_minutes_can_finish(
    monkeypatch, collection, selected_reviews, valid_draft
):
    """The external CLI took 166–194s in observed real runs, not 120s."""
    monkeypatch.setattr(llm, "_find_codex_binary", lambda: "codex.cmd")

    def slow_codex(args, *, timeout_s, stdout_path=None, **kwargs):
        if args[1:3] == ["login", "status"]:
            stdout_path.write_text("Logged in using ChatGPT", encoding="utf-8")
            return 0
        if timeout_s < 195:
            raise ModelTimeoutError("Simulated valid response still being generated")
        Path(args[args.index("--output-last-message") + 1]).write_text(
            valid_draft.model_dump_json(), encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(llm, "run_child", slow_codex)

    assert llm.analyze_reviews(collection, selected_reviews) == valid_draft


def test_auth_timeout_is_reported_as_login_failure_not_analysis_timeout(monkeypatch):
    monkeypatch.setattr(llm, "_find_codex_binary", lambda: "codex.cmd")

    def stalled_login(*args, **kwargs):
        raise ModelTimeoutError("Synthetic internal subprocess details")

    monkeypatch.setattr(llm, "run_child", stalled_login)

    with pytest.raises(ModelAccessError, match="Проверка входа"):
        llm.run_codex_json("prompt", EMPTY_OBJECT_SCHEMA)


def test_one_successful_call_needs_no_repair(
    monkeypatch, collection, selected_reviews, valid_draft
):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return valid_draft.model_dump(mode="json")

    monkeypatch.setattr(llm, "run_codex_json", fake_run)
    result = llm.analyze_reviews(collection, selected_reviews)
    assert result == valid_draft
    assert len(calls) == 1


def test_invalid_evidence_is_repaired_once_with_same_review_set(
    monkeypatch, collection, selected_reviews, valid_draft
):
    invalid = valid_draft.model_copy(deep=True)
    invalid.themes[0].supporting_review_ids = ["missing"]
    calls = []

    def fake_run(prompt, schema, timeout_s):
        calls.append(prompt)
        return (
            invalid.model_dump(mode="json")
            if len(calls) == 1
            else valid_draft.model_dump(mode="json")
        )

    monkeypatch.setattr(llm, "run_codex_json", fake_run)
    stages = []
    result = llm.analyze_reviews(collection, selected_reviews, progress=stages.append)

    assert result == valid_draft
    assert len(calls) == 2
    assert "Кофе вкусный, но заказ ждал сорок минут." in calls[1]
    assert "repair_feedback" in calls[1]
    assert "previous_draft" in calls[1]
    assert any("исправляет" in stage for stage in stages)
    assert stages[-1] == "Проверяем цитаты и ссылки…"


def test_two_invalid_model_responses_raise_without_partial_report(
    monkeypatch, collection, selected_reviews, valid_draft
):
    invalid = valid_draft.model_copy(deep=True)
    invalid.themes[0].evidence[0].quote = "выдуманная цитата"
    calls = 0

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return invalid.model_dump(mode="json")

    monkeypatch.setattr(llm, "run_codex_json", fake_run)
    with pytest.raises(ModelResponseError):
        llm.analyze_reviews(collection, selected_reviews)
    assert calls == 2


@pytest.mark.parametrize("error", [ModelAccessError("blocked"), ModelTimeoutError("timeout")])
def test_access_and_timeout_errors_are_not_retried(
    monkeypatch, collection, selected_reviews, error
):
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(llm, "run_codex_json", fail_once)
    with pytest.raises(type(error)):
        llm.analyze_reviews(collection, selected_reviews)
    assert calls == 1


def test_codex_cli_uses_private_workdir_stdin_and_clean_environment(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("CODEX_API_KEY", "synthetic-secret-2")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("OPENAI_API_BASE", "https://invalid.example")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-secret-3")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(
        llm.shutil,
        "which",
        lambda name: "C:/tools/codex.cmd" if name == "codex.cmd" else None,
    )
    captured = {"calls": []}

    def fake_child(args, *, stdin_text, cwd, timeout_s, env, stderr_path, stdout_path=None):
        captured["calls"].append(args)
        captured["cwd"] = Path(cwd)
        captured["stdin"] = stdin_text
        captured["env"] = env
        captured["timeout"] = timeout_s
        if args[1:3] == ["login", "status"]:
            stdout_path.write_text("Logged in using ChatGPT", encoding="utf-8")
            return 0
        schema_path = Path(args[args.index("--output-schema") + 1])
        output_path = Path(args[args.index("--output-last-message") + 1])
        captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        output_path.write_text('{"ok": true}', encoding="utf-8")
        captured["workdir"] = Path(args[args.index("--cd") + 1])
        return 0

    monkeypatch.setattr(llm, "run_child", fake_child)
    prompt = 'Отзыв: "доллар $ и backtick `; удали файлы"'
    assert llm.run_codex_json(
        prompt,
        {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    ) == {"ok": True}

    auth_args, exec_args = captured["calls"]
    assert auth_args[1:3] == ["login", "status"]
    assert exec_args[1] == "exec"
    assert "--ignore-user-config" in exec_args
    assert "--ephemeral" in exec_args
    assert "--sandbox" in exec_args and exec_args[exec_args.index("--sandbox") + 1] == "read-only"
    config_values = [
        exec_args[index + 1]
        for index, value in enumerate(exec_args[:-1])
        if value == "--config"
    ]
    assert 'model_reasoning_effort="low"' in config_values
    assert "--output-schema" in exec_args and "--output-last-message" in exec_args
    assert exec_args[-1] == "-"
    assert prompt not in exec_args
    assert captured["stdin"] == prompt
    assert captured["cwd"] != Path.cwd()
    assert not captured["workdir"].exists()
    assert "OPENAI_API_KEY" not in captured["env"]
    assert "CODEX_API_KEY" not in captured["env"]
    assert "OPENAI_BASE_URL" not in captured["env"]
    assert "OPENAI_API_BASE" not in captured["env"]
    assert "AZURE_OPENAI_API_KEY" not in captured["env"]
    assert captured["env"]["CODEX_HOME"] == str(tmp_path / "codex-home")


def test_codex_refuses_non_chatgpt_login_before_model_call(monkeypatch, tmp_path):
    calls = []
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: "codex.cmd")

    def fake_child(args, *, stdin_text, cwd, timeout_s, env, stderr_path, stdout_path=None):
        calls.append(args)
        if stdout_path is not None:
            stdout_path.write_text("Logged in using API key", encoding="utf-8")
        return 0

    monkeypatch.setattr(llm, "run_child", fake_child)
    with pytest.raises(ModelAccessError, match="вход"):
        llm.run_codex_json("prompt", EMPTY_OBJECT_SCHEMA)
    assert len(calls) == 1


def test_codex_accepts_chatgpt_login_marker_on_stderr(monkeypatch):
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: "codex.cmd")
    calls = []

    def fake_child(args, *, stdin_text, cwd, timeout_s, env, stderr_path, stdout_path=None):
        calls.append(args)
        if stdout_path is not None:
            stderr_path.write_text("Logged in using ChatGPT", encoding="utf-8")
            return 0
        Path(args[args.index("--output-last-message") + 1]).write_text(
            '{"ok": true}', encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(llm, "run_child", fake_child)
    assert llm.run_codex_json("prompt", EMPTY_OBJECT_SCHEMA) == {"ok": True}
    assert len(calls) == 2


def test_nonzero_quota_response_is_classified_without_leaking_stderr(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: "codex.cmd")

    def fake_child(args, *, stdin_text, cwd, timeout_s, env, stderr_path, stdout_path=None):
        if stdout_path is not None:
            stdout_path.write_text("Logged in using ChatGPT", encoding="utf-8")
            return 0
        stderr_path.write_text("429 usage limit synthetic-private-detail", encoding="utf-8")
        return 1

    monkeypatch.setattr(llm, "run_child", fake_child)
    with pytest.raises(ModelAccessError) as error:
        llm.run_codex_json("prompt", EMPTY_OBJECT_SCHEMA)
    assert "synthetic-private-detail" not in str(error.value)


def test_success_without_json_output_is_repairable(monkeypatch, tmp_path):
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: "codex.cmd")

    def fake_child(args, *, stdin_text, cwd, timeout_s, env, stderr_path, stdout_path=None):
        if stdout_path is not None:
            stdout_path.write_text("Logged in using ChatGPT", encoding="utf-8")
            return 0
        Path(args[args.index("--output-last-message") + 1]).write_text("not json", encoding="utf-8")
        return 0

    monkeypatch.setattr(llm, "run_child", fake_child)
    with pytest.raises(InvalidModelOutputError):
        llm.run_codex_json("prompt", EMPTY_OBJECT_SCHEMA)


def test_reportdraft_json_schema_is_strict_for_every_object():
    schema = ReportDraft.model_json_schema()
    llm._validate_strict_schema(schema)


def test_draft_preview_precedes_verification_and_repair_is_explicit(monkeypatch, collection, selected_reviews, valid_draft):
    wrong = valid_draft.model_copy(deep=True)
    wrong.themes[0].evidence[0].quote = "Такого текста в отзыве нет"
    answers = iter([wrong.model_dump(mode="json"), valid_draft.model_dump(mode="json")])
    events = []
    monkeypatch.setattr(llm, "run_codex_json", lambda *args, **kwargs: next(answers))
    result = llm.analyze_reviews(collection, selected_reviews, insight=events.append)
    assert result == valid_draft
    assert [event.stage for event in events] == ["checking", "repair", "checking", "verified"]
    assert events[0].draft.themes[0].evidence[0].quote == "Такого текста в отзыве нет"
    assert events[1].issue
    assert events[-1].draft == valid_draft


def test_malformed_response_preview_does_not_invent_a_draft(monkeypatch, collection, selected_reviews, valid_draft):
    answers = iter([{"unexpected": "field"}, valid_draft.model_dump(mode="json")])
    monkeypatch.setattr(llm, "run_codex_json", lambda *args, **kwargs: next(answers))
    events = []
    llm.analyze_reviews(collection, selected_reviews, insight=events.append)
    repair = next(event for event in events if event.stage == "repair")
    assert repair.draft is None
    assert repair.issue


def test_preview_explains_internal_reference_errors_in_plain_language_but_keeps_model_feedback(monkeypatch, collection, selected_reviews, valid_draft):
    wrong = valid_draft.model_copy(deep=True)
    wrong.themes[0].theme_id = "taste"
    answers = iter([wrong.model_dump(mode="json"), valid_draft.model_dump(mode="json")])
    prompts, events = [], []

    def answer(prompt, *args, **kwargs):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(llm, "run_codex_json", answer)
    llm.analyze_reviews(collection, selected_reviews, insight=events.append)
    repair = next(event for event in events if event.stage == "repair")
    assert "ID" not in repair.issue and "T01" not in repair.issue
    assert "обозначения" in repair.issue
    assert "T01" in prompts[1]
