"""Make one bounded, metadata-only browser diagnostic for a Maps card."""

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from review_insights.browser_runtime import BrowserMode, open_review_context
from review_insights.collector import _CARD_SELECTOR, _page_is_challenge
from review_insights.models import UrlError
from review_insights.urls import is_same_org_url, normalize_org_url


_TOTAL_TIMEOUT_S = 30.0
_NAVIGATION_TIMEOUT_MS = 15_000
_CARD_WAIT_TIMEOUT_MS = 10_000
_DATE_SELECTOR = f"{_CARD_SELECTOR} meta[itemprop='datePublished']"


def _diagnostic_record(mode: str) -> dict:
    return {
        "mode": mode,
        "browser_version": None,
        "main_document_status": None,
        "final_host": None,
        "same_org": False,
        "retry_after": None,
        "card_count": 0,
        "date_count": 0,
        "duration_seconds": 0.0,
        "challenge_detected": False,
    }


def _remaining_ms(deadline: float, maximum: int) -> int:
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        raise PlaywrightTimeoutError("Diagnostic deadline reached")
    return min(maximum, remaining)


def diagnose(url: str, mode: BrowserMode, project_root: Path) -> tuple[dict, int]:
    """Open one organization document and return safe metadata only."""
    started = time.monotonic()
    deadline = started + _TOTAL_TIMEOUT_S
    record = _diagnostic_record(mode)
    exit_code = 1

    try:
        organization = normalize_org_url(url)
    except UrlError:
        record["duration_seconds"] = round(time.monotonic() - started, 2)
        return record, exit_code

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    try:
        with sync_playwright() as playwright:
            with open_review_context(
                playwright, mode=mode, project_root=project_root
            ) as context:
                page = context.new_page()
                try:
                    response = page.goto(
                        organization.url,
                        wait_until="domcontentloaded",
                        timeout=_remaining_ms(deadline, _NAVIGATION_TIMEOUT_MS),
                    )
                    if response is not None:
                        record["main_document_status"] = response.status
                        try:
                            record["retry_after"] = response.header_value("retry-after")
                        except Exception:
                            record["retry_after"] = None

                    record["final_host"] = urlsplit(page.url).hostname
                    record["same_org"] = is_same_org_url(page.url, organization.org_id)
                    browser = context.browser
                    if browser is not None:
                        record["browser_version"] = browser.version

                    if record["same_org"] and record["main_document_status"] is not None:
                        record["challenge_detected"] = _page_is_challenge(page)

                    status = record["main_document_status"]
                    if (
                        record["same_org"]
                        and not record["challenge_detected"]
                        and (status is None or status < 400)
                    ):
                        try:
                            page.locator(_CARD_SELECTOR).first.wait_for(
                                state="attached",
                                timeout=_remaining_ms(deadline, _CARD_WAIT_TIMEOUT_MS),
                            )
                        except PlaywrightTimeoutError:
                            pass

                        if time.monotonic() < deadline:
                            record["card_count"] = page.locator(_CARD_SELECTOR).count()
                            record["date_count"] = page.locator(_DATE_SELECTOR).count()
                    exit_code = 0
                except PlaywrightTimeoutError:
                    # Keep the diagnostic bounded and do not print exception text.
                    record["final_host"] = urlsplit(page.url).hostname
                    record["same_org"] = is_same_org_url(page.url, organization.org_id)
                    exit_code = 1
    except Exception:
        # Browser setup and navigation errors can contain URLs or response data.
        # The user needs the safe result fields, never the raw exception text.
        exit_code = 1
    finally:
        record["duration_seconds"] = round(time.monotonic() - started, 2)

    return record, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Open one Yandex Maps organization card and print safe diagnostics."
    )
    parser.add_argument("--url", required=True, help="Full organization URL")
    parser.add_argument(
        "--mode", required=True, choices=("headed", "persistent")
    )
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]
    record, exit_code = diagnose(args.url, args.mode, project_root)
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
