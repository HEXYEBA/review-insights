from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import time

from playwright.sync_api import Error as PlaywrightError

from review_insights.models import OrgRef, Review
from review_insights.selection import deduplicate_reviews
from review_insights.collector import (
    _merge_metadata,
    _next_page_url,
    _page_is_challenge,
    _accept_essential_cookies,
    _scroll_review_container,
    collect_reviews,
    extract_page_metadata,
    extract_page_reviews,
    set_newest_sort,
)


ORG = OrgRef(
    org_id="123", url="https://yandex.ru/maps/org/test/123/reviews/"
)
FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(pw_page, name="reviews.html"):
    pw_page.set_content((FIXTURES / name).read_text(encoding="utf-8"))
    return pw_page


def test_metadata_uses_organization_rating_instead_of_review_rating(pw_page):
    page = load_fixture(pw_page)
    metadata = extract_page_metadata(page)
    reviews, skipped = extract_page_reviews(page, ORG, ORG.url)

    assert metadata == {
        "org_name": "Тестовое кафе",
        "source_review_count": 3,
        "source_rating_count": 10,
        "source_rating": 4.2,
    }
    assert skipped == 0
    assert reviews[0].rating == 5
    assert reviews[0].published_at.year == 2026
    assert reviews[0].published_at.month == 10
    assert reviews[0].published_at.day == 1
    assert reviews[0].published_at.tzinfo is not None
    assert reviews[0].published_at.utcoffset().total_seconds() == 0


def test_later_page_without_counters_does_not_erase_initial_metadata():
    first = {
        "org_name": "Тестовое кафе",
        "source_review_count": 119,
        "source_rating_count": 808,
        "source_rating": 4.5,
    }
    later = {
        "org_name": "",
        "source_review_count": None,
        "source_rating_count": None,
        "source_rating": None,
    }
    assert _merge_metadata(first, later) == first


def test_expansion_collects_full_text_and_invalid_dates_stay_unknown(pw_page):
    page = load_fixture(pw_page)
    reviews, skipped = extract_page_reviews(page, ORG, ORG.url)

    assert skipped == 0
    assert reviews[1].text == "Кофе остыл, заказ ждали слишком долго."
    assert reviews[1].rating == 1
    assert reviews[2].published_at is None
    assert reviews[2].date_label == "давно"


def test_still_collapsed_text_is_skipped(pw_page):
    pw_page.set_content(
        """
        <div class="business-review-view">
          <meta itemprop="ratingValue" content="5">
          <div class="business-review-view__body" itemprop="reviewBody">
            <span class="spoiler-view__text-container">Короткий текст...</span>
            <button class="business-review-view__expand">Ещё</button>
          </div>
        </div>
        """
    )
    reviews, skipped = extract_page_reviews(pw_page, ORG, ORG.url)
    assert reviews == []
    assert skipped == 1


def test_extraction_saves_completed_cards_before_a_blocked_expand_uses_the_budget(pw_page):
    pw_page.set_content(
        """
        <div class="business-review-view">
          <div class="business-review-view__body">Полный первый отзыв.</div>
        </div>
        <div class="business-review-view">
          <div class="business-review-view__body">Текст...</div>
          <button class="business-review-view__expand">Ещё</button>
        </div>
        <div style="position:fixed;inset:0;z-index:999">Перекрывающий баннер</div>
        """
    )
    saved = []
    started = time.monotonic()
    reviews, skipped = extract_page_reviews(
        pw_page, ORG, ORG.url, deadline=started + 0.5, on_review=saved.append
    )
    assert [review.text for review in reviews] == ["Полный первый отзыв."]
    assert saved == reviews
    assert skipped == 1
    assert time.monotonic() - started < 1.5


def test_missing_card_body_does_not_wait_for_a_nonexistent_text_node(pw_page):
    pw_page.set_default_timeout(100)
    pw_page.set_content(
        '<div class="business-review-view"></div>'
        '<div class="business-review-view">'
        '<div class="business-review-view__body">Полный текст.</div></div>'
    )
    reviews, skipped = extract_page_reviews(pw_page, ORG, ORG.url)
    assert [review.text for review in reviews] == ["Полный текст."]
    assert skipped == 1


