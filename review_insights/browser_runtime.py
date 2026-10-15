"""Owned browser contexts for bounded public-page diagnostics and collection."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal

from playwright.sync_api import Browser, BrowserContext, Playwright


BrowserMode = Literal["headed", "persistent"]


@contextmanager
def open_review_context(
    pw: Playwright, *, mode: BrowserMode, project_root: Path
) -> Iterator[BrowserContext]:
    """Open an isolated visible browser context owned by this project."""
    if mode not in {"headed", "persistent"}:
        raise ValueError("Unsupported browser mode")

    root = project_root.resolve()
    browser: Browser | None = None
    context: BrowserContext | None = None
    options = {
        "locale": "ru-RU",
        "timezone_id": "Europe/Moscow",
        "viewport": {"width": 1440, "height": 1000},
    }

    try:
        if mode == "headed":
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context(**options)
        else:
            profile = root / ".browser-profile" / "yandex"
            profile.mkdir(parents=True, exist_ok=True)
            context = pw.chromium.launch_persistent_context(
                str(profile), headless=False, **options
            )
        yield context
    finally:
        try:
            if context is not None:
                context.close()
        finally:
            if browser is not None:
                browser.close()
