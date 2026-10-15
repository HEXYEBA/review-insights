"""Presentation helpers. All source and model text is escaped before HTML rendering."""

from dataclasses import dataclass, field
from html import escape
from time import time

from review_insights.models import AnalysisPreview, CollectionResult
from review_insights.selection import normalize_text


APP_CSS = """
<style>
:root{--ink:#20202c;--accent:#6d4aff;--muted:#656577;--line:#e4e4ed;--surface:#fff;color-scheme:light}
.stApp{background:#f7f7fa;color:var(--ink)}
.stApp,.stApp button,.stApp input,.stApp textarea{font-family:'Aptos','Segoe UI Variable Text','Segoe UI',sans-serif}
[data-testid="stHeader"]{background:#f7f7faf0;border-bottom:1px solid #eaeaf1;backdrop-filter:blur(18px)}
[data-testid="stAppDeployButton"],[data-testid="stMainMenu"]{display:none!important}
[data-testid="stStatusWidget"]{display:none!important}
.stMainBlockContainer{max-width:1180px;padding:5.2rem 2.5rem 5rem}
h1,h2,h3{font-family:'Segoe UI Variable Display','Segoe UI',sans-serif!important;color:var(--ink)!important}
h1{font-size:clamp(2.7rem,4.5vw,4.7rem)!important;font-weight:650!important;letter-spacing:-.065em;line-height:1.06!important;padding-bottom:1.25rem!important}
h2{font-size:1.8rem!important;font-weight:650!important;letter-spacing:-.035em;margin-top:1.5rem!important}
h3{font-size:1.2rem!important;letter-spacing:-.02em}
.stApp [data-testid="stMarkdownContainer"]{color:var(--ink)}
[data-testid="stCaptionContainer"],[data-testid="stCaptionContainer"] p{color:var(--muted)!important;line-height:1.7}
.st-key-hero{margin:2.1rem 0 1.5rem}
.st-key-examples{max-width:300px}
.st-key-examples [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important}
.st-key-examples [data-testid="stColumn"]{min-width:0!important;flex:1!important}
.st-key-hero [data-testid="stMarkdownContainer"] p{font-size:1.14rem;line-height:1.65;color:#656577;max-width:41ch}
.ri-brand{display:flex;align-items:center;justify-content:space-between;color:#656577;font-size:13px;gap:1rem}
.ri-brand-name{display:flex;gap:10px;align-items:center;font-size:17px;font-weight:650;letter-spacing:-.035em;color:#20202c}
.ri-mark{display:grid;place-items:center;width:32px;height:32px;border-radius:10px;color:white;background:#6d4aff;font-size:27px;font-family:Georgia,serif;box-shadow:0 4px 12px #6d4aff24}
.ri-brand-note{border:1px solid #e4e4ed;border-radius:30px;padding:7px 13px;background:#ffffffa0}
.ri-art{position:relative;height:270px;margin-top:0;isolation:isolate}
.ri-art:before{content:'';position:absolute;inset:0;background:radial-gradient(ellipse,#dfd7ff9c,transparent 68%);z-index:-1}
.ri-art svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.ri-art path{stroke:#bcb0ef;stroke-width:1.2;fill:none;stroke-dasharray:4 6;animation:flow 26s linear infinite}
.ri-art-node{position:absolute;background:#ffffffeb;border:1px solid #e4e1f0;border-radius:14px;padding:14px 18px;color:#30303f;font-size:14px;box-shadow:0 10px 30px #3f2c7510;display:flex;align-items:center;gap:12px}
.ri-art-node b{font-weight:600}.ri-art-node span{display:grid;place-items:center;width:30px;height:30px;border-radius:9px;background:#f0ecff;color:#6d4aff;font-size:18px}
.ri-art .reviews{left:0;top:24px;transform:rotate(-5deg);animation:float 7s ease-in-out infinite}
.ri-art .themes{right:0;top:107px;transform:rotate(4deg);animation:float 7s ease-in-out 1s infinite}
.ri-art .decision{left:16%;bottom:9px;background:#6d4aff;color:white;border-color:#6d4aff;box-shadow:0 10px 34px #6d4aff30}
.ri-art .decision span{background:#ffffff25;color:white}
.ri-art-dot{position:absolute;left:50%;top:43%;width:10px;height:10px;background:#9177ff;border:3px solid #eee8ff;border-radius:50%;box-shadow:0 0 0 7px #ece6ff}
[data-testid="stForm"]{background:#fff;border:1px solid #e4e4ed;border-radius:22px;padding:1.6rem 1.7rem;box-shadow:0 12px 40px #29204506}
.stApp [data-testid="stWidgetLabel"],.stApp [data-testid="stWidgetLabel"] p{color:#363642!important;font-size:14px;font-weight:550}
.stApp [data-testid="stTextInput"] [data-baseweb="input"]{background:#fafafd!important;border:1px solid #dcdce7!important;border-radius:12px;min-height:60px;transition:border-color .2s,box-shadow .2s}
.stApp [data-testid="stTextInput"] [data-baseweb="base-input"]{background:transparent!important}
.stApp [data-testid="stTextInput"] input{background:transparent!important;color:#20202c!important;-webkit-text-fill-color:#20202c!important;caret-color:#6d4aff!important;font-size:16px;padding:17px 16px;min-height:56px;opacity:1!important}
.stApp [data-testid="stTextInput"] input::placeholder{-webkit-text-fill-color:#9292a3!important;color:#9292a3!important;opacity:1}
.stApp [data-testid="stTextInput"] [data-baseweb="input"]:focus-within{border-color:#856bff!important;box-shadow:0 0 0 4px #6d4aff12}
.stApp input::selection{background:#dcd2ff;color:#20202c}
.stApp button:not([data-testid="stExpandSidebarButton"]){border:1px solid #e1e1ea!important;background:#fff!important;color:#454552!important;border-radius:11px;min-height:43px;transition:background .2s,box-shadow .2s,transform .2s,border-color .2s}
.stApp button p{color:inherit!important;font-weight:550}
.stApp button [data-testid="stMarkdownContainer"]{color:inherit!important}
.stApp button:hover{background:#f3f0ff!important;border-color:#b9aaff!important;transform:translateY(-1px);box-shadow:0 5px 15px #6d4aff0c}
.stApp button:active{transform:translateY(0)}
.stApp button:focus-visible{outline:3px solid #b9aaff;outline-offset:3px}
.stApp button[kind="primaryFormSubmit"],.stApp button[kind="primary"]{color:#fff!important;background:#6d4aff!important;border-color:#6d4aff!important;box-shadow:0 4px 12px #6d4aff24}
.stApp button[kind="primaryFormSubmit"]:hover,.stApp button[kind="primary"]:hover{background:#5d3be9!important;box-shadow:0 6px 20px #6d4aff36}
[data-testid="stForm"] button{margin-top:3px}
[data-testid="stMetric"]{padding:12px 0 14px;border-bottom:1px solid #e4e4ed}
[data-testid="stMetricLabel"],[data-testid="stMetricLabel"] p{color:#656577!important}
[data-testid="stMetricValue"]{color:#20202c!important;font-weight:550;letter-spacing:-.045em}
[data-testid="stExpander"]{border:1px solid #e4e4ed!important;background:#fff!important;border-radius:13px;transition:border-color .2s}
[data-testid="stExpander"]:hover{border-color:#c3b7ef!important}
[data-testid="stExpander"] summary,[data-testid="stExpander"] summary p{color:#30303f!important;font-weight:600}
[data-testid="stExpanderDetails"]{color:#20202c!important}
.stApp [data-testid="stAlert"]{background:#f0edf8!important;color:#39304f!important;border:1px solid #e0d8f7}
.stApp [data-testid="stLinkButton"] a{background:#fff!important;color:#454552!important;border:1px solid #e1e1ea!important;border-radius:11px}
.ri-copy{font-size:1.06rem;line-height:1.8;max-width:75ch;white-space:pre-wrap;overflow-wrap:anywhere;color:#363642}
.ri-summary{border-left:3px solid #8c72f4;background:#f0ecff;padding:1.5rem 1.7rem;border-radius:0 16px 16px 0}
.ri-quote{margin:1rem 0;border-left:2px solid #c7b9f6;padding:.8rem 1rem;background:#f8f6ff;color:#56506b;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}
.ri-action{font-size:1.1rem;font-weight:650;margin:1.1rem 0 .5rem;color:#20202c;letter-spacing:-.02em}
.ri-preview{border:1px solid #e4e4ed;border-radius:18px;padding:1.2rem 1.5rem;background:#fff;color:#30303f}
.ri-preview-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:.5rem;font-weight:650}
.ri-preview-note{font-size:.86rem;color:#656577;margin:.5rem 0 1rem}
.ri-preview article{padding:.9rem 0;border-top:1px solid #eeeef4}.ri-preview article p{margin:.35rem 0 0;line-height:1.65;overflow-wrap:anywhere}
.ri-preview small{color:#656577}
.ri-stage-preview{border:1px solid #e1daf5;border-radius:18px;background:#fff;padding:1.35rem 1.5rem;color:#363642}
.ri-stage-preview h4{font-size:1.05rem;color:#3c315a;margin:0 0 .6rem;letter-spacing:-.02em}
.ri-stage-preview p{line-height:1.65;margin:.5rem 0;overflow-wrap:anywhere}
.ri-stage-stats{display:flex;flex-wrap:wrap;gap:.5rem;margin:1rem 0}
.ri-stage-stats span{padding:6px 11px;border-radius:8px;background:#f3f0fc;color:#544374;font-size:13px}
.ri-stage-note{font-size:13px;color:#656577}.ri-draft{border-left:3px solid #cab9fa;padding-left:1rem;margin:1rem 0}
.ri-draft-label{font-size:12px;color:#806515;font-weight:600}
.ri-compare{display:grid;grid-template-columns:1fr 1fr;gap:1rem;border-top:1px solid #eeeaf5;padding-top:.8rem;margin-top:1rem}
.ri-compare small{color:#656577}.ri-compare p{font-size:14px;white-space:pre-wrap}.ri-match{font-size:13px;font-weight:600;color:#41694e}.ri-mismatch{font-size:13px;font-weight:600;color:#9a6240}
@media(max-width:600px){.ri-stage-preview{padding:1.1rem}.ri-compare{grid-template-columns:1fr}}
@keyframes float{50%{translate:0 -7px}}@keyframes flow{to{stroke-dashoffset:-140}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
@media(max-width:800px){.ri-art{height:220px}.ri-art-node{font-size:12px;padding:10px}.ri-brand-note{display:none}}
@media(max-width:600px){.stMainBlockContainer{padding:5rem 1.2rem 3rem}.st-key-hero{margin:1.5rem 0 .6rem}.st-key-hero [data-testid="stColumn"]:last-child{display:none}h1{font-size:2.9rem!important}.ri-art{display:none}[data-testid="stForm"]{padding:1.2rem;border-radius:18px}.ri-summary{padding:1.1rem}.ri-brand{font-size:12px}}
</style>
"""

