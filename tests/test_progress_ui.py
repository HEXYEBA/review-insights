from review_insights.ui import ProcessView, render_process, render_preview


def test_repair_keeps_validation_active_and_error_does_not_complete_future_steps():
    view = ProcessView()
    view.update("Собираем отзывы…")
    view.update("Анализируем 50 отзывов…")
    view.update("Проверяем цитаты и ссылки…")
    view.update("Модель исправляет отчёт после проверки (до 300 секунд)…")
    assert view.stage == 2
    html = render_process(view)
    assert 'aria-current="step"' in html
    assert "Модель исправляет" in html
    assert 'data-step="3" data-state="waiting"' in html
    view.fail()
    html = render_process(view)
    assert 'data-step="2" data-state="error"' in html
    assert 'data-step="3" data-state="waiting"' in html


def test_cached_report_does_not_claim_live_collection_or_analysis():
    view = ProcessView()
    view.complete(cached=True)
    html = render_process(view)
    assert "Сохранённый отчёт открыт" in html
    assert 'data-state="done"' not in html


def test_preview_escapes_reviews(collection):
    collection.reviews[0].text = '<script>alert("x")</script> **текст**'
    html = render_preview(collection)
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert 'Собрано: 2' in html
    assert '**текст**' in html


def test_preview_bounds_samples_without_changing_real_collection_count(collection):
    collection.reviews = [collection.reviews[0].model_copy(update={"review_id": str(i), "text": f"sample-{i}"}) for i in range(8)]
    html = render_preview(collection)
    assert html.count('<article>') == 3
    assert 'Собрано: 8' in html
    assert 'sample-0' not in html
    assert 'sample-7' in html


def test_analysis_preview_shows_measured_ratings_without_claiming_model_results(selected_reviews):
    from review_insights.models import AnalysisPreview
    from review_insights.ui import render_analysis_preview
    event = AnalysisPreview(stage="analysis", selected_reviews=selected_reviews)
    html = render_analysis_preview(event)
    assert 'Отобрано: 2' in html
    assert '4–5 звёзд: 1' in html
    assert '3 звезды: 1' in html
    assert 'Черновик ИИ' not in html


def test_checking_preview_exposes_unmatched_quote_and_escapes_untrusted_draft(selected_reviews, valid_draft):
    from review_insights.models import AnalysisPreview
    from review_insights.ui import render_analysis_preview
    draft = valid_draft.model_copy(deep=True)
    draft.summary = '<script>alert("x")</script>'
    draft.themes[0].evidence[0].quote = 'Вымышленная цитата'
    html = render_analysis_preview(AnalysisPreview(stage="repair", selected_reviews=selected_reviews, draft=draft, issue="Цитата не найдена"))
    assert '&lt;script&gt;' in html
    assert '<script>' not in html
    assert 'ещё не итог' in html
    assert 'Не найдено дословного совпадения' in html
    assert 'Кофе вкусный, но заказ ждал сорок минут.' in html
    assert 'Совпало цитат: 0 из 1' in html


def test_stage_preview_fits_phone_and_shows_both_quote_and_source(pw_page, selected_reviews, valid_draft):
    from review_insights.models import AnalysisPreview
    from review_insights.ui import APP_CSS, render_analysis_preview
    pw_page.set_viewport_size({"width": 390, "height": 844})
    pw_page.set_content(APP_CSS + render_analysis_preview(AnalysisPreview(stage="checking", selected_reviews=selected_reviews, draft=valid_draft)))
    assert pw_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert pw_page.get_by_text('заказ ждал сорок минут', exact=True).is_visible()
    assert pw_page.get_by_text('Кофе вкусный, но заказ ждал сорок минут.', exact=True).is_visible()
