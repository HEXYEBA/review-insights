"""Input validation for public Yandex Maps organization links."""

import re
from urllib.parse import quote, unquote, urlsplit

from review_insights.models import OrgRef, UrlError


_ALLOWED_HOSTS = frozenset(
    {"yandex.ru", "www.yandex.ru", "yandex.com", "www.yandex.com"}
)
_ORG_PATH = re.compile(r"^/maps/org/([^/]+)/([0-9]{1,20})(?:/.*)?$")


def _parse_supported_org_url(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise UrlError("Вставьте полную ссылку на организацию в Яндекс Картах.")

    candidate = value.strip()
    if not candidate or len(candidate) > 2048:
        raise UrlError("Ссылка пустая или длиннее 2048 символов.")
    if any(ord(char) < 32 for char in candidate):
        raise UrlError("Ссылка содержит недопустимые управляющие символы.")

    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        # Comparing the complete authority with the hostname also rejects
        # explicit ports, empty ports, and user information.
        if (
            parsed.scheme.lower() != "https"
            or hostname is None
            or hostname.lower() not in _ALLOWED_HOSTS
            or parsed.netloc.lower() != hostname.lower()
        ):
            raise UrlError("Нужна HTTPS-ссылка с домена Яндекс Карт.")
    except ValueError as exc:
        raise UrlError("Не удалось разобрать ссылку на организацию.") from exc

    path = parsed.path
    if "\\" in path or re.search(r"%2f|%5c", path, flags=re.IGNORECASE):
        raise UrlError("Путь ссылки содержит недопустимый разделитель.")
    match = _ORG_PATH.fullmatch(path)
    if match is None:
        raise UrlError("Откройте полную карточку организации в Яндекс Картах.")

    slug, org_id = match.groups()
    decoded_slug = unquote(slug)
    if (
        not decoded_slug
        or decoded_slug in {".", ".."}
        or "/" in decoded_slug
        or "\\" in decoded_slug
        or any(ord(char) < 32 for char in decoded_slug)
        or re.search(r"%2f|%5c", decoded_slug, flags=re.IGNORECASE)
    ):
        raise UrlError("В ссылке не найдено имя карточки организации.")
    canonical_slug = quote(decoded_slug, safe="-._~")
    return canonical_slug, org_id


def normalize_org_url(value: str) -> OrgRef:
    """Validate and canonicalize a full Yandex Maps organization URL."""
    slug, org_id = _parse_supported_org_url(value)
    canonical = f"https://yandex.ru/maps/org/{slug}/{org_id}/reviews/"
    return OrgRef(org_id=org_id, url=canonical)


def is_same_org_url(value: str, org_id: str) -> bool:
    """Return whether a redirect URL is a supported card for the same ID."""
    try:
        redirected = normalize_org_url(value)
    except UrlError:
        return False
    return redirected.org_id == str(org_id)
