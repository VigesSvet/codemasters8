"""PDF сигнальной карточки (A4) на reportlab.

Шрифт — Noto Sans (кириллица, казахские буквы, ₸; лицензия OFL, fonts/OFL.txt). Эмодзи и прочих символов,
которых нет в шрифте, в PDF не будет: они вырезаются перед выводом, чтобы не печатались «квадратики».
Время сервера выводится по Алматы (risk_model.ALMATY, фиксированное UTC+5 — в slim-образе может не быть tzdata).
Длинные списки доказательств и комментарии — отдельные абзацы вне ячеек таблиц: они переносятся постранично.
"""
import math
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .risk_model import ALMATY, fmt_date

Obj = Dict[str, Any]

FONTS = Path(__file__).with_name("fonts")
FONT, BOLD = "NotoSans", "NotoSans-Bold"
MARGIN = 16 * mm
WIDTH = A4[0] - 2 * MARGIN
CODE_W = 12 * mm  # колонка «Код» в таблице факторов

DISCLAIMER = ("Предварительная оценка по открытым данным и публичной рекламе. Рекомендация для проверки, не юридическое "
              "заключение. Автоматической отправки в госорганы нет.")
DEMO_NOTE = "Демонстрационные данные (вымышленный ЖК). "  # демо-строки прежних версий (до python -m app.demo purge)

INK, MUTED = colors.HexColor("#1f1f1f"), colors.HexColor("#6b6b6b")
LINE, SOFT = colors.HexColor("#d9d9d9"), colors.HexColor("#f3f3f3")
ZONES = {  # подпись, цвет текста, фон
    "red": ("Красная зона", colors.HexColor("#c5221f"), colors.HexColor("#fdecea")),
    "yellow": ("Жёлтая зона", colors.HexColor("#8a6100"), colors.HexColor("#fff4d6")),
    "green": ("Зелёная зона", colors.HexColor("#137333"), colors.HexColor("#e6f4ea")),
}
STATES = {"hit": ("подтверждён", colors.HexColor("#c5221f")), "clear": ("нет", colors.HexColor("#137333")),
          "unknown": ("нужна проверка", colors.HexColor("#8a6100"))}
OBS_STATES = {"ok": "в норме", "flag": "внимание", "unknown": "нет данных"}
SIGNAL_STATUS = {"new": "Новый — ожидает решения аналитика", "confirmed": "Подтверждён", "returned": "Возвращён на доработку"}
AD_STATUS = {"new": "новая", "needs_check": "нужна проверка", "in_review": "на проверке", "confirmed": "подтверждена",
             "dismissed": "отклонена"}
APPEAL_STATUS = {"verified": "проверено", "pending": "на проверке", "rejected": "отклонено"}
DOC_STATE = {"yes": "есть", "no": "нет", "unknown": "нет данных"}
PLATFORMS = {"instagram": "Instagram", "tiktok": "TikTok", "krisha": "Krisha.kz", "olx": "OLX", "telegram": "Telegram",
             "facebook": "Facebook", "2gis": "2ГИС", "other": "Другая площадка"}
_SUBST = {"→": "->", "←": "<-", "✓": "+", "✔": "+", "●": "•", "★": "*", "\u00a0": " "}


# ---------- шрифт и текст ----------

def _fonts() -> set:
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, str(FONTS / "NotoSans-Regular.ttf")))
        pdfmetrics.registerFont(TTFont(BOLD, str(FONTS / "NotoSans-Bold.ttf")))
        pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=BOLD, italic=FONT, boldItalic=BOLD)
    return set(pdfmetrics.getFont(FONT).face.charToGlyph)


_GLYPHS: set = set()


def _glyphs() -> set:
    if not _GLYPHS:
        _GLYPHS.update(_fonts())
    return _GLYPHS


