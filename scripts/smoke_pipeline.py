"""Run the complete pipeline once and save a safe local demonstration report."""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from review_insights.models import (
    CollectionError,
    EvidenceValidationError,
    ModelAccessError,
    ModelResponseError,
    ModelTimeoutError,
    UrlError,
)
from review_insights.reporting import render_markdown
from review_insights.service import run_pipeline
from review_insights.urls import normalize_org_url


_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _safe_error_message(error: Exception) -> str:
    if isinstance(error, UrlError):
        return str(error)
    if isinstance(error, CollectionError):
        return str(error)
    if isinstance(error, ModelAccessError):
        return str(error)
    if isinstance(error, ModelTimeoutError):
        return "Операция превысила лимит времени."
    if isinstance(error, (ModelResponseError, EvidenceValidationError)):
        return str(error)
    return "Внутренняя ошибка; подробности не содержат текст отзывов."


def _artifact_directory() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    configured = Path(os.environ.get("ARTIFACTS_DIR", "artifacts")).expanduser()
    if not configured.is_absolute():
        configured = project_root / configured
    return configured.resolve()


def _write_outputs(artifacts: Path, prefix: str, bundle) -> tuple[Path, Path]:
    artifacts.mkdir(parents=True, exist_ok=True)
    json_path = artifacts / f"{prefix}.json"
    markdown_path = artifacts / f"{prefix}.md"
    json_text = bundle.model_dump_json(indent=2)
    markdown_text = render_markdown(bundle)

    with tempfile.TemporaryDirectory(prefix=".smoke-", dir=artifacts) as temporary:
        staging = Path(temporary)
        staged_json = staging / json_path.name
        staged_markdown = staging / markdown_path.name
        staged_json.write_text(json_text, encoding="utf-8")
        staged_markdown.write_text(markdown_text, encoding="utf-8")
        os.replace(staged_json, json_path)
        os.replace(staged_markdown, markdown_path)
    return json_path, markdown_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one review analysis and save its report.")
    parser.add_argument("--url", required=True, help="Полная ссылка организации в Яндекс Картах")
    parser.add_argument(
        "--out-prefix",
        help="Имя файлов отчёта без пути и расширения; по умолчанию добавляется дата и время",
    )
    args = parser.parse_args(argv)

    try:
        org = normalize_org_url(args.url)
        prefix = args.out_prefix or (
            "smoke-"
            + org.org_id
            + "-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
        if not _PREFIX.fullmatch(prefix):
            parser.error("--out-prefix должен быть коротким именем файла без пути.")

        stages: list[str] = []
        started = perf_counter()
        bundle = run_pipeline(org.url, progress=stages.append)
        duration_seconds = perf_counter() - started
        json_path, markdown_path = _write_outputs(
            _artifact_directory(), prefix, bundle
        )
        result = {
            "status": "ok",
            "org_id": bundle.collection.org.org_id,
            "collected_count": len(bundle.collection.reviews),
            "analyzed_count": len(bundle.analyzed_reviews),
            "theme_count": len(bundle.draft.themes),
            "stages": stages,
            "duration_seconds": round(duration_seconds, 2),
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except SystemExit:
        raise
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "message": _safe_error_message(error),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