BRAND_HTML = '<div class="ri-brand"><div class="ri-brand-name"><span class="ri-mark">”</span>Review Insights</div><span class="ri-brand-note">Яндекс Карты · анализ отзывов</span></div>'

HERO_ART = """<div class="ri-art" aria-hidden="true">
<svg viewBox="0 0 400 270" preserveAspectRatio="none"><path d="M95 55 C300 30 150 140 300 135 S250 225 140 235"/></svg>
<div class="ri-art-node reviews"><span>☷</span><b>Отзывы гостей</b></div>
<i class="ri-art-dot"></i>
<div class="ri-art-node themes"><span>⌕</span><b>Темы и цитаты</b></div>
<div class="ri-art-node decision"><span>↗</span><b>Понятные действия</b></div>
</div>"""


@dataclass
class ProcessView:
    stage: int = 0
    label: str = "Собираем отзывы…"
    started_at: float = field(default_factory=time)
    finished_at: float | None = None
    failed: bool = False
    cached: bool = False
    completed: bool = False

    def update(self, label: str) -> None:
        self.label = label
        if label.startswith("Анализируем"):
            self.stage = 1
        elif label.startswith(("Проверяем", "Модель исправляет")):
            self.stage = 2

    def complete(self, *, cached: bool = False) -> None:
        self.cached = cached
        self.completed = True
        self.finished_at = time()
        self.stage = 3
        self.label = "Сохранённый отчёт открыт" if cached else "Анализ завершён"

    def fail(self) -> None:
        self.failed = True
        self.finished_at = time()
        self.label = "Не удалось завершить этап. Подробности ниже."