def clean(x: Any) -> str:
    """Убирает символы вне BMP, U+FE0F/U+200D и всё, чего нет в шрифте."""
    glyphs, out = _glyphs(), []
    for ch in "" if x is None else str(x):
        o = ord(ch)
        if o > 0xFFFF or ch in "\ufe0f\u200d":
            continue
        if ch in "\n " or o in glyphs:
            out.append(ch)
        elif ch in _SUBST:
            out.append(_SUBST[ch])
        elif ch == "\t":
            out.append(" ")
    return "".join(out)


def esc(x: Any) -> str:
    return escape(clean(x)).replace("\n", "<br/>")


def line(x: Any, limit: int) -> str:
    """Одна строка без переводов и лишних пробелов, не длиннее limit (по словам, с «…») — для ячеек таблиц:
    строка таблицы не переносится на следующую страницу, длинный текст в ячейке ронял сборку (LayoutError)."""
    t = " ".join(clean(x).split())
    return t if len(t) <= limit else (t[:limit - 1].rsplit(" ", 1)[0] or t[:limit - 1]) + "…"


def when(value: Any) -> str:
    """ДД.ММ.ГГГГ для дат, ДД.ММ.ГГГГ ЧЧ:ММ для моментов. Время сервера (с UTC-смещением или наивное datetime) — по
    Алматы; наивная строка из источника (дата публикации) — как есть, это уже местное время."""
    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        d = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return d.astimezone(ALMATY).strftime("%d.%m.%Y %H:%M")
    s = str(value)
    if len(s) <= 10:
        return fmt_date(s)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    return (d.astimezone(ALMATY) if d.tzinfo else d).strftime("%d.%m.%Y %H:%M")


# ---------- стили ----------

def _style(name: str, **kw: Any) -> ParagraphStyle:
    base = dict(fontName=FONT, fontSize=9, leading=12, textColor=INK, splitLongWords=1)
    return ParagraphStyle(name, **{**base, **kw})


S = {
    "h1": _style("h1", fontName=BOLD, fontSize=17, leading=21),
    "sub": _style("sub", fontSize=10, leading=13),
    "h2": _style("h2", fontName=BOLD, fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=4, keepWithNext=1),
    "h3": _style("h3", fontName=BOLD, fontSize=9, leading=12, spaceBefore=4, keepWithNext=1),
    "p": _style("p"),
    "cell": _style("cell", fontSize=8.5, leading=11),
    "bold": _style("bold", fontName=BOLD, fontSize=8.5, leading=11),
    "small": _style("small", fontSize=7.5, leading=10, textColor=MUTED),
    "ev": _style("ev", fontSize=8, leading=10.5, leftIndent=8, bulletIndent=0, bulletFontName=FONT),
    "fx": _style("fx", fontSize=8.5, leading=11, leftIndent=CODE_W + 4, rightIndent=4),
    "fev": _style("fev", fontSize=8, leading=10.5, leftIndent=CODE_W + 12, bulletIndent=CODE_W + 4, rightIndent=4,
                  bulletFontName=FONT),
    "score": _style("score", fontName=BOLD, fontSize=22, leading=26),
}


def P(text: str, style: str = "cell") -> Paragraph:
    return Paragraph(text, S[style])


def _grid(rows: List[list], widths: List[float], header: bool = False) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), SOFT)]
    t.setStyle(TableStyle(st))
    return t


def _kv(rows: List[tuple]) -> Table:
    return _grid([[P(esc(k), "bold"), v if isinstance(v, Paragraph) else P(v)] for k, v in rows], [46 * mm, WIDTH - 46 * mm])


def _box(text: str, fg, bg) -> Table:
    t = Table([[P(f'<font color="{fg.hexval()}">{text}</font>', "cell")]], colWidths=[WIDTH])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.6, fg),
                           ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


# ---------- содержание ----------

def _join(*parts: Any) -> str:
    return " · ".join(esc(p) for p in parts if p not in (None, "", "—"))


def _pct(v: Any) -> str:
    return f"{v}%" if v not in (None, "") else "—"


