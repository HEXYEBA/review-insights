"""Explicit, browser-side clipboard actions; never read the server clipboard."""

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

from review_insights.models import UrlError
from review_insights.urls import normalize_org_url


clipboard_controls = components.declare_component(
    "clipboard_controls", path=str(Path(__file__).resolve().parent / "clipboard_frontend")
)


def apply_clipboard_event(event: Any, state: MutableMapping) -> None:
    """Apply a new action once, before the native URL widget is instantiated."""
    if not isinstance(event, dict):
        return
    nonce = event.get("nonce")
    if not isinstance(nonce, str) or not 1 <= len(nonce) <= 128:
        return
    if nonce == state.get("_clipboard_nonce"):
        return
    state["_clipboard_nonce"] = nonce
    action = event.get("action")
    if action == "clear":
        value = ""
    elif action == "paste":
        try:
            value = normalize_org_url(event.get("value")).url
        except UrlError:
            state["input_feedback"] = "Не вставлено: в буфере нет полной ссылки на карточку Яндекс Карт. Скопируйте адрес карточки из адресной строки."
            return
    else:
        return
    state.update(
        input_url=value, report_bundle=None, report_markdown=None,
        report_is_saved=False, error_message=None, process_view=None,
        input_feedback=None,
    )
    # A new widget identity discards unsubmitted text buffered by the old form.
    state["input_revision"] = state.get("input_revision", 0) + 1