def test_newest_sort_waits_for_fetch_and_delayed_review_content(pw_page):
    page = load_fixture(pw_page)
    page.route(
        "**/maps/api/business/fetchReviews*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            body="{}",
        ),
    )

    assert set_newest_sort(page, deadline=__import__("time").monotonic() + 10)
    reviews, skipped = extract_page_reviews(page, ORG, ORG.url)
    assert skipped == 0
    assert reviews[0].text == "Новые отзывы появились после ответа сервера."


def test_newest_sort_uses_keyboard_when_pointer_does_not_open_menu(pw_page):
    pw_page.set_content(
        """
        <div class="rating-ranking-view" role="button" tabindex="0" aria-expanded="false">
          <span>По умолчанию</span>
        </div>
        <div id="menu" style="display:none"><div id="newest" role="button">По новизне</div></div>
        <div class="business-review-view">
          <meta itemprop="datePublished" content="2026-10-01T10:00:00Z">
          <div class="business-review-view__body"><span class="spoiler-view__text-container">Старый текст.</span></div>
        </div>
        <script>
          const ranking = document.querySelector(".rating-ranking-view");
          ranking.addEventListener("keydown", event => {
            if (event.key === "Enter") {
              document.querySelector("#menu").style.display = "block";
              ranking.setAttribute("aria-expanded", "true");
            }
          });
          document.querySelector("#newest").addEventListener("click", () => {
            fetch("https://yandex.ru/maps/api/business/fetchReviews?page=1&pageSize=50&ranking=by_time")
              .then(() => document.querySelector(".spoiler-view__text-container").textContent = "Обновлённый текст.");
          });
        </script>
        """
    )
    pw_page.route(
        "**/maps/api/business/fetchReviews*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            body="{}",
        ),
    )
    assert set_newest_sort(pw_page, deadline=__import__("time").monotonic() + 10)
    assert pw_page.locator(".spoiler-view__text-container").text_content() == "Обновлённый текст."


def test_scroll_loads_more_cards_and_duplicate_ids_do_not_grow_count(pw_page):
    page = pw_page
    pw_page.set_content(
        """
        <style>.business-review-view { min-height:60px; }</style>
        <div class="scroll__container" style="height:40px;overflow:auto">
          <div class="business-review-view">
            <meta itemprop="ratingValue" content="5">
            <meta itemprop="datePublished" content="2026-10-01T10:00:00Z">
            <div class="business-review-view__body" itemprop="reviewBody">
              <span class="spoiler-view__text-container">Старый отзыв.</span>
            </div>
          </div>
        </div>
        <script>
          document.querySelector(".scroll__container").addEventListener("scroll", event => {
            const root = event.currentTarget;
            if (!root.dataset.loaded) {
              root.dataset.loaded = "yes";
              root.insertAdjacentHTML("beforeend", `
                <div class="business-review-view">
                  <meta itemprop="ratingValue" content="5">
                  <meta itemprop="datePublished" content="2026-10-01T10:00:00Z">
                  <div class="business-review-view__body" itemprop="reviewBody">
                    <span class="spoiler-view__text-container">Старый отзыв.</span>
                  </div>
                </div>
                <div class="business-review-view">
                  <meta itemprop="ratingValue" content="4">
                  <meta itemprop="datePublished" content="2026-10-02T10:00:00Z">
                  <div class="business-review-view__body" itemprop="reviewBody">
                    <span class="spoiler-view__text-container">Новый отзыв.</span>
                  </div>
                </div>`);
            }
          });
        </script>
        """
    )
    first, skipped = extract_page_reviews(page, ORG, ORG.url)
    assert skipped == 0
    assert _scroll_review_container(page, wait_s=1)
    second, skipped = extract_page_reviews(page, ORG, ORG.url)

    assert skipped == 0
    assert len(second) == len(first) + 1
    assert len(deduplicate_reviews([*second, second[0]])) == len(second)


