"""Collect a real organization into a local ignored JSON artifact."""

import argparse
import sys
from pathlib import Path
from time import perf_counter

from review_insights.models import CollectionError, UrlError
from review_insights.service import run_collection
from review_insights.urls import normalize_org_url


def _output_path(artifacts_dir: Path, requested: str) -> Path:
    root = artifacts_dir.resolve()
    output = (root / requested).resolve()
    if output == root or root not in output.parents:
        raise ValueError("--out must name a file inside the artifacts directory")
    if output.suffix.lower() != ".json":
        raise ValueError("--out must use the .json extension")
    return output


def main(argv: list[str] | None = None, *, artifacts_dir: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect public Yandex Maps reviews.")
    parser.add_argument("--url", required=True, help="Full organization URL from Yandex Maps")
    parser.add_argument("--out", required=True, help="JSON filename inside artifacts/")
    args = parser.parse_args(argv)

    root = artifacts_dir or Path(__file__).resolve().parents[1] / "artifacts"
    try:
        org = normalize_org_url(args.url)
        output = _output_path(root, args.out)
    except (UrlError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    try:
        result = run_collection(org)
    except CollectionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    duration = perf_counter() - started
    output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"org_name: {result.org_name}")
    print(f"collected_count: {len(result.reviews)}")
    print(f"stop_reason: {result.stop_reason}")
    print(f"sort_mode: {result.sort_mode}")
    print(f"duration_seconds: {duration:.1f}")
    print(f"saved_to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
