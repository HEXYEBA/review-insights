import pytest

from review_insights.models import UrlError
from review_insights.urls import is_same_org_url, normalize_org_url


def test_canonical_url_removes_query_and_appends_reviews():
    ref = normalize_org_url(
        " https://yandex.ru/maps/org/kofemaniya/1242233919/?ll=1,2#photos "
    )
    assert ref.org_id == "1242233919"
    assert ref.url == "https://yandex.ru/maps/org/kofemaniya/1242233919/reviews/"


def test_canonical_url_encodes_markup_in_slug_before_rendering_as_link():
    ref = normalize_org_url("https://yandex.ru/maps/org/cafe%3E%5Bspecial%5D/123/")
    assert ref.url == "https://yandex.ru/maps/org/cafe%3E%5Bspecial%5D/123/reviews/"


@pytest.mark.parametrize(
    "url",
    [
        "http://yandex.ru/maps/org/a/123/",
        "https://yandex.ru.evil.example/maps/org/a/123/",
        "https://user:password@yandex.ru/maps/org/a/123/",
        "https://127.0.0.1/maps/org/a/123/",
        "https://yandex.ru:8443/maps/org/a/123/",
        "https://yandex.ru/maps/-/short",
        "https://yandex.ru/maps/org/a/not-a-number/",
        "https://yandex.ru/maps/org/a%2Fb/123/",
        "file:///C:/private.txt",
    ],
)
def test_invalid_url_is_rejected(url):
    with pytest.raises(UrlError):
        normalize_org_url(url)


def test_url_longer_than_limit_is_rejected():
    with pytest.raises(UrlError):
        normalize_org_url("https://yandex.ru/maps/org/a/123/" + "x" * 2048)


def test_yandex_com_and_www_are_accepted_and_canonicalized():
    ref = normalize_org_url("https://www.yandex.com/maps/org/a/123")
    assert ref.url == "https://yandex.ru/maps/org/a/123/reviews/"


def test_redirect_between_yandex_domains_keeps_same_org():
    assert is_same_org_url(
        "https://yandex.com/maps/org/new_slug/1242233919/reviews/?page=2",
        "1242233919",
    )
    assert not is_same_org_url(
        "https://yandex.com/maps/org/a/999/reviews/", "1242233919"
    )
    assert not is_same_org_url("https://example.com/maps/org/a/1242233919/", "1242233919")
