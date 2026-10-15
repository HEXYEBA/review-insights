"""Private command-line entry point for the bounded browser collector."""

import os

# Use this project's Playwright package cache and ignore a parent shell's
# browser path, which may belong to another local project.
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from review_insights.collector import collect_reviews
from review_insights.models import CollectionError, CollectionResult, OrgRef
from review_insights.urls import normalize_org_url


def _write_snapshot(result_path: Path, result: CollectionResult) -> None:
    temporary_path = result_path.with_name(
        f".{result_path.name}.{os.getpid()}.tmp"
    )
    try:
        temporary_path.write_text(result.model_dump_json(), encoding="utf-8")
        # Windows cannot replace a file while the parent's live preview reads it.
        # Retry only this short local sharing conflict; do not restart collection.
        for attempt in range(6):
            try:
                os.replace(temporary_path, result_path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (2 ** attempt))
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _read_snapshot(result_path: Path) -> CollectionResult | None:
    try:
        return CollectionResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _empty_error_result(org: OrgRef) -> CollectionResult:
    return CollectionResult(
        org=org,
        org_name="",
        collected_at=datetime.now(timezone.utc),
        source_review_count=None,
        source_rating_count=None,
        source_rating=None,
        reviews=[],
        stop_reason="blocked",
        sort_mode="default",
        warnings=["Не удалось подтвердить, что открыта карточка выбранной организации."],
    )


def _parse_request(raw: str) -> tuple[OrgRef, int, int, Path]:
    payload: Any = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {
        "url",
        "limit",
        "timeout_s",
        "result_path",
    }:
        raise ValueError("invalid request shape")
    if not isinstance(payload["url"], str):
        raise ValueError("invalid url")
    if isinstance(payload["limit"], bool) or not isinstance(payload["limit"], int):
        raise ValueError("invalid limit")
    if isinstance(payload["timeout_s"], bool) or not isinstance(payload["timeout_s"], int):
        raise ValueError("invalid timeout")
    if not isinstance(payload["result_path"], str):
        raise ValueError("invalid result path")

    org = normalize_org_url(payload["url"])
    result_path = Path(payload["result_path"])
    if not result_path.is_absolute() or not result_path.parent.is_dir():
        raise ValueError("result path must be inside an existing private directory")
    if not 1 <= payload["limit"] <= 200 or not 1 <= payload["timeout_s"] <= 60:
        raise ValueError("limits outside supported bounds")
    return org, payload["limit"], payload["timeout_s"], result_path


def main() -> int:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    try:
        org, limit, timeout_s, result_path = _parse_request(sys.stdin.read())
    except Exception:
        sys.stderr.write("Invalid collector request.\n")
        return 2

    try:
        try:
            result = collect_reviews(
                org,
                limit=limit,
                timeout_s=timeout_s,
                on_snapshot=lambda snapshot: _write_snapshot(result_path, snapshot),
            )
        except CollectionError:
            result = _read_snapshot(result_path)
            if result is None:
                result = _empty_error_result(org)
            elif result.reviews:
                result = result.model_copy(
                    update={
                        "stop_reason": "load_error",
                        "warnings": [*result.warnings, "Сбор прерван из-за ошибки загрузки страницы."],
                    }
                )
        _write_snapshot(result_path, result)
    except Exception as exc:
        # Keep locations for diagnosis, never exception messages or source lines.
        diagnostic = {
            "error_type": type(exc).__name__,
            "frames": [
                {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(exc.__traceback__)[-12:]
            ],
        }
        sys.stderr.write("COLLECTOR_FAILURE " + json.dumps(diagnostic) + "\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