def test_scroll_waits_for_late_review_cards(pw_page):
    pw_page.set_content(
        """
        <style>.business-review-view { min-height:60px; }</style>
        <div class="scroll__container" style="height:40px;overflow:auto">
          <div class="business-review-view">
            <div class="business-review-view__body"><span>Первый отзыв</span></div>
          </div>
        </div>
        <script>
          document.querySelector('.scroll__container').addEventListener('scroll', event => {
            const root = event.currentTarget;
            setTimeout(() => root.insertAdjacentHTML(
              'beforeend', '<div class="business-review-view"><div class="business-review-view__body">Поздний отзыв</div></div>'
            ), 300);
          });
        </script>
        """
    )

    started = time.monotonic()
    assert _scroll_review_container(pw_page, wait_s=1)
    assert time.monotonic() - started >= 0.2
    assert pw_page.locator(".business-review-view").count() == 2


def test_scroll_stops_after_wait_when_no_cards_arrive(pw_page):
    pw_page.set_content(
        """
        <div class="scroll__container" style="height:40px;overflow:auto">
          <div class="business-review-view">
            <div class="business-review-view__body"><span>Первый отзыв</span></div>
          </div>
        </div>
        """
    )
    assert not _scroll_review_container(pw_page, wait_s=0.05)


def test_only_required_cookie_button_is_clicked(pw_page):
    pw_page.set_content(
        """
        <button id="required" onclick="window.clicked = 'required'">
          Разрешить обязательные
        </button>
        <button id="all" onclick="window.clicked = 'all'">Разрешить все</button>
        """
    )

    assert _accept_essential_cookies(pw_page, time.monotonic() + 1)
    assert pw_page.evaluate("window.clicked") == "required"


def test_collect_uses_project_persistent_profile(monkeypatch, tmp_path):
    seen = {}

    class FakePage:
        url = ORG.url

        def goto(self, *args, **kwargs):
            return SimpleNamespace(status=429)

        def locator(self, selector):
            class Body:
                @staticmethod
                def inner_text(timeout):
                    return ""

            return Body()

    class FakeContext:
        def __init__(self):
            self.closed = False

        def new_page(self):
            return FakePage()

        def close(self):
            self.closed = True

    context = FakeContext()

    class FakeChromium:
        def launch_persistent_context(self, user_data_dir, **options):
            seen.update(path=Path(user_data_dir), options=options)
            return context

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("review_insights.collector.sync_playwright", lambda: FakePlaywright())
    monkeypatch.setattr("review_insights.collector.PROJECT_ROOT", tmp_path)

    result = collect_reviews(ORG)

    assert result.stop_reason == "rate_limited"
    assert seen["path"] == (tmp_path / ".browser-profile" / "yandex").resolve()
    assert seen["options"]["headless"] is False
    assert context.closed


def test_busy_project_profile_returns_safe_error_without_retry(monkeypatch, tmp_path):
    calls = []

    class FakeChromium:
        def launch_persistent_context(self, user_data_dir, **options):
            calls.append(Path(user_data_dir))
            raise PlaywrightError("private profile details")

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("review_insights.collector.sync_playwright", lambda: FakePlaywright())
    monkeypatch.setattr("review_insights.collector.PROJECT_ROOT", tmp_path)

    result = collect_reviews(ORG)

    assert result.reviews == []
    assert result.stop_reason == "load_error"
    assert calls == [tmp_path / ".browser-profile" / "yandex"]
    assert "private profile details" not in " ".join(result.warnings)


def test_pagination_rejects_other_organization_and_visited_page(pw_page):
    page = load_fixture(pw_page)
    visited = {1}
    assert _next_page_url(page, "123", current_page=1, visited_pages=visited) == (
        "https://yandex.ru/maps/org/test/123/reviews/?page=2"
    )
    visited.add(2)
    assert _next_page_url(page, "123", current_page=2, visited_pages=visited) is None


