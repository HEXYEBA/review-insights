"""Use an already authenticated Codex CLI to return grounded JSON reports."""

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from review_insights.config import (
    DEFAULT_MODEL,
    LLM_REASONING_EFFORT,
    LLM_TIMEOUT_S,
    MAX_REPAIR_ATTEMPTS,
)
from review_insights.models import (
    AnalysisPreview,
    CollectionResult,
    EvidenceValidationError,
    InvalidModelOutputError,
    ModelAccessError,
    ModelResponseError,
    ModelTimeoutError,
    ReportDraft,
    Review,
)
from review_insights.processes import run_child
from review_insights.prompts import build_prompt
from review_insights.reporting import validate_evidence


_API_ENV_NAMES = {
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "AZURE_OPENAI_API_KEY",
}


def _validate_strict_schema(value: Any, path: str = "$schema") -> None:
    """Require closed objects and a required entry for every JSON property."""
    if isinstance(value, dict):
        if value.get("type") == "object":
            properties = value.get("properties", {})
            required = value.get("required", [])
            if value.get("additionalProperties") is not False:
                raise ModelResponseError(f"JSON schema is not closed at {path}.")
            if set(required) != set(properties):
                raise ModelResponseError(f"JSON schema has optional fields at {path}.")
        for key, nested in value.items():
            _validate_strict_schema(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _validate_strict_schema(nested, f"{path}[{index}]")


def _find_codex_binary() -> str:
    configured = os.environ.get("CODEX_BIN", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return str(configured_path)
        discovered = shutil.which(configured)
        if discovered:
            return discovered
        raise ModelAccessError("Не найден исполняемый файл Codex CLI.")

    for candidate in ("codex.cmd", "codex.exe", "codex"):
        discovered = shutil.which(candidate)
        if discovered:
            return discovered
    raise ModelAccessError("Не найден Codex CLI. Установите его и выполните вход через ChatGPT.")


def _codex_environment() -> dict[str, str]:
    child_env = os.environ.copy()
    for name in list(child_env):
        if name.upper() in _API_ENV_NAMES:
            child_env.pop(name, None)
    return child_env


def _classify_cli_failure(stderr_path: Path) -> Exception:
    try:
        details = stderr_path.read_text(encoding="utf-8", errors="replace").casefold()
    except OSError:
        details = ""
    if any(fragment in details for fragment in ("not logged in", "authentication", "unauthorized", "401", "403")):
        return ModelAccessError("Codex не подтвердил вход через подписку ChatGPT.")
    if any(fragment in details for fragment in ("usage limit", "rate limit", "quota", "429")):
        return ModelAccessError("Лимит подписки ChatGPT исчерпан; попробуйте позже.")
    return ModelResponseError("Codex завершил анализ с ошибкой.")


def run_codex_json(prompt: str, schema: dict, timeout_s: int = LLM_TIMEOUT_S) -> dict:
    """Run one isolated Codex CLI request and parse only its final JSON file."""
    _validate_strict_schema(schema)
    codex_bin = _find_codex_binary()
    model_name = os.environ.get("MODEL_NAME", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    child_env = _codex_environment()

    with tempfile.TemporaryDirectory(prefix="review-insights-codex-") as directory:
        private_root = Path(directory)
        work_dir = private_root / "work"
        work_dir.mkdir()
        schema_path = private_root / "schema.json"
        output_path = private_root / "response.json"
        auth_status_path = private_root / "auth_status.txt"
        auth_stderr_path = private_root / "auth.stderr"
        stderr_path = private_root / "codex.stderr"
        schema_path.write_text(
            json.dumps(schema, ensure_ascii=False), encoding="utf-8"
        )

        try:
            auth_exit_code = run_child(
                [codex_bin, "login", "status"],
                stdin_text="",
                cwd=work_dir,
                timeout_s=10,
                env=child_env,
                stderr_path=auth_stderr_path,
                stdout_path=auth_status_path,
            )
        except ModelTimeoutError as exc:
            raise ModelAccessError(
                "Проверка входа Codex не завершилась за 10 секунд. "
                "Проверьте вход в Codex через ChatGPT."
            ) from exc
        auth_marker_found = False
        for status_path in (auth_status_path, auth_stderr_path):
            try:
                status_text = status_path.read_text(
                    encoding="utf-8", errors="replace"
                ).casefold()
            except OSError:
                status_text = ""
            auth_marker_found = auth_marker_found or (
                "logged in using chatgpt" in status_text
            )
        if auth_exit_code != 0 or not auth_marker_found:
            raise ModelAccessError("Нужен вход Codex через подписку ChatGPT.")

        args = [
            codex_bin,
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--disable",
            "shell_tool",
            "--disable",
            "apps",
            "--disable",
            "hooks",
            "--disable",
            "multi_agent",
            "--disable",
            "plugins",
            "--disable",
            "remote_plugin",
            "--disable",
            "view_image",
            "--disable",
            "image_generation",
            "--disable",
            "skill_search",
            "--config",
            'web_search="disabled"',
            "--config",
            f'model_reasoning_effort="{LLM_REASONING_EFFORT}"',
            "--color",
            "never",
            "--model",
            model_name,
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--cd",
            str(work_dir),
            "-",
        ]
        exit_code = run_child(
            args,
            stdin_text=prompt,
            cwd=work_dir,
            timeout_s=timeout_s,
            env=child_env,
            stderr_path=stderr_path,
        )
        if exit_code != 0:
            raise _classify_cli_failure(stderr_path)

        try:
            raw_output = output_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InvalidModelOutputError(
                "Codex завершил запрос, но не создал JSON-ответ."
            ) from exc
        try:
            response = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise InvalidModelOutputError(
                "Ответ Codex не является корректным JSON."
            ) from exc
        if not isinstance(response, dict):
            raise InvalidModelOutputError("Ответ Codex должен быть JSON-объектом.")
        return response


def _validation_feedback(error: Exception) -> str:
    if isinstance(error, EvidenceValidationError):
        return str(error)
    if isinstance(error, InvalidModelOutputError):
        return str(error)
    if isinstance(error, ValidationError):
        issues = []
        for item in error.errors()[:8]:
            location = ".".join(str(part) for part in item.get("loc", ()))
            issues.append(f"{location}: {item.get('msg', 'неверное поле')}")
        return "Ответ не прошёл JSON-схему. Исправь ошибки полей: " + "; ".join(issues)
    return "Ответ не прошёл проверку; верни полный корректный JSON по схеме."


def analyze_reviews(
    collection: CollectionResult,
    selected: list[Review],
    *,
    progress: Callable[[str], None] | None = None,
    insight: Callable[[AnalysisPreview], None] | None = None,
) -> ReportDraft:
    """Generate one evidence-checked draft and allow one format-only repair."""
    schema = ReportDraft.model_json_schema()
    feedback: str | None = None
    previous_draft: ReportDraft | None = None

    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        if attempt and progress is not None:
            progress(
                f"Модель исправляет отчёт после проверки (до {LLM_TIMEOUT_S} секунд)…"
            )
        prompt = build_prompt(
            collection,
            selected,
            repair_feedback=feedback,
            previous_draft=previous_draft,
        )
        draft: ReportDraft | None = None
        try:
            payload = run_codex_json(prompt, schema, timeout_s=LLM_TIMEOUT_S)
            if progress is not None:
                progress("Проверяем цитаты и ссылки…")
            draft = ReportDraft.model_validate(payload)
            if insight is not None:
                insight(AnalysisPreview(stage="checking", selected_reviews=selected, draft=draft))
            validate_evidence(draft, selected)
            if insight is not None:
                insight(AnalysisPreview(stage="verified", selected_reviews=selected, draft=draft))
            return draft
        except (InvalidModelOutputError, ValidationError, EvidenceValidationError) as exc:
            if attempt >= MAX_REPAIR_ATTEMPTS:
                raise ModelResponseError(
                    "Отчёт не прошёл проверку даже после одного исправления."
                ) from exc
            feedback = _validation_feedback(exc)
            previous_draft = draft
            if insight is not None:
                issue = str(exc) if isinstance(exc, EvidenceValidationError) else "Ответ модели пока не соответствует структуре отчёта."
                if "ID" in issue or "T01" in issue:
                    issue = "Нужно привести обозначения тем и отзывов в черновике к единому виду."
                insight(AnalysisPreview(stage="repair", selected_reviews=selected, draft=draft, issue=issue))

    raise ModelResponseError("Не удалось сформировать проверяемый отчёт.")
