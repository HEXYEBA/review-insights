"""Validate and store small local report bundles by organization ID."""

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from review_insights.models import EvidenceValidationError, ReportBundle
from review_insights.reporting import render_markdown


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "artifacts" / "reports"
MAX_REPORT_BYTES = 5 * 1024 * 1024
_ORG_ID = re.compile(r"^[0-9]{1,20}$")
_FRESH_FOR = timedelta(hours=6)


def _report_path(org_id: str, root: Path | None) -> Path | None:
    if not isinstance(org_id, str) or not _ORG_ID.fullmatch(org_id):
        return None
    directory = Path(root) if root is not None else REPORTS_DIR
    return directory / f"{org_id}.json"


def save_report(bundle: ReportBundle, *, root: Path | None = None) -> Path:
    """Validate a bundle and atomically save its JSON under its numeric org ID."""
    org_id = bundle.collection.org.org_id
    target = _report_path(org_id, root)
    if target is None:
        raise ValueError("Report organization ID must contain 1 to 20 digits")

    render_markdown(bundle)
    payload = bundle.model_dump_json(indent=2).encode("utf-8")
    if len(payload) > MAX_REPORT_BYTES:
        raise ValueError("Report is larger than the 5 MiB storage limit")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return target


def load_saved_report(
    org_id: str, *, root: Path | None = None
) -> ReportBundle | None:
    """Load and revalidate one saved report without contacting external services."""
    target = _report_path(org_id, root)
    if target is None:
        return None

    try:
        if target.is_symlink() or target.stat().st_size > MAX_REPORT_BYTES:
            return None
        with target.open("rb") as source:
            payload = source.read(MAX_REPORT_BYTES + 1)
        if len(payload) > MAX_REPORT_BYTES:
            return None

        bundle = ReportBundle.model_validate_json(payload)
        if bundle.collection.org.org_id != org_id:
            return None
        render_markdown(bundle)
    except (OSError, UnicodeDecodeError, ValidationError, ValueError, TypeError, EvidenceValidationError):
        return None
    return bundle


def is_fresh_report(bundle: ReportBundle, now: datetime) -> bool:
    """Reports remain the default for six hours after their source collection."""
    collected_at = bundle.collection.collected_at
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or collected_at.tzinfo is None
        or collected_at.utcoffset() is None
    ):
        return False

    age = now.astimezone(timezone.utc) - collected_at.astimezone(timezone.utc)
    return timedelta(0) <= age <= _FRESH_FOR
