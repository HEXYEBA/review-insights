from pathlib import Path

import pytest

from review_insights.browser_runtime import open_review_context


class FakeContext:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.context_options = None
        self.closed = False

    def new_context(self, **options):
        self.context_options = options
        return self.context

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self):
        self.context = FakeContext()
        self.browser = FakeBrowser(self.context)
        self.launch_options = None
        self.persistent_options = None

    def launch(self, **options):
        self.launch_options = options
        return self.browser

    def launch_persistent_context(self, user_data_dir, **options):
        self.persistent_options = (Path(user_data_dir), options)
        return self.context


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()


def test_headed_context_uses_new_visible_browser_and_closes_it(tmp_path):
    playwright = FakePlaywright()

    with open_review_context(playwright, mode="headed", project_root=tmp_path) as context:
        assert context is playwright.chromium.context
        assert playwright.chromium.launch_options == {"headless": False}
        assert playwright.chromium.browser.context_options == {
            "locale": "ru-RU",
            "timezone_id": "Europe/Moscow",
            "viewport": {"width": 1440, "height": 1000},
        }

    assert playwright.chromium.context.closed
    assert playwright.chromium.browser.closed


def test_persistent_context_uses_only_project_profile_and_closes_after_error(tmp_path):
    playwright = FakePlaywright()

    with pytest.raises(RuntimeError, match="stop"):
        with open_review_context(playwright, mode="persistent", project_root=tmp_path):
            raise RuntimeError("stop")

    profile, options = playwright.chromium.persistent_options
    assert profile == (tmp_path / ".browser-profile" / "yandex").resolve()
    assert options == {
        "headless": False,
        "locale": "ru-RU",
        "timezone_id": "Europe/Moscow",
        "viewport": {"width": 1440, "height": 1000},
    }
    assert playwright.chromium.context.closed
    assert not playwright.chromium.browser.closed


def test_unknown_browser_mode_is_rejected_before_launch(tmp_path):
    playwright = FakePlaywright()

    with pytest.raises(ValueError, match="Unsupported browser mode"):
        with open_review_context(playwright, mode="stealth", project_root=tmp_path):
            pass

    assert playwright.chromium.launch_options is None
    assert playwright.chromium.persistent_options is None