def render_process(view: ProcessView) -> str:
    """Isolated iframe: a real elapsed clock and animations, no external assets."""
    if view.cached:
        return '<div style="font:15px Segoe UI,sans-serif;color:#6d4aff;padding:18px 0">📂 Сохранённый отчёт открыт · без повторного сбора и анализа</div>'
    steps = []
    for index, (icon, title, detail) in enumerate([
        ("🔎", "Сбор", "Получаем отзывы"),
        ("🧠", "Анализ", "Находим темы"),
        ("🔍", "Сверка", "Отчёт и отзывы"),
        ("✅", "Отчёт", "Готов к чтению"),
    ]):
        state = "waiting"
        if index < view.stage or view.completed:
            state = "done"
        elif index == view.stage:
            state = "error" if view.failed else "active"
        current = ' aria-current="step"' if state in {"active", "error"} else ""
        steps.append(
            f'<li data-step="{index}" data-state="{state}"{current}>'
            f'<span class="node">{icon}</span><div><strong>{title}</strong><small>{detail}</small></div></li>'
        )
    frozen = int(((view.finished_at or time()) - view.started_at))
    running = "false" if view.finished_at else "true"
    note = {
        0: "До 200 доступных отзывов. Первые тексты появятся ниже по мере загрузки.",
        1: "Модель читает до 50 отзывов. Обычно это несколько минут; до 5 минут на ответ.",
        2: "Сравниваем цитаты из отчёта с исходными отзывами и проверяем ссылки. При расхождении модель исправит черновик — до 5 минут.",
        3: "Выводы и подтверждающие цитаты — ниже. Отчёт можно скачать.",
    }[view.stage]
    if view.failed:
        note = "Повторный запуск доступен через форму. Предыдущий сохранённый отчёт можно открыть отдельно."
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;color:#20202c;font:15px 'Segoe UI',sans-serif;background:transparent}
.panel{border:1px solid #e4e4ed;background:white;border-radius:18px;padding:20px 22px}
ol{display:grid;grid-template-columns:repeat(4,1fr);list-style:none;padding:0;margin:0 0 22px}
li{position:relative;display:flex;gap:10px;align-items:center;color:#6a7c92}
li:not(:last-child):after{content:'';position:absolute;left:42px;right:10px;top:18px;height:2px;background:#e2e9f2;z-index:0}
li[data-state=done]:after{background:#6d4aff;transform-origin:left;animation:connect .55s ease-out}
.node{position:relative;z-index:1;display:grid;place-items:center;width:38px;height:38px;border-radius:50%;background:#edf2f8;border:2px solid #e0e7f0;flex-shrink:0}
li div{position:relative;z-index:1;background:white;padding-right:8px}strong{display:block;font-size:15px}small{display:block;font-size:11px;margin-top:3px}
li[data-state=active],li[data-state=done]{color:#6d4aff}
li[data-state=active] .node{border-color:#6d4aff;background:#f0ecff;animation:pulse 2s ease-in-out infinite}
li[data-state=done] .node{border-color:#6d4aff;background:#f0ecff}
li[data-state=error]{color:#ae343f}li[data-state=error] .node{border-color:#ae343f;background:#fff0f1}
.status{display:flex;justify-content:space-between;gap:12px;align-items:start}.label{font-weight:650}.timer{font-variant-numeric:tabular-nums;white-space:nowrap;color:#656577;font-size:13px}
p{color:#656577;font-size:13px;line-height:1.5;margin:7px 0 0}
@keyframes pulse{50%{box-shadow:0 0 0 6px #6d4aff13}}@keyframes connect{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media(max-width:600px){.panel{padding:16px 12px}li{flex-direction:column;gap:6px;text-align:center}li div{padding:0}li small{display:none}li:not(:last-child):after{left:50%;right:-50%}strong{font-size:13px}ol{margin-bottom:18px}.status{flex-wrap:wrap}}
</style></head><body><section class="panel" aria-label="Ход анализа"><ol>""" + "".join(steps) + f"""</ol>
<div class="status"><span class="label" role="status">{escape(view.label)}</span><span class="timer">Прошло <span id="clock"></span></span></div>
<p>{escape(note)}</p></section><script>
const start={view.started_at * 1000:.0f}, frozen={max(frozen, 0)}, running={running};
function tick(){{const n=running?Math.max(0,Math.floor((Date.now()-start)/1000)):frozen;document.getElementById('clock').textContent=Math.floor(n/60)+':'+String(n%60).padStart(2,'0');}}
tick();if(running)setInterval(tick,1000);
</script></body></html>"""


def render_preview(collection: CollectionResult) -> str:
    samples = []
    for review in collection.reviews[-3:]:
        rating = f"⭐ {review.rating}/5" if review.rating is not None else "Без оценки"
        text = review.text[:260] + ("…" if len(review.text) > 260 else "")
        samples.append(
            f'<article><small>{escape(rating)} · {escape(review.date_label or "Дата не указана")}</small>'
            f'<p>{escape(text)}</p></article>'
        )
    return (
        '<section class="ri-preview" aria-label="Превью собранных отзывов">'
        f'<div class="ri-preview-head"><span>💬 {escape(collection.org_name or "Отзывы из карточки")}</span>'
        f'<span>Собрано: {len(collection.reviews)}</span></div>'
        '<p class="ri-preview-note">Фрагменты реальных отзывов. Это ещё не выводы анализа.</p>'
        + "".join(samples) + '</section>'
    )


def text_html(value: str, *, css_class: str = "ri-copy") -> str:
    return f'<div class="{css_class}">{escape(value)}</div>'


def render_analysis_preview(event: AnalysisPreview) -> str:
    """One real checkpoint per stage; never generate fake intermediate insights."""
    selected = event.selected_reviews
    if event.stage == "analysis":
        high = sum(review.rating is not None and review.rating >= 4 for review in selected)
        middle = sum(review.rating == 3 for review in selected)
        low = sum(review.rating is not None and review.rating <= 2 for review in selected)
        unknown = sum(review.rating is None for review in selected)
        stats = [f"Отобрано: {len(selected)}", f"4–5 звёзд: {high}", f"3 звезды: {middle}", f"1–2 звезды: {low}"]
        if unknown:
            stats.append(f"Без оценки: {unknown}")
        return (
            '<section class="ri-stage-preview" aria-label="Первый срез анализа">'
            '<h4>🧩 Первый срез готов</h4>'
            '<p>Полные тексты отобраны для анализа. Модель ищет повторяющиеся темы, сильные стороны и проблемы.</p>'
            '<div class="ri-stage-stats">' + ''.join(f'<span>{escape(value)}</span>' for value in stats) + '</div>'
            '<p class="ri-stage-note">Это подсчёт оценок только в выбранных отзывах, а не вывод ИИ. Смысл текста может отличаться от оценки. Черновик появится после ответа модели.</p></section>'
        )
    draft = event.draft
    verified = event.stage == "verified"
    title = "✓ Сверка завершена" if verified else "🔍 Сверяем отчёт с отзывами"
    parts = [f'<section class="ri-stage-preview" aria-label="Превью сверки"><h4>{title}</h4>']
    if event.issue:
        parts.append(f'<p class="ri-stage-note">Нашли расхождение: {escape(event.issue)} Модель исправляет ответ.</p>')
    if draft is None:
        parts.append('<p>Ответ модели получен, но готового черновика пока нет. Исправляем структуру ответа.</p></section>')
        return ''.join(parts)
    quote_pairs = [(evidence, next((review for review in selected if review.review_id == evidence.review_id), None))
                   for theme in draft.themes for evidence in theme.evidence]
    matched = sum(review is not None and bool(normalize_text(evidence.quote)) and normalize_text(evidence.quote) in normalize_text(review.text)
                  for evidence, review in quote_pairs)
    parts.append(f'<div class="ri-stage-stats"><span>Тем в черновике: {len(draft.themes)}</span><span>Совпало цитат: {matched} из {len(quote_pairs)}</span></div>')
    parts.append('<p class="ri-stage-note">Сравниваем выдержки, которые ИИ включил в отчёт, с соответствующими исходными отзывами. Проверяем также ссылки на темы и учёт выбранных отзывов.</p>')
    label = 'Проверенный вывод' if verified else 'Черновик ИИ · ещё не итог'
    summary = draft.summary[:500] + ('…' if len(draft.summary) > 500 else '')
    parts.append(f'<div class="ri-draft"><span class="ri-draft-label">{label}</span><p>{escape(summary)}</p></div>')
    if quote_pairs:
        # Prefer a mismatch so the user can understand why a repair is taking time.
        evidence, review = next(((e, r) for e, r in quote_pairs if r is None or normalize_text(e.quote) not in normalize_text(r.text)), quote_pairs[0])
        matches = review is not None and normalize_text(evidence.quote) in normalize_text(review.text)
        source = 'Указанный отзыв не найден в выборке.'
        if review is not None:
            original = normalize_text(review.text)
            location = original.find(normalize_text(evidence.quote))
            start = max(0, location - 55) if location >= 0 else 0
            source = ('…' if start else '') + original[start:start+350] + ('…' if len(original) > start+350 else '')
        parts.append(f'<div class="ri-compare"><div><small>Цитата в отчёте</small><p>{escape(evidence.quote)}</p></div><div><small>Исходный отзыв</small><p>{escape(source)}</p></div></div>')
        result = 'Дословное совпадение найдено' if matches else 'Не найдено дословного совпадения'
        parts.append(f'<p class="{"ri-match" if matches else "ri-mismatch"}">{result}</p>')
    parts.append('<p class="ri-stage-note">Сверка подтверждает опору отчёта на отзывы, а не правдивость описанных в них событий.</p></section>')
    return ''.join(parts)