def link(url: Any) -> str:
    """Ссылка на источник: http(s) — кликабельная в PDF, прочее — текстом."""
    u = clean(url).strip()
    if not u:
        return ""
    if not u.lower().startswith(("http://", "https://")):
        return esc(u)
    return f'<a href="{escape(u, {chr(34): "&quot;"})}" color="#1a56db">{esc(u)}</a>'


def _document(d: Obj) -> str:
    """Документ-основание с провенансом по документу (T-29): реквизит, источник, дата сверки, ссылка на источник.
    Документ без источника и даты не проверялся — так и пишется, «нет» без источника не выдаётся за факт."""
    state = DOC_STATE.get(str(d.get("value")), d.get("value"))
    head = f"<b>{esc(d.get('label'))}</b>: {esc(state)}"
    checked = d.get("source") or d.get("date")
    tail = _join(d.get("quote") and f"реквизит: {d['quote']}", d.get("source") and f"источник: {d['source']}",
                 d.get("date") and f"сверено {when(d['date'])}",
                 None if checked else ("не проверялся" if str(d.get("value")) in ("unknown", "None") else "источник не указан"))
    url = link(d.get("url"))
    tail = " · ".join(x for x in (tail, url) if x)
    return head + (f" — {tail}" if tail else "")


DEMO_DEV = "вымышленная организация (демо)"
DEMO_COMPLEX = "вымышленный ЖК (демо)"


def _demo(x: Optional[Obj]) -> bool:
    """Демо-сущность: origin 'demo'; в снимках сигналов до блока E поля нет — тогда тоже демо (других ЖК в реестре нет)."""
    return bool(x) and x.get("origin", "demo") == "demo"


def _item(it: Obj) -> str:
    k = it.get("kind")
    if k == "ad":
        q = f"«{esc(it.get('quote'))}»" if it.get("quote") else "Публикация"
        meta = _join(PLATFORMS.get(it.get("source"), it.get("source")), it.get("label"), when(it.get("date")),
                     it.get("marker") and f"маркер «{it['marker']}»",
                     "цитата со скриншота (OCR)" if it.get("quoteFrom") == "ocr" else None)
        return f"{q} — {meta}" + (f"<br/>{esc(it['url'])}" if it.get("url") else "")
    if k == "document":
        return _document(it)
    if k == "appeal":
        return f"Обращение: {esc(it.get('label'))} — " + _join(APPEAL_STATUS.get(it.get("value"), it.get("value")),
                                                             it.get("source"), when(it.get("date")))
    if k == "readiness":
        return f"{esc(it.get('label'))}: {esc(_pct(it.get('value')))} — " + _join(it.get("source"), when(it.get("date")))
    if k == "deadline":
        return f"{esc(it.get('label'))}: {esc(when(it.get('date')))}"
    if k == "photo":
        return "Снимок " + _join(when(it.get("date")), it.get("source"), it.get("value") and f"активность: {it['value']}",
                                 it.get("label") if it.get("label") != it.get("value") else None)
    return _join(it.get("label"), it.get("quote"), it.get("source"), when(it.get("date")))


def _header(sig: Obj, generated_at: datetime) -> list:
    c = sig["evidence"]["complex"]
    return [
        P(f"Сигнальная карточка {esc(sig['code'])}", "h1"),
        P(f"ЖК «{esc(c.get('name'))}» · {esc(c.get('address'))}", "sub"),
        P(_join(f"Сформирована {when(generated_at)} (Алматы, UTC+5)", f"сигнал создан {when(sig['createdAt'])}",
                f"модель {sig['modelVersion']}"), "small"),
        Spacer(1, 6),
        _box(esc((DEMO_NOTE if _demo(c) else "") + DISCLAIMER), colors.HexColor("#8a6100"), colors.HexColor("#fff4d6")),
    ]


