from review_insights.clipboard_input import apply_clipboard_event


def test_paste_replaces_whole_field_with_canonical_link_and_clears_old_report():
    state = {"input_url": "старый посторонний текст", "report_bundle": object(), "error_message": "old"}
    apply_clipboard_event({"nonce": "one", "action": "paste", "value": "https://yandex.ru/maps/org/vkusvill/217633938954/?z=12"}, state)
    assert state["input_url"] == "https://yandex.ru/maps/org/vkusvill/217633938954/reviews/"
    assert state["report_bundle"] is None
    assert state["error_message"] is None
    assert state["input_revision"] == 1


def test_rejected_clipboard_text_never_overwrites_a_good_url_or_exposes_content():
    state = {"input_url": "https://yandex.ru/maps/org/test/123/reviews/"}
    apply_clipboard_event({"nonce": "bad", "action": "paste", "value": "private old text"}, state)
    assert state["input_url"] == "https://yandex.ru/maps/org/test/123/reviews/"
    assert "private old text" not in str(state)
    assert state["input_feedback"]


def test_old_clipboard_event_is_not_reapplied_when_user_edits_field():
    state = {}
    event = {"nonce": "once", "action": "paste", "value": "https://yandex.ru/maps/org/test/123/"}
    apply_clipboard_event(event, state)
    state["input_url"] = "https://yandex.ru/maps/org/other/456/"
    apply_clipboard_event(event, state)
    assert state["input_url"] == "https://yandex.ru/maps/org/other/456/"
    assert state["input_revision"] == 1


def test_clear_removes_input_and_previous_output_without_changing_clipboard():
    state = {"input_url": "old", "report_markdown": "old report", "process_view": object()}
    apply_clipboard_event({"nonce": "clear-1", "action": "clear"}, state)
    assert state["input_url"] == ""
    assert state["report_markdown"] is None
    assert state["process_view"] is None
