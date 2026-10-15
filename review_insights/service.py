"""Parent-side process boundary for review collection and later analysis."""

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable

from review_insights import report_store
from review_insights.config import (
    COLLECT_PARENT_TIMEOUT_S,
    COLLECT_TIMEOUT_S,
    DEFAULT_MODEL,
    MAX_ANALYSIS_REVIEWS,
    MAX_REVIEWS,
    MIN_REVIEWS_FOR_NORMAL_REPORT,
)
from review_insights.llm import analyze_reviews
from review_insights.models import (
    AnalysisPreview,
    CollectionError,
    CollectionResult,
    ModelTimeoutError,
    OrgRef,
    ReportBundle,
)
from review_insights.processes import run_child
from review_insights.reporting import validate_evidence
from review_insights.selection import select_analysis_reviews
from review_insights.urls import normalize_org_url


def _collection_environment() -> dict[str, str]:
    child_env = os.environ.copy()
    for name in list(child_env):
        upper = name.upper()
        if (
            upper.endswith("_API_KEY")
            or upper.endswith("_BASE_URL")
            or upper.endswith("_API_BASE")
            or upper in {"OPENAI_API_BASE", "OPENAI_BASE_URL"}
        ):
            child_env.pop(name, None)
    child_env["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    return child_env


def _read_result(result_path: Path) -> CollectionResult | None:
    try:
        result = CollectionResult.model_validate_json(
            result_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return result


def _partial_after_stop(result: CollectionResult, reason: str, warning: str) -> CollectionResult:
    return result.model_copy(
        update={
            "stop_reason": reason,
            "warnings": [*result.warnings, warning],
        }
    )


def _no_reviews_message(result: CollectionResult) -> str:
    messages = {
        "blocked": "Яндекс Карты запросили проверку посетителя; соберите отзывы позже.",
        "rate_limited": "Яндекс Карты временно ограничили загрузку страницы (HTTP 429). Повторите позже.",
        "exhausted": "В карточке не найдено доступных текстовых отзывов.",
        "layout_changed": "Не удалось прочитать отзывы на этой странице Яндекс Карт.",
        "load_error": "Не удалось загрузить отзывы с Яндекс Карт.",
        "deadline": "Сбор отзывов превысил лимит времени и не успел получить данные.",
        "no_new_reviews": "Не удалось получить текстовые отзывы из этой карточки.",
    }
    return messages.get(result.stop_reason, "Сбор не вернул текстовых отзывов.")


def _save_collection_diagnostic(stderr_path: Path, org: OrgRef, exit_code: int, count: int) -> None:
    """Retain safe worker locations after its private temporary files are removed."""
    record = {
        "at": datetime.now(timezone.utc).isoformat(), "org_id": org.org_id,
        "exit_code": exit_code, "review_count": count,
        "error_type": "WorkerExitError", "frames": [],
    }
    try:
        with stderr_path.open(encoding="utf-8", errors="replace") as source:
            lines = source.read(64_000).splitlines()
        for line in lines:
            if not line.startswith("COLLECTOR_FAILURE "):
                continue
            try:
                data = json.loads(line.removeprefix("COLLECTOR_FAILURE "))
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            error_type = data.get("error_type")
            if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", error_type):
                record["error_type"] = error_type
            frames = data.get("frames", [])
            for frame in frames[:12] if isinstance(frames, list) else []:
                if not isinstance(frame, dict):
                    continue
                name, number, function = frame.get("file"), frame.get("line"), frame.get("function")
                if (isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}\.py", name)
                    and type(number) is int and 0 < number < 1_000_000
                    and isinstance(function, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}|<lambda>", function)):
                    record["frames"].append({"file": name, "line": number, "function": function})
    except OSError:
        pass
    artifacts = Path(os.environ.get("ARTIFACTS_DIR", "artifacts"))
    if not artifacts.is_absolute():
        artifacts = Path(__file__).resolve().parents[1] / artifacts
    try:
        artifacts.mkdir(parents=True, exist_ok=True)
        with (artifacts / "collection-errors.jsonl").open("a", encoding="utf-8") as target:
            target.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Diagnostics must not replace the collection result or source error.


def run_collection(
    org: OrgRef, *, preview: Callable[[CollectionResult], None] | None = None
) -> CollectionResult:
    """Run the browser worker in a bounded child process with private snapshots."""
    project_root = Path(__file__).resolve().parents[1]
    # Windows descendants can briefly hold inherited stderr after worker shutdown.
    # Best-effort teardown must not replace a valid snapshot or the source error.
    with tempfile.TemporaryDirectory(
        prefix="review-insights-", ignore_cleanup_errors=True
    ) as directory:
        private_dir = Path(directory)
        result_path = private_dir / "collection.json"
        stderr_path = private_dir / "worker.stderr"
        request = {
            "url": org.url,
            "limit": MAX_REVIEWS,
            "timeout_s": COLLECT_TIMEOUT_S,
            "result_path": str(result_path),
        }
        last_count = -1

        def publish_snapshot() -> None:
            nonlocal last_count
            snapshot = _read_result(result_path)
            if (snapshot is not None and snapshot.org.org_id == org.org_id
                    and len(snapshot.reviews) > last_count):
                last_count = len(snapshot.reviews)
                preview(snapshot)

        try:
            exit_code = run_child(
                [sys.executable, "-m", "review_insights.collect_worker"],
                stdin_text=json.dumps(request, ensure_ascii=False),
                cwd=project_root,
                timeout_s=COLLECT_PARENT_TIMEOUT_S,
                env=_collection_environment(),
                stderr_path=stderr_path,
                **({"on_tick": publish_snapshot} if preview is not None else {}),
            )
        except ModelTimeoutError as exc:
            result = _read_result(result_path)
            if result is None:
                raise CollectionError(
                    "Сбор отзывов превысил лимит времени и не успел получить данные."
                ) from exc
            if not result.reviews:
                raise CollectionError(_no_reviews_message(result)) from exc
            result = _partial_after_stop(
                result,
                "deadline",
                "Сбор остановлен по внешнему лимиту времени; показаны сохранённые отзывы.",
            )
        except OSError as exc:
            raise CollectionError("Не удалось запустить процесс сбора отзывов.") from exc
        else:
            result = _read_result(result_path)
            if exit_code != 0:
                _save_collection_diagnostic(stderr_path, org, exit_code, len(result.reviews) if result else 0)
                if result is None or not result.reviews:
                    raise CollectionError("Процесс сбора отзывов завершился с ошибкой.")
                result = _partial_after_stop(
                    result,
                    "load_error",
                    "Сбор прервался; показаны отзывы, сохранённые до остановки.",
                )
            elif result is None:
                raise CollectionError("Процесс сбора не вернул проверяемый результат.")

        if result.org.org_id != org.org_id:
            raise CollectionError("Процесс сбора вернул данные другой организации.")
        if not result.reviews:
            raise CollectionError(_no_reviews_message(result))
        return result


def _review_word(count: int) -> str:
    last_two = count % 100
    last = count % 10
    if 11 <= last_two <= 14:
        return "отзывов"
    if last == 1:
        return "отзыв"
    if 2 <= last <= 4:
        return "отзыва"
    return "отзывов"


def run_pipeline(
    url: str, progress: Callable[[str], None] | None = None,
    *, preview: Callable[[CollectionResult], None] | None = None,
    insight: Callable[[AnalysisPreview], None] | None = None,
) -> ReportBundle:
    """Collect, select, analyze, validate, and package one organization's report."""
    org = normalize_org_url(url)
    if progress is not None:
        progress("Собираем отзывы…")
    collection = run_collection(org, **({"preview": preview} if preview is not None else {}))
    if preview is not None:
        preview(collection)
    if not collection.reviews:
        raise CollectionError("Не удалось получить текстовые отзывы для анализа.")

    eligible_reviews = select_analysis_reviews(collection.reviews)
    selected = eligible_reviews[:MAX_ANALYSIS_REVIEWS]
    if not selected:
        raise CollectionError(
            "После отбора не осталось целых отзывов в пределах лимита текста."
        )

    warnings = list(collection.warnings)
    if len(collection.reviews) != len(selected):
        warnings.append(
            f"Собрано {len(collection.reviews)}, проанализировано {len(selected)}"
        )
    if len(eligible_reviews) > MAX_ANALYSIS_REVIEWS:
        warnings.append(
            f"Лимит одного анализа — {MAX_ANALYSIS_REVIEWS} отзывов; отобраны самые новые по известным датам."
        )
    if len(selected) < MIN_REVIEWS_FOR_NORMAL_REPORT:
        warnings.append(
            f"Малая выборка: проанализировано {len(selected)} отзывов; выводы могут быть неполными."
        )
    unknown_dates = sum(review.published_at is None for review in selected)
    if unknown_dates:
        warnings.append(
            f"У {unknown_dates} анализируемых отзывов неизвестна дата публикации."
        )

    if progress is not None:
        count = len(selected)
        progress(f"Анализируем {count} {_review_word(count)}…")
    analysis_started_at = datetime.now(timezone.utc)
    started = perf_counter()
    if insight is not None:
        insight(AnalysisPreview(stage="analysis", selected_reviews=selected))
    draft = analyze_reviews(collection, selected, progress=progress,
                            **({"insight": insight} if insight is not None else {}))
    analysis_seconds = perf_counter() - started
    validate_evidence(draft, selected)

    return ReportBundle(
        collection=collection,
        analyzed_reviews=selected,
        model_name=os.environ.get("MODEL_NAME", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        analysis_started_at=analysis_started_at,
        analysis_seconds=analysis_seconds,
        draft=draft,
        warnings=warnings,
    )


def get_report(
    url: str,
    *,
    refresh: bool = False,
    progress: Callable[[str], None] | None = None,
    preview: Callable[[CollectionResult], None] | None = None,
    insight: Callable[[AnalysisPreview], None] | None = None,
) -> tuple[ReportBundle, bool]:
    """Use a fresh local report unless the caller explicitly requests an update."""
    org = normalize_org_url(url)
    if not refresh:
        saved = report_store.load_saved_report(org.org_id)
        if saved is not None and report_store.is_fresh_report(
            saved, datetime.now(timezone.utc)
        ):
            return saved, True

    bundle = run_pipeline(
        org.url, progress=progress, **({"preview": preview} if preview is not None else {}),
        **({"insight": insight} if insight is not None else {})
    )
    report_store.save_report(bundle)
    return bundle, False