def _sourced(x: Obj) -> str:
    """«58% — источник · дата» для значения готовности {value, source, date}."""
    tail = _join(x.get("source"), x.get("date") and when(x["date"]))
    return esc(_pct(x.get("value"))) + (f" — {tail}" if tail else ", без источника")


def readiness_lines(c: Obj) -> List[str]:
    """Стадия и готовность: «подтверждённая готовность» — только из readiness.confirmed (с источником и датой);
    процент из карточки объекта без источника подтверждением не называется."""
    r = c.get("readiness") or {}
    conf, decl = r.get("confirmed"), r.get("declared")
    stage = esc(c.get("stage")) or "стадия не указана"
    if conf:
        head = f"{stage}, подтверждённая готовность {_sourced(conf)}"
    elif c.get("progress") is not None:
        head = f"{stage}; готовность по карточке объекта {esc(_pct(c['progress']))}, без источника"
    else:
        head = stage
    out = [head, "Заявлено: " + (_sourced(decl) if decl else "нет данных")]
    return out if conf else out + ["Подтверждено: нет данных"]


def _object(sig: Obj) -> list:
    ev = sig["evidence"]
    c, dev = ev["complex"], ev.get("developer")
    ready = readiness_lines(c)
    docs = ev.get("documents") or []
    return [
        P("Объект", "h2"),
        _kv([
            ("Название", P(_join(f"ЖК «{c.get('name')}»", DEMO_COMPLEX if _demo(c) else None))),
            ("Адрес", P(_join(c.get("address"), c.get("district") and f"р-н {c['district']}"))),
            ("Застройщик", P(_join(dev.get("name"), f"БИН {dev.get('bin')}", DEMO_DEV if _demo(dev) else None)
                             if dev else "нет данных")),
            ("Стадия и готовность", P("<br/>".join(ready))),
            ("Срок сдачи", P(_join(f"заявлен {when(c.get('plannedEnd'))}", c.get("start") and f"начало {when(c['start'])}"))),
            ("Основание привлечения средств", P("<br/>".join(_document(d) for d in docs) if docs else "нет данных")),
        ]),
    ]


def _risk(sig: Obj) -> list:
    label, fg, bg = ZONES.get(sig["zone"], ZONES["green"])
    unknown = [f for f in sig["factors"] if f["state"] == "unknown"]
    check = "; ".join(f"{esc(f['code'])} — {esc(f['title'])}" for f in unknown) if unknown else "нет"
    left = [P(f'{sig["score"]} <font size="10">из 100</font>', "score"),
            P(f'<font color="{fg.hexval()}"><b>{esc(label)}</b></font>')]
    right = [P(esc(sig["summary"]), "p"), Spacer(1, 3), P(f"<b>Нужна проверка:</b> {check}")]
    t = Table([[left, right]], colWidths=[40 * mm, WIDTH - 40 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), bg), ("BOX", (0, 0), (-1, -1), 0.6, fg),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return [P("Оценка риска", "h2"), t]


FACTOR_W = [CODE_W, WIDTH - CODE_W - 30 * mm - 22 * mm, 30 * mm, 22 * mm]


def _row(cells: list, *style: tuple) -> Table:
    """Одна строка таблицы факторов (те же колонки у заголовка, факторов и итога)."""
    t = Table([cells], colWidths=FACTOR_W)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3), *style]))
    return t


