"""Bounded public-page collector for Yandex Maps review cards."""

import math
import re
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from review_insights.browser_runtime import open_review_context
from review_insights.config import (
    COLLECT_BROWSER_MODE,
    COLLECT_TIMEOUT_S,
    MAX_FALLBACK_PAGES,
    MAX_REVIEWS,
    NAVIGATION_TIMEOUT_MS,
    SCROLL_WAIT_MS,
    UI_ACTION_TIMEOUT_MS,
)
from review_insights.models import (
    CollectionError,
    CollectionResult,
    OrgRef,
    Review,
)
from review_insights.selection import deduplicate_reviews, make_review_id, normalize_text
from review_insights.urls import is_same_org_url


_CARD_SELECTOR = ".business-review-view"
_TEXT_SELECTOR = ".business-review-view__body .spoiler-view__text-container"
_BODY_SELECTOR = ".business-review-view__body"
_EXPAND_SELECTOR = ".business-review-view__expand"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _remaining_ms(deadline: float, maximum: int) -> int:
    remaining = math.ceil((deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        raise PlaywrightTimeoutError("Collection deadline reached")
    return min(maximum, remaining)


def _global_meta_values(page: Page, itemprop: str) -> list[str]:
    selector = f'meta[itemprop="{itemprop}"]'
    return page.locator(selector).evaluate_all(
        "nodes => nodes.filter(node => !node.closest('.business-review-view'))"
        ".map(node => node.getAttribute('content') || '')"
    )


def _parse_count(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        result = int(float(value.strip().replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def _first_global_meta(page: Page, itemprop: str) -> str | None:
    for value in _global_meta_values(page, itemprop):
        if value.strip():
            return value.strip()
    return None


def extract_page_metadata(page: Page) -> dict:
    """Read the organization label and aggregate counters, excluding card metadata."""
    name_locator = page.locator("h1.orgpage-header-view__header").first
    org_name = normalize_text(name_locator.text_content() or "") if name_locator.count() else ""

    rating_raw = _first_global_meta(page, "ratingValue")
    try:
        source_rating = float(rating_raw) if rating_raw is not None else None
        if source_rating is not None and not 0 <= source_rating <= 5:
            source_rating = None
    except (TypeError, ValueError, OverflowError):
        source_rating = None

    return {
        "org_name": org_name,
        "source_review_count": _parse_count(_first_global_meta(page, "reviewCount")),
        "source_rating_count": _parse_count(_first_global_meta(page, "ratingCount")),
        "source_rating": source_rating,
    }


def _merge_metadata(previous: dict, current: dict) -> dict:
    """Keep known organization counters when a later page omits global metadata."""
    merged = dict(previous)
    for key, value in current.items():
        if value is not None and (key != "org_name" or value.strip()):
            merged[key] = value
    return merged


def _parse_review_rating(card) -> int | None:
    rating_locator = card.locator('meta[itemprop="ratingValue"]').first
    rating_raw = rating_locator.get_attribute("content") if rating_locator.count() else None
    if rating_raw is None:
        stars = card.locator(".business-rating-badge-view__stars").first
        rating_raw = stars.get_attribute("aria-label") if stars.count() else None
    if not rating_raw:
        return None
    match = re.search(r"[0-9]+(?:[.,][0-9]+)?", rating_raw)
    if not match:
        return None
    try:
        rating = int(float(match.group(0).replace(",", ".")))
    except (ValueError, OverflowError):
        return None
    return rating if 1 <= rating <= 5 else None


def _parse_published_at(card) -> datetime | None:
    meta = card.locator('meta[itemprop="datePublished"]').first
    raw = meta.get_attribute("content") if meta.count() else None
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value


def _review_date_label(card) -> str:
    label = card.locator(".business-review-view__date").first
    return normalize_text(label.text_content() or "") if label.count() else ""


def _review_text_locator(card):
    text = card.locator(_TEXT_SELECTOR).first
    if text.count():
        return text
    return card.locator(_BODY_SELECTOR).first


def _expand_if_needed(card, *, deadline: float | None = None) -> bool:
    button = card.locator(_EXPAND_SELECTOR).first
    if not button.count() or not button.is_visible():
        return True
    label = normalize_text(button.text_content() or "").casefold()
    if label not in {"ещё", "еще"}:
        return True

    text_locator = _review_text_locator(card)
    if not text_locator.count():
        return False
    read_timeout = _remaining_ms(deadline, UI_ACTION_TIMEOUT_MS) if deadline else UI_ACTION_TIMEOUT_MS
    before = normalize_text(text_locator.text_content(timeout=read_timeout) or "")
    try:
        button.click(timeout=_remaining_ms(deadline, UI_ACTION_TIMEOUT_MS) if deadline else UI_ACTION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        return False

    expansion_deadline = time.monotonic() + 1.0
    if deadline is not None:
        expansion_deadline = min(expansion_deadline, deadline)
    changed = False
    while time.monotonic() < expansion_deadline:
        current = normalize_text(text_locator.text_content(
            timeout=_remaining_ms(expansion_deadline, UI_ACTION_TIMEOUT_MS)
        ) or "")
        changed = current != before
        if changed and not button.is_visible():
            return True
        if not button.is_visible():
            return bool(current)
        card.page.wait_for_timeout(min(100, _remaining_ms(expansion_deadline, 100)))
    # A visible expand control means the card may still contain hidden text.
    return False


def extract_page_reviews(
    page: Page, org: OrgRef, source_url: str, *,
    deadline: float | None = None,
    on_review: Callable[[Review], None] | None = None,
) -> tuple[list[Review], int]:
    """Extract complete review text and return the number of unreadable cards."""
    cards = page.locator(_CARD_SELECTOR)
    extracted: list[Review] = []
    skipped = 0
    emitted: set[str] = set()

    for index in range(cards.count()):
        if deadline is not None and time.monotonic() >= deadline:
            break
        card = cards.nth(index)
        try:
            text_locator = _review_text_locator(card)
            if not text_locator.count() or not _expand_if_needed(card, deadline=deadline):
                skipped += 1
                continue
            text = normalize_text(text_locator.text_content(
                timeout=_remaining_ms(deadline, UI_ACTION_TIMEOUT_MS) if deadline else UI_ACTION_TIMEOUT_MS
            ) or "")
        except PlaywrightTimeoutError:
            skipped += 1
            continue

        if not text:
            skipped += 1
            continue

        published_at = _parse_published_at(card)
        date_label = _review_date_label(card)
        rating = _parse_review_rating(card)
        date_component = published_at.isoformat() if published_at is not None else date_label
        review_id = make_review_id(org.org_id, text, date_component, rating)
        review = Review(
                review_id=review_id,
                text=text,
                rating=rating,
                published_at=published_at,
                date_label=date_label,
                source_url=source_url,
            )
        extracted.append(review)
        if review_id not in emitted:
            emitted.add(review_id)
            if on_review is not None:
                on_review(review)

    return deduplicate_reviews(extracted), skipped


def _page_signature(page: Page) -> tuple[str, ...]:
    # One atomic DOM read avoids auto-waiting on placeholders and hundreds of IPCs.
    values = page.locator(_CARD_SELECTOR).evaluate_all(
        """cards => cards.map(card => ({
            date: card.querySelector('meta[itemprop="datePublished"]')?.content || '',
            text: (card.querySelector('.business-review-view__body .spoiler-view__text-container')
                || card.querySelector('.business-review-view__body'))?.textContent || ''
        }))"""
    )
    return tuple(
        f"{value['date']}\n{normalize_text(value['text'])}"
        for value in values if normalize_text(value["text"])
    )


def _is_newest_response(response) -> bool:
    parsed = urlsplit(response.url)
    query = parse_qs(parsed.query)
    return (
        parsed.path.endswith("/maps/api/business/fetchReviews")
        and query.get("ranking") == ["by_time"]
    )


def set_newest_sort(page: Page, deadline: float) -> bool:
    """Select newest ordering and wait for the browser response and stable DOM."""
    old_signature = _page_signature(page)
    try:
        ranking_control = page.locator(".rating-ranking-view").first
        ranking_control.click(timeout=_remaining_ms(deadline, UI_ACTION_TIMEOUT_MS))
        newest_option = page.get_by_text("По новизне", exact=True).first
        if not newest_option.count() or not newest_option.is_visible():
            # Some current Yandex Maps builds expose this control as a keyboard
            # button; pointer activation leaves aria-expanded=false in that UI.
            ranking_control.press("Enter", timeout=_remaining_ms(deadline, UI_ACTION_TIMEOUT_MS))
        newest_option.wait_for(
            state="visible",
            timeout=_remaining_ms(deadline, UI_ACTION_TIMEOUT_MS),
        )
        with page.expect_response(
            _is_newest_response,
            timeout=_remaining_ms(deadline, 8_000),
        ) as response_info:
            newest_option.click(timeout=_remaining_ms(deadline, UI_ACTION_TIMEOUT_MS))
        response = response_info.value
        if response.status != 200:
            raise CollectionError("Не удалось загрузить сортировку отзывов.")
        response.finished()
    except PlaywrightTimeoutError:
        return False

    change_deadline = min(deadline, time.monotonic() + 2.0)
    previous: tuple[str, ...] | None = None
    stable_samples = 0
    changed = False
    while time.monotonic() < deadline:
        now = time.monotonic()
        signature = _page_signature(page)
        changed = changed or signature != old_signature
        if changed or now >= change_deadline:
            if signature and signature == previous:
                stable_samples += 1
            else:
                stable_samples = 1 if signature else 0
            if stable_samples >= 2:
                return True
        else:
            stable_samples = 0
        previous = signature
        page.wait_for_timeout(min(100, _remaining_ms(deadline, 100)))
    return False


def _scroll_review_container(page: Page, wait_s: float = SCROLL_WAIT_MS / 1000) -> bool:
    cards = page.locator(_CARD_SELECTOR)
    if not cards.count():
        return False
    container = cards.first.locator(
        "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' scroll__container ')][1]"
    )
    if not container.count():
        container = page.locator(".scroll__container").first
    if not container.count():
        return False

    before = _page_signature(page)
    container.evaluate("element => { element.scrollTop = element.scrollHeight; }")
    deadline = time.monotonic() + max(0.0, wait_s)
    while time.monotonic() < deadline:
        signature = _page_signature(page)
        if signature != before:
            return True
        page.wait_for_timeout(min(100, max(1, math.ceil((deadline - time.monotonic()) * 1000))))
    return _page_signature(page) != before


def _next_page_url(
    page: Page, org_id: str, current_page: int, visited_pages: set[int]
) -> str | None:
    candidates: list[tuple[int, str]] = []
    links = page.locator('a[href*="page="]')
    for index in range(links.count()):
        href = links.nth(index).get_attribute("href")
        if not href:
            continue
        candidate = urljoin(page.url, href)
        if not is_same_org_url(candidate, org_id):
            continue
        query = parse_qs(urlsplit(candidate).query)
        for raw_page in query.get("page", []):
            if not raw_page.isdigit():
                continue
            page_number = int(raw_page)
            if page_number > current_page and page_number not in visited_pages:
                candidates.append((page_number, candidate))
    return min(candidates, default=(0, None), key=lambda item: item[0])[1]


def _page_is_challenge(page: Page) -> bool:
    current_url = page.url.casefold()
    if "showcaptcha" in current_url or "captcha" in current_url:
        return True
    try:
        body = (page.locator("body").inner_text(timeout=500) or "").casefold()
    except PlaywrightTimeoutError:
        return False
    return "подтвердите, что вы не робот" in body


def _accept_essential_cookies(page: Page, deadline: float) -> bool:
    """Accept only the required-cookie option when that exact button is visible."""
    button = page.get_by_role(
        "button",
        name=re.compile(
            r"^(Allow essential cookies|Только необходимые|Разрешить обязательные)$",
            re.I,
        ),
    ).first
    try:
        if button.count() and button.is_visible():
            button.click(timeout=_remaining_ms(deadline, UI_ACTION_TIMEOUT_MS))
    except PlaywrightTimeoutError:
        return False
    return True


def _http_status_failure(response) -> tuple[str, str] | None:
    """Classify a failed Yandex document response without reading its body."""
    if response is None or response.status < 400:
        return None
    if response.status == 429:
        return (
            "rate_limited",
            "Яндекс Карты временно ограничили загрузку страницы (HTTP 429).",
        )
    return (
        "load_error",
        f"Страница Яндекс Карт вернула ошибку загрузки (HTTP {response.status}).",
    )


def _make_result(
    org: OrgRef,
    metadata: dict,
    reviews: list[Review],
    stop_reason: str,
    sort_mode: str,
    warnings: list[str],
) -> CollectionResult:
    return CollectionResult(
        org=org,
        org_name=metadata.get("org_name", ""),
        collected_at=datetime.now(timezone.utc),
        source_review_count=metadata.get("source_review_count"),
        source_rating_count=metadata.get("source_rating_count"),
        source_rating=metadata.get("source_rating"),
        reviews=list(reviews),
        stop_reason=stop_reason,
        sort_mode=sort_mode,
        warnings=list(warnings),
    )


def collect_reviews(
    org: OrgRef,
    limit: int = MAX_REVIEWS,
    timeout_s: int = COLLECT_TIMEOUT_S,
    on_snapshot: Callable[[CollectionResult], None] | None = None,
) -> CollectionResult:
    """Collect a bounded set of public reviews with the project's browser profile."""
    if not 1 <= limit <= MAX_REVIEWS:
        raise ValueError(f"limit must be between 1 and {MAX_REVIEWS}")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")

    deadline = time.monotonic() + timeout_s
    metadata = {
        "org_name": "",
        "source_review_count": None,
        "source_rating_count": None,
        "source_rating": None,
    }
    collected: dict[str, Review] = {}
    warnings: list[str] = []
    sort_mode = "default"
    stop_reason = "no_new_reviews"
    try:
        with sync_playwright() as playwright, ExitStack() as stack:
            context = stack.enter_context(
                open_review_context(
                    playwright,
                    mode=COLLECT_BROWSER_MODE,
                    project_root=PROJECT_ROOT,
                )
            )
            page = context.new_page()
            try:
                document_response = page.goto(
                    org.url,
                    wait_until="domcontentloaded",
                    timeout=_remaining_ms(deadline, NAVIGATION_TIMEOUT_MS),
                )
            except PlaywrightTimeoutError:
                stop_reason = "deadline" if time.monotonic() >= deadline else "load_error"
                warnings.append("Не удалось загрузить страницу организации вовремя.")
                return _make_result(org, metadata, [], stop_reason, sort_mode, warnings)

            if not is_same_org_url(page.url, org.org_id):
                raise CollectionError("Яндекс Карты открыли другую карточку организации.")

            if _page_is_challenge(page):
                return _make_result(org, metadata, [], "blocked", sort_mode, [
                    "Яндекс Карты запросили проверку посетителя; сбор остановлен."
                ])

            response_failure = _http_status_failure(document_response)
            if response_failure is not None:
                reason, warning = response_failure
                warnings.append(warning)
                return _make_result(org, metadata, [], reason, sort_mode, warnings)

            if not _accept_essential_cookies(page, deadline):
                warnings.append("Не удалось закрыть запрос согласия на необходимые cookies.")

            metadata = extract_page_metadata(page)
            if metadata["source_review_count"] == 0:
                return _make_result(org, metadata, [], "exhausted", sort_mode, warnings)

            try:
                page.locator(_CARD_SELECTOR).first.wait_for(
                    state="attached", timeout=_remaining_ms(deadline, 10_000)
                )
            except PlaywrightTimeoutError:
                reason = "deadline" if time.monotonic() >= deadline else "layout_changed"
                warnings.append("На карточке не найдены ожидаемые элементы отзывов.")
                return _make_result(org, metadata, [], reason, sort_mode, warnings)

            if _page_is_challenge(page):
                return _make_result(org, metadata, [], "blocked", sort_mode, [
                    "Яндекс Карты запросили проверку посетителя; сбор остановлен."
                ])

            try:
                if set_newest_sort(page, deadline):
                    sort_mode = "newest"
                else:
                    warnings.append("Не удалось подтвердить сортировку по новизне; сохранён порядок сайта.")
            except CollectionError:
                raise

            visited_pages = {1}
            current_page = 1
            pages_opened = 1
            while True:
                if _page_is_challenge(page):
                    stop_reason = "blocked"
                    warnings.append("Яндекс Карты запросили проверку посетителя; сбор остановлен.")
                    break

                page_url = page.url
                before_ids = set(collected)
                page_skipped = 0
                no_growth_scrolls = 0
                while no_growth_scrolls < 2:
                    if time.monotonic() >= deadline:
                        stop_reason = "deadline"
                        break
                    def preserve_review(review: Review) -> None:
                        if review.review_id in collected or len(collected) >= limit:
                            return
                        collected[review.review_id] = review
                        if on_snapshot is not None:
                            on_snapshot(_make_result(
                                org, metadata, list(collected.values()),
                                "no_new_reviews", sort_mode, warnings,
                            ))

                    page_reviews, skipped = extract_page_reviews(
                        page, org, page_url, deadline=deadline, on_review=preserve_review
                    )
                    page_skipped = max(page_skipped, skipped)
                    for review in page_reviews:
                        if len(collected) >= limit:
                            break
                        collected.setdefault(review.review_id, review)
                    if page_skipped:
                        warning = f"Пропущено свёрнутых или нечитаемых карточек на странице: {page_skipped}."
                        if warning not in warnings:
                            warnings.append(warning)
                    snapshot = _make_result(
                        org, metadata, list(collected.values()), "no_new_reviews", sort_mode, warnings
                    )
                    if on_snapshot is not None:
                        on_snapshot(snapshot)
                    if len(collected) >= limit:
                        stop_reason = "limit"
                        break

                    if set(collected) != before_ids:
                        before_ids = set(collected)
                        no_growth_scrolls = 0

                    remaining = max(0.0, deadline - time.monotonic())
                    if remaining <= 0:
                        stop_reason = "deadline"
                        break
                    changed = _scroll_review_container(
                        page, wait_s=min(SCROLL_WAIT_MS / 1000, remaining)
                    )
                    if changed:
                        # Extract the newly rendered batch on the next iteration.
                        continue
                    no_growth_scrolls += 1

                if stop_reason in {"limit", "deadline", "blocked"}:
                    break

                source_count = metadata["source_review_count"]
                if source_count is not None and len(collected) >= source_count:
                    stop_reason = "exhausted"
                    break

                next_url = _next_page_url(page, org.org_id, current_page, visited_pages)
                if (
                    next_url is not None
                    and pages_opened < MAX_FALLBACK_PAGES
                    and time.monotonic() < deadline
                ):
                    query = parse_qs(urlsplit(next_url).query)
                    next_page = int(query["page"][0])
                    try:
                        next_response = page.goto(
                            next_url,
                            wait_until="domcontentloaded",
                            timeout=_remaining_ms(deadline, NAVIGATION_TIMEOUT_MS),
                        )
                    except PlaywrightTimeoutError:
                        stop_reason = "deadline" if time.monotonic() >= deadline else "load_error"
                        warnings.append("Не удалось загрузить следующую страницу отзывов.")
                        break
                    if not is_same_org_url(page.url, org.org_id):
                        raise CollectionError("Страница отзывов ведёт на другую организацию.")
                    if _page_is_challenge(page):
                        stop_reason = "blocked"
                        warnings.append("Яндекс Карты запросили проверку посетителя; сбор остановлен.")
                        break
                    response_failure = _http_status_failure(next_response)
                    if response_failure is not None:
                        stop_reason, warning = response_failure
                        warnings.append(warning)
                        break
                    current_page = next_page
                    visited_pages.add(next_page)
                    pages_opened += 1
                    sort_mode = "mixed"
                    warning = "Использованы отдельные страницы; общий порядок по новизне не гарантирован."
                    if warning not in warnings:
                        warnings.append(warning)
                    metadata = _merge_metadata(metadata, extract_page_metadata(page))
                    try:
                        page.locator(_CARD_SELECTOR).first.wait_for(
                            state="attached", timeout=_remaining_ms(deadline, 10_000)
                        )
                    except PlaywrightTimeoutError:
                        stop_reason = "layout_changed"
                        warnings.append("На следующей странице не найдены карточки отзывов.")
                        break
                    continue

                source_count = metadata["source_review_count"]
                if source_count is not None and len(collected) >= source_count:
                    stop_reason = "exhausted"
                else:
                    stop_reason = "no_new_reviews"
                    if pages_opened >= MAX_FALLBACK_PAGES:
                        warnings.append("Достигнут лимит пяти страниц; сбор может быть неполным.")
                break

            return _make_result(
                org, metadata, list(collected.values()), stop_reason, sort_mode, warnings
            )
    except PlaywrightError:
        warnings.append("Не удалось открыть отдельный профиль браузера для сбора отзывов.")
        return _make_result(
            org, metadata, list(collected.values()), "load_error", sort_mode, warnings
        )