def test_captcha_page_is_classified_as_challenge(pw_page):
    pw_page.set_content("<title>showcaptcha</title><p>Подтвердите, что вы не робот</p>")
    assert _page_is_challenge(pw_page)


def test_initial_http_429_is_reported_as_rate_limited(monkeypatch, tmp_path):
    class FakePage:
        url = ORG.url

        def goto(self, *args, **kwargs):
            return SimpleNamespace(status=429)

        def locator(self, selector):
            class Body:
                @staticmethod
                def inner_text(timeout):
                    return ""

            return Body()

    class FakeContext:
        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeBrowser:
        def new_context(self, **kwargs):
            return FakeContext()

        def close(self):
            pass

    class FakeChromium:
        def launch_persistent_context(self, user_data_dir, **kwargs):
            return FakeBrowser().new_context()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        "review_insights.collector.sync_playwright", lambda: FakePlaywright()
    )
    monkeypatch.setattr("review_insights.collector.PROJECT_ROOT", tmp_path)

    result = collect_reviews(ORG)

    assert result.reviews == []
    assert result.stop_reason == "rate_limited"
    assert any("HTTP 429" in warning for warning in result.warnings)


def test_fallback_page_http_429_preserves_partial_collection(monkeypatch, tmp_path):
    class FakeLocator:
        first = None

        def __init__(self):
            self.first = self

        def count(self):
            return 0

        def is_visible(self):
            return False

        def wait_for(self, **kwargs):
            pass

    responses = iter((200, 429))

    class FakePage:
        url = ""

        def goto(self, target, **kwargs):
            self.url = target
            return SimpleNamespace(status=next(responses))

        def get_by_role(self, *args, **kwargs):
            return FakeLocator()

        def locator(self, *args, **kwargs):
            return FakeLocator()

    page = FakePage()

    class FakeContext:
        def new_page(self):
            return page

        def close(self):
            pass

    class FakeBrowser:
        def new_context(self, **kwargs):
            return FakeContext()

        def close(self):
            pass

    class FakeChromium:
        def launch_persistent_context(self, user_data_dir, **kwargs):
            return FakeBrowser().new_context()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    review = Review(
        review_id="partial-review",
        text="review text collected before the page limit.",
        rating=5,
        published_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        date_label="Oct 1",
        source_url=ORG.url,
    )
    second_page = "https://yandex.ru/maps/org/test/123/reviews/?page=2"
    monkeypatch.setattr(
        "review_insights.collector.sync_playwright", lambda: FakePlaywright()
    )
    monkeypatch.setattr("review_insights.collector.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("review_insights.collector._page_is_challenge", lambda page: False)
    monkeypatch.setattr("review_insights.collector.extract_page_metadata", lambda page: {
        "org_name": "Test cafe",
        "source_review_count": 10,
        "source_rating_count": 10,
        "source_rating": 4.5,
    })
    monkeypatch.setattr(
        "review_insights.collector.set_newest_sort", lambda page, deadline: False
    )
    monkeypatch.setattr(
        "review_insights.collector.extract_page_reviews",
        lambda page, org, source_url, **kwargs: ([review], 0),
    )
    monkeypatch.setattr(
        "review_insights.collector._scroll_review_container",
        lambda page, wait_s: False,
    )
    monkeypatch.setattr(
        "review_insights.collector._next_page_url",
        lambda page, org_id, current_page, visited_pages: (
            second_page if current_page == 1 else None
        ),
    )

    result = collect_reviews(ORG, limit=10, timeout_s=5)

    assert [item.review_id for item in result.reviews] == ["partial-review"]
    assert result.stop_reason == "rate_limited"
    assert any("HTTP 429" in warning for warning in result.warnings)


def test_date_fixture_has_expected_offset_semantics(pw_page):
    page = load_fixture(pw_page)
    reviews, _ = extract_page_reviews(page, ORG, ORG.url)
    assert reviews[1].published_at.utcoffset().total_seconds() == 3 * 60 * 60