def _factors(sig: Obj) -> list:
    head = _row([P("Код", "bold"), P("Фактор", "bold"), P("Статус", "bold"), P("Баллы", "bold")],
                ("BACKGROUND", (0, 0), (-1, -1), SOFT), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE))
    out: list = [KeepTogether([P("Факторы риска R1–R6 (ТЗ §5)", "h2"), head])]
    for f in sig["factors"]:
        label, color = STATES.get(f["state"], (f["state"], INK))
        got = f["points"] if f["state"] == "hit" else 0
        row = _row([P(f"<b>{esc(f['code'])}</b>"), P(f"<b>{esc(f['title'])}</b>"),
                    P(f'<font color="{color.hexval()}">{esc(label)}</font>'), P(f"{got} из {f['points']}")],
                   ("BOTTOMPADDING", (0, 0), (-1, -1), 1))
        items = [Paragraph(_item(it), S["fev"], bulletText="–") for it in f.get("items") or []]
        # заголовок фактора не отрывается от пояснения и первого доказательства, остальные переносятся свободно
        out += [KeepTogether([row, P(esc(f["evidence"]), "fx")] + items[:1]), *items[1:],
                HRFlowable(width="100%", thickness=0.4, color=LINE, spaceBefore=4, spaceAfter=0)]
    out.append(_row(["", P("<b>Итого</b> (сумма весов подтверждённых факторов, не больше 100)"), "",
                     P(f"<b>{sig['score']}</b>")], ("BACKGROUND", (0, 0), (-1, -1), SOFT)))
    return out


def _observations(sig: Obj) -> list:
    obs = sig.get("observations") or []
    if not obs:
        return []
    rows = [[P("Код", "bold"), P("Наблюдение", "bold"), P("Состояние", "bold"), P("Пояснение", "bold")]]
    rows += [[P(esc(o["code"])), P(esc(o["title"])), P(esc(OBS_STATES.get(o["state"], o["state"]))), P(esc(o["text"]))]
             for o in obs]
    return [KeepTogether([P("Наблюдения вне балла", "h2"),
                          P("Не влияют на балл и зону — контекст для аналитика.", "small"), Spacer(1, 2),
                          _grid(rows, [12 * mm, 52 * mm, 22 * mm, WIDTH - 86 * mm], header=True)])]


def _materials(sig: Obj) -> list:
    ev = sig["evidence"]
    out: list = [P("Материалы (снимок на момент создания сигнала)", "h2")]
    ads = ev.get("ads") or []
    lines = []
    for a in ads:
        head = _join(PLATFORMS.get(a.get("platform"), a.get("platform")), a.get("account"), when(a.get("date")),
                     AD_STATUS.get(a.get("status"), a.get("status")), link_text(a),
                     "условия оплаты: аналитик отклонил — не предложение внести деньги"
                     if a.get("termsReview") == "rejected" else None)
        lines.append(Paragraph(f"<b>{head}</b><br/>{esc(line(a.get('text'), 320))}"
                               + (f"<br/>{esc(a['url'])}" if a.get("url") else ""), S["ev"], bulletText="–"))
    rule = ("Привязка к ЖК — факт только после подтверждения аналитиком; гипотеза ИИ (с уверенностью) в балл не входит. "
            "Если аналитик отклонил условия оплаты («не предложение внести деньги»), публикация не даёт R2.")
    out += [P("Публикации", "h3"), P(esc(rule), "small")] + (lines or [P("Привязанных публикаций нет", "small")])
    apps = ev.get("appeals") or []
    out += [P("Обращения граждан", "h3")]
    out += [Paragraph(_join(when(a.get("date")), a.get("subject"), f"заявителей: {a.get('applicants')}",
                            APPEAL_STATUS.get(a.get("status"), a.get("status")), a.get("source")), S["ev"], bulletText="–")
            for a in apps] or [P("Обращений нет", "small")]
    photos = ev.get("photos") or []
    out += [P("Снимки стройки", "h3")]
    out += [Paragraph(_join(when(p.get("date")), p.get("source"), p.get("activity") and f"активность: {p['activity']}",
                            p.get("note")), S["ev"], bulletText="–") for p in photos] or [P("Снимков нет", "small")]
    return out


def link_text(a: Obj) -> str:
    """Привязка публикации к ЖК в материалах: факт (кто и когда подтвердил) или гипотеза ИИ с уверенностью."""
    if a.get("linkConfirmedBy"):
        at = a.get("linkConfirmedAt")
        return f"привязка — факт (подтвердил {line(a['linkConfirmedBy'], 64)}{', ' + when(at) if at else ''})"
    if a.get("status") == "confirmed":
        return "привязка — факт (нарушение подтверждено аналитиком)"
    conf = math.floor((a.get("matchConfidence") or 0) * 100 + 0.5)
    return f"привязка — гипотеза ИИ {conf}%, в балл не входит"


def _status(sig: Obj) -> list:
    decided = (_join(line(sig.get("decidedBy"), 128), when(sig.get("decidedAt"))) if sig.get("decidedAt")
               else "ещё не принято")
    table = _kv([
        ("Статус", P(f"<b>{esc(SIGNAL_STATUS.get(sig['status'], sig['status']))}</b>")),
        ("Создан", P(_join(line(sig.get("createdBy"), 128), when(sig.get("createdAt"))))),
        ("Решение", P(decided)),
        ("Выгрузка", P(_join(sig.get("fileName"), sig.get("exportedAt") and when(sig["exportedAt"])))),
    ])
    # комментарий (до 4000 символов) — абзацами вне таблицы, чтобы переносился на следующую страницу
    comment = [P(esc(x.strip()), "p") for x in (sig.get("decisionComment") or "").splitlines() if x.strip()]
    comment = comment or [P("—", "p")]
    return [KeepTogether([P("Статус проверки", "h2"), table, P("Комментарий решения", "h3"), comment[0]]), *comment[1:]]


def _journal(sig: Obj) -> list:
    """Журнал событий; длинный текст события обрезан — полный комментарий решения есть в «Статусе проверки»."""
    rows = [[P("Когда", "bold"), P("Кто", "bold"), P("Событие", "bold")]]
    rows += [[P(esc(when(e["at"]))), P(esc(line(e["who"], 128))), P(esc(line(e["what"], 300)))]
             for e in sig.get("journal") or []]
    return [P("Журнал", "h2"), _grid(rows, [30 * mm, 40 * mm, WIDTH - 70 * mm], header=True)]


# ---------- сборка ----------

def _canvas(footer: str):
    class NumberedCanvas(Canvas):
        """Подвал «стр. N из M»: страницы копятся и дорисовываются при save, когда известно их число."""

        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            self._pages: List[dict] = []

        def showPage(self) -> None:
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont(FONT, 7.5)
                self.setFillColor(MUTED)
                self.setStrokeColor(LINE)
                self.line(MARGIN, 11 * mm, A4[0] - MARGIN, 11 * mm)
                self.drawString(MARGIN, 7.5 * mm, footer)
                self.drawRightString(A4[0] - MARGIN, 7.5 * mm, f"стр. {self._pageNumber} из {total}")
                super().showPage()
            super().save()

    return NumberedCanvas


def story(sig: Obj, generated_at: datetime) -> list:
    """Содержимое карточки — список flowable (отдельно от сборки, чтобы проверять текст в тестах)."""
    _glyphs()
    return (_header(sig, generated_at) + _object(sig) + [KeepTogether(_risk(sig))] + _factors(sig)
            + _observations(sig) + _materials(sig) + _status(sig) + _journal(sig))


def render_signal(sig: Obj, generated_at: Optional[datetime] = None) -> bytes:
    """Сигнал в форме API (camelCase, как Signal в types.ts) → PDF."""
    generated_at = generated_at or datetime.now(timezone.utc)
    content = story(sig, generated_at)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
                            bottomMargin=MARGIN + 2 * mm, title=f"Сигнальная карточка {sig['code']}",
                            author="SAQ Qurylys", subject=clean(DISCLAIMER), creator="SAQ Qurylys")
    demo = " · демонстрационные данные" if _demo(sig["evidence"]["complex"]) else ""
    footer = clean(f"SAQ Qurylys · сигнал {sig['code']} · модель риска {sig['modelVersion']}{demo}")
    doc.build(content, canvasmaker=_canvas(footer))
    return buf.getvalue()
