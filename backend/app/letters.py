"""Проект запроса сведений по ЖК в ЖАО(МИО) ВКО, Единому оператору жилищного строительства или ГАСК ВКО(T-78).

Письмо собирается из данных карточки ЖК: застройщик и БИН, стадия, документы-основания с источником и датой сверки
(или "не проверялся"), сведения открытых данных, привязанные публикации с цитатами, ссылками и временем фиксации,
нормы ст. 7, 8, 18, 23 Закона РК № 486-V дословно(app.law, shared/law.json), перечень приложений, блок подписи с
должностью(без имён), дата и место под исходящий номер. Это черновик для внутреннего маршрута: автоматической
отправки нет, в журнал объекта пишется "Сформирован проект запроса в …"(kind = request).

Форматы: PDF(reportlab, шрифт и вспомогательные функции app.pdf) и DOCX - WordprocessingML собирается вручную
(zipfile + XML) без python-docx: файл открывают Word, LibreOffice и "Страницы".
"""
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Literal, Optional
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field, model_validator
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import law, models, registry, schemas, screening
from .db import get_db
from .risk import factor_r1
from .pdf import MARGIN, PLATFORMS, P, S, _box, _canvas, _glyphs, _style, clean, esc, when
from .world import load_complex

router = APIRouter(prefix="/api/complexes", tags=["complexes"])

Obj = Dict[str, Any]
Addressee = Literal["mio", "operator", "gask"]
Fmt = Literal["pdf", "docx"]

ADDRESSEES: Dict[str, Obj] = {
    "mio": {"short": "ЖАО (МИО) ВКО", "to": "в ЖАО (МИО) ВКО",
            "lines": ["В местный исполнительный орган", "Восточно-Казахстанской области (ЖАО)",
                      "уполномоченное подразделение по вопросам долевого", "участия в жилищном строительстве"],
            "basis": "о выдаче (отказе в выдаче) уполномоченной компании разрешения на привлечение денег дольщиков по "
                     "объекту (ст. 18 п. 1, 8 Закона): номер, дата, срок действия, наименование уполномоченной компании; "
                     "о поступавших заявлениях на получение разрешения", "doc": "mio"},
    "operator": {"short": "Единый оператор жилищного строительства (АО «Казахстанская жилищная компания»)",
                 "to": "Единому оператору жилищного строительства (АО «Казахстанская жилищная компания»)",
                 "lines": ["В АО «Казахстанская жилищная компания» —", "Единый оператор жилищного строительства"],
                 "basis": "о заключении с застройщиком (уполномоченной компанией) договора о предоставлении гарантии "
                          "Единого оператора по объекту (ст. 8 п. 5, 6 Закона): номер и дата договора, действует ли он "
                          "на дату ответа; о поступавших заявках на гарантию и итогах их рассмотрения", "doc": "guarantee"},
    "gask": {"short": "ГАСК ВКО", "to": "в ГАСК ВКО",
             "lines": ["В Управление государственного", "архитектурно-строительного контроля",
                       "Восточно-Казахстанской области (ГАСК ВКО)"],
             "basis": "об имеющихся сведениях об основании привлечения денег дольщиков по объекту (гарантия Единого "
                      "оператора, разрешение МИО, проект с банком второго уровня — ст. 7 п. 1 Закона) и о разрешительной "
                      "документации на строительство (уведомление о начале строительно-монтажных работ)", "doc": "bvu"},
}
TOPIC_ASKS = {
    "readiness": "о фактической стадии строительства и подтверждённой готовности объекта на дату ответа (по данным "
                 "технического надзора, инжиниринговой компании или проверки)",
    "appeals": "об обращениях граждан (дольщиков, покупателей) по объекту за последние 12 месяцев и результатах их "
               "рассмотрения",
    "list": "о включении объекта в перечень строящихся объектов, опубликованный ГАСК ВКО, и дате включения (исключения)",
}
SENDER = "Департамент экономических расследований по Восточно-Казахстанской области (ДЭР ВКО)"
DRAFT_NOTE = "Черновик для внутреннего маршрута — автоматической отправки нет"
DOC_STATE = {"yes": "есть", "no": "нет", "unknown": "нет данных"}
AD_LIMIT = 10  # публикаций в письме; остальные - в приложении "по запросу"
QUOTE_MAX = 260
EXCERPT_MAX = 700


class LetterIn(schemas.Camel):
    addressee: Addressee
    author: schemas.HumanName
    topics: List[schemas.InfoTopic] = Field(min_length=1, max_length=5)
    comment: schemas.OptNote = None

    @model_validator(mode="after")
    def _other_needs_comment(self):
        if "other" in self.topics and not self.comment:
            raise ValueError("для темы «другое» нужен комментарий: какие сведения запросить")
        return self


# ---------- содержание ----------

def _cut(t: Any, limit: int) -> str:
    s = " ".join(str(t or "").split())
    return s if len(s) <= limit else (s[:limit - 1].rsplit(" ", 1)[0] or s[:limit - 1]) + "…"


def _signer(db: Session, author: str) -> str:
    """Должность для подписи по учётной записи автора("Ведущий аналитик · ДЭР ВКО" → "Ведущий аналитик ДЭР ВКО");
    имени в письме нет. Неизвестный автор - место под должность."""
    a = db.scalar(select(models.Analyst).where(models.Analyst.name == author).limit(1))
    return a.role.replace(" · ", " ") if a else "Должность: ____________________"


def _doc_line(d: Obj) -> str:
    state = DOC_STATE.get(str(d.get("value")), str(d.get("value")))
    checked = d.get("source") or d.get("date")
    tail = [f"реквизит: {d['quote']}" if d.get("quote") else "",
            f"источник: {d['source']}" if d.get("source") else "",
            f"сверено {when(d['date'])}" if d.get("date") else "",
            "" if checked else "не проверялся: источника и даты сверки нет", d.get("url") or ""]
    return f"{d.get('label')}: {state}" + (" — " + "; ".join(t for t in tail if t) if any(tail) else "")


def _ad_quote(ad: Obj, risk: Obj) -> str:
    for code in ("R2", "R3"):
        f = next((f for f in risk["factors"] if f["code"] == code), None)
        for it in (f or {}).get("items") or []:
            if it.get("kind") == "ad" and it.get("ref") == ad["id"] and it.get("quote"):
                return _cut(it["quote"], QUOTE_MAX)
    return _cut(ad.get("text") or ad.get("ocr"), QUOTE_MAX)


def _ad_line(ad: Obj, risk: Obj) -> str:
    link = "привязка к ЖК подтверждена аналитиком" if ad.get("linkConfirmedBy") else \
        f"привязка к ЖК — гипотеза ИИ ({round((ad.get('matchConfidence') or 0) * 100)}%), аналитиком не подтверждена"
    head = ", ".join(x for x in (PLATFORMS.get(ad.get("platform"), ad.get("platform")), ad.get("account"),
                                 f"опубликовано {when(ad.get('date'))}" if ad.get("date") else "",
                                 f"зафиксировано системой {when(ad.get('collectedAt'))}" if ad.get("collectedAt") else "")
                     if x)
    q = _ad_quote(ad, risk)
    return f"{head}: «{q}»" + (f" — {ad['url']}" if ad.get("url") else "") + f" ({link})"


def _law_refs(addressee: str) -> List[Obj]:
    """Нормы письма: ссылки факторов R1-R3 и документа основания адресата, по статьям 7, 8, 18, 23 по порядку."""
    parts: Dict[str, set] = {}
    for code in ("R1", "R2", "R3", ADDRESSEES[addressee]["doc"]):
        for r in law.refs(code):
            parts.setdefault(r["article"], set()).update(r.get("parts") or [])
    order = [a["num"] for a in law.LAW["articles"]]
    return [{"article": a, "parts": sorted(parts[a], key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"[-.\s]", p) if x])}
            for a in order if a in parts]


def _law_lines(refs: List[Obj]) -> List[str]:
    """Дословные выдержки по пунктам; название статьи - у первого пункта статьи."""
    out = []
    for r in refs:
        a = law.ARTICLES[r["article"]]
        exs = [e for e in a["excerpts"] if not r["parts"] or e.get("part") in r["parts"]] or a["excerpts"]
        for i, e in enumerate(exs):
            part = e.get("part") or ""
            where = f"ст. {a['num']}" + (f" {part}" if part.startswith("ч.") else f" п. {part}" if part else "")
            title = f" («{a['title']}»)" if i == 0 else ""
            out.append(f"{where}{title}: «{_cut(e['text'], EXCERPT_MAX)}»")
    return out


def law_intro() -> str:
    act = law.ACT
    title = act["title"].replace("Закон Республики Казахстан ", "")
    m = re.search(r"\d\d\.\d\d\.\d{4}", act.get("redaction") or "")
    red = f"; редакция ИПС «Әділет» на {m.group(0)}" if m else ""
    return (f"Сведения запрашиваются для проверки соблюдения требований Закона Республики Казахстан от 7 апреля 2016 года "
            f"№ {act['number']} {title} ({act['url']}{red}):")


def build(db: Session, cid: str, body: LetterIn, now: Optional[datetime] = None) -> Obj:
    """Письмо как структура: blocks [{type, text|lines}] - для PDF, DOCX и предпросмотра; text - плоский текст."""
    c_row = db.get(models.Complex, cid)
    if c_row is None:
        raise registry.not_found(db, cid)
    now = now or datetime.now(timezone.utc)
    w = load_complex(db, cid)
    c = w.complexes[0]
    dev = w.developers.get(c["developerId"])
    risk = w.risk(c)
    docs = next((f["items"] for f in risk["factors"] if f["code"] == "R1"), []) or []
    if not docs:  # введённый объект: R1 не применяется - документы-основания тем же движком
        docs = factor_r1({**c, "stage": None})["items"]
    sc = screening.compute(db)
    permit, scr = sc.permits.get(cid), sc.complexes.get(cid)
    ads = sorted([a for a in w.ads if a["status"] != "dismissed"], key=lambda a: a.get("date") or "", reverse=True)
    sig = db.scalar(select(models.Signal).where(models.Signal.complex_id == cid).order_by(models.Signal.id.desc()).limit(1))
    a = ADDRESSEES[body.addressee]
    date_s = when(now)[:10]
    name = f"ЖК «{c['name']}»"
    addr = c["address"] or "адрес не указан"
    subject = f"О предоставлении сведений по {name} ({addr if 'Усть-Каменогорск' in addr else addr + ', г. Усть-Каменогорск'})"

    asks = []
    for t in schemas_topics(body.topics):
        if t == "basis":
            asks.append(a["basis"])
        elif t == "other":
            asks.append(body.comment or "")
        else:
            asks.append(TOPIC_ASKS[t])

    origin = {"registry": "карточка по открытому реестру, аналитиком не проверена",
              "advertised": "объект известен по рекламе, в открытом реестре не найден",
              "demo": "вымышленный ЖК (демо)"}.get(c.get("origin"), "")
    rd = c.get("readiness") or {}
    conf, decl = rd.get("confirmed"), rd.get("declared")
    stage = c.get("stage") or "стадия не указана"
    ready = (f"подтверждённая готовность {conf['value']}% ({conf.get('source') or 'без источника'}, {when(conf.get('date'))})"
             if conf else "подтверждённой готовности нет")
    ready += f"; заявлено {decl['value']}% ({decl.get('source') or 'без источника'})" if decl else ""
    facts = [
        f"Объект: {name}" + (f" — {origin}" if origin else ""),
        f"Адрес: {c['address'] or 'не указан'}" + (f", район {c['district']}" if c.get("district") else ""),
        "Застройщик: " + (f"{dev['name']}, БИН {dev['bin'] or 'не установлен'}" if dev else "не установлен"),
        f"Стадия: {stage}; {ready}" + (f"; заявленный срок сдачи {when(c['plannedEnd'])}" if c.get("plannedEnd") else ""),
    ]
    if scr:
        facts.append(f"Проверка по рекламе (предварительная автоматическая оценка, не вывод о нарушении): {scr['label']}")
    if risk.get("assessed", True) and risk.get("score") is not None:
        hits = [f["code"] for f in risk["factors"] if f["state"] == "hit"]
        facts.append(f"Предварительная оценка риска системой: {risk['score']} из 100" +
                     (f", подтверждённые факторы {', '.join(hits)}" if hits else ""))
    basis = [_doc_line(d) for d in docs]
    if permit and permit.get("text"):
        basis.append(f"Открытые данные: {permit['text'][:1].lower()}{permit['text'][1:]}" + (f" — {permit['url']}" if permit.get("url") else ""))
    if c.get("basis", {}).get("mioRef"):
        basis.append(c["basis"]["mioRef"])
    pubs = [_ad_line(x, risk) for x in ads[:AD_LIMIT]]
    refs = _law_refs(body.addressee)
    attachments = []
    if pubs:
        attachments.append(f"Перечень публикаций с цитатами и ссылками — в тексте запроса ({len(ads)} шт.); копии "
                           "(скриншоты) публикаций — на ___ л.")
    if sig:
        attachments.append(f"Сигнальная карточка {sig.code} от {when(sig.created_at)[:10]} (выгрузка PDF из SAQ Qurylys) — "
                           "на ___ л.")
    attachments.append("Выписка из журнала объекта в SAQ Qurylys (по требованию) — на ___ л.")

    blocks: List[Obj] = [
        {"type": "note", "text": f"ПРОЕКТ. {DRAFT_NOTE}. Сформирован в SAQ Qurylys {when(now)} (Алматы); подлежит проверке "
                                 "и подписанию в установленном порядке."},
        {"type": "addr", "lines": a["lines"]},
        {"type": "meta", "text": f"Исх. № ____________ от {date_s}"},
        {"type": "meta", "text": f"От: {SENDER}"},
        {"type": "subject", "text": subject},
        {"type": "p", "text": "В рамках мониторинга рекламы долевого жилищного строительства в г. Усть-Каменогорске "
                              f"(система SAQ Qurylys) по {name} выявлены публикации о продаже квартир в строящемся доме. "
                              "Для проверки наличия основания привлечения денег дольщиков просим предоставить сведения:"},
        *[{"type": "li", "n": i + 1, "text": t + (";" if i < len(asks) - 1 else ".")} for i, t in enumerate(asks)],
        {"type": "h", "text": "Сведения об объекте"},
        *[{"type": "bullet", "text": t} for t in facts],
        {"type": "h", "text": "Основание привлечения денег дольщиков — состояние сверки"},
        *[{"type": "bullet", "text": t} for t in basis],
        {"type": "h", "text": f"Публикации ({len(ads)})"},
        *([{"type": "bullet", "text": t} for t in pubs] or [{"type": "p", "text": "Привязанных к ЖК публикаций нет."}]),
        *([{"type": "p", "text": f"Ещё {len(ads) - AD_LIMIT} публикаций — в приложении."}] if len(ads) > AD_LIMIT else []),
        {"type": "h", "text": "Правовые основания"},
        {"type": "p", "text": law_intro()},
        *[{"type": "bullet", "text": t} for t in _law_lines(refs)],
        {"type": "p", "text": "Отсутствие разрешения МИО само по себе не означает нарушения: основанием привлечения денег "
                              "дольщиков также является договор о предоставлении гарантии Единого оператора (ст. 8 п. 6). "
                              "Оценка системы — предварительная, рекомендация для проверки, не юридическое заключение."},
        *([{"type": "p", "text": f"Дополнительно: {body.comment}"}] if body.comment and "other" not in body.topics else []),
        {"type": "p", "text": "Ответ просим направить в ДЭР ВКО в установленный законодательством срок."},
        {"type": "h", "text": "Приложения"},
        *[{"type": "li", "n": i + 1, "text": t} for i, t in enumerate(attachments)],
        {"type": "sign", "lines": [_signer(db, body.author), "____________________ (подпись)          М. П.",
                                   f"Дата: {date_s}"]},
        {"type": "small", "text": "Исполнитель: " + _signer(db, body.author).replace("Должность: ", "") + ", тел. ____________"},
    ]
    text = "\n".join(_plain(b) for b in blocks)
    return {"complexId": c["id"], "complexName": c["name"], "addressee": body.addressee, "addresseeName": a["short"], "addresseeTo": a["to"],
            "subject": subject, "date": date_s, "number": "Исх. № ____________", "topics": schemas_topics(body.topics),
            "lawRefs": [law.ref_text(r) for r in refs], "attachments": attachments, "signature": _signer(db, body.author),
            "signalCode": sig.code if sig else None, "ads": len(ads), "note": DRAFT_NOTE, "blocks": blocks, "text": text,
            "fileBase": _file_base(c["id"], body.addressee, now)}


def schemas_topics(topics: List[str]) -> List[str]:
    """Темы в порядке списка(как в журнале и окне запроса), без повторов."""
    order = ["basis", "readiness", "appeals", "list", "other"]
    return [t for t in order if t in topics]


def _plain(b: Obj) -> str:
    t = b["type"]
    if t in ("addr", "sign"):
        return "\n".join(b["lines"])
    if t == "li":
        return f"{b['n']}. {b['text']}"
    if t == "bullet":
        return f"— {b['text']}"
    if t == "h":
        return f"\n{b['text']}"
    if t == "subject":
        return f"\nТема: {b['text']}\n"
    return b["text"]


def _file_base(cid: str, addressee: str, now: datetime) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", cid).strip("-") or "complex"
    return f"request-{safe}-{addressee}-{now.astimezone(timezone.utc):%Y%m%d}"


# ---------- PDF ----------

_URL = re.compile(r"https?://[^\s«»()<>]+[^\s«»()<>.,;:]")


def _linkify(text: str) -> str:
    """Экранированный текст с кликабельными http(s)-ссылками."""
    out, pos = [], 0
    s = clean(text)
    for m in _URL.finditer(s):
        out.append(esc(s[pos:m.start()]))
        u = m.group(0)
        out.append(f'<a href="{escape(u, {chr(34): "&quot;"})}" color="#1a56db">{esc(u)}</a>')
        pos = m.end()
    out.append(esc(s[pos:]))
    return "".join(out)


LS = {
    "addr": _style("l-addr", fontSize=10, leading=13, alignment=TA_RIGHT),
    "meta": _style("l-meta", fontSize=9.5, leading=13),
    "subject": _style("l-subj", fontName="NotoSans-Bold", fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=6),
    "p": _style("l-p", fontSize=9.5, leading=13.5, spaceBefore=3, spaceAfter=3, firstLineIndent=10 * mm),
    "li": _style("l-li", fontSize=9.5, leading=13.5, leftIndent=8 * mm, bulletIndent=2 * mm, spaceAfter=1),
    "bullet": _style("l-b", fontSize=8.8, leading=12, leftIndent=6 * mm, bulletIndent=1 * mm, spaceAfter=1.5),
    "sign": _style("l-sign", fontSize=10, leading=15),
    "title": _style("l-title", fontName="NotoSans-Bold", fontSize=12, leading=16, alignment=TA_CENTER),
}


def story(letter: Obj) -> list:
    _glyphs()
    out: list = []
    for b in letter["blocks"]:
        t = b["type"]
        if t == "note":
            out += [_box(esc(b["text"]), colors.HexColor("#8a6100"), colors.HexColor("#fff4d6")), Spacer(1, 8)]
        elif t == "addr":
            out += [Paragraph("<br/>".join(esc(x) for x in b["lines"]), LS["addr"]), Spacer(1, 10)]
        elif t == "meta":
            out.append(Paragraph(esc(b["text"]), LS["meta"]))
        elif t == "subject":
            out.append(Paragraph(f"Тема: {esc(b['text'])}", LS["subject"]))
        elif t == "h":
            out.append(P(esc(b["text"]), "h2"))
        elif t == "li":
            out.append(Paragraph(_linkify(b["text"]), LS["li"], bulletText=f"{b['n']}."))
        elif t == "bullet":
            out.append(Paragraph(_linkify(b["text"]), LS["bullet"], bulletText="—"))
        elif t == "sign":
            out += [Spacer(1, 16), Paragraph("<br/>".join(esc(x) for x in b["lines"]), LS["sign"])]
        elif t == "small":
            out += [Spacer(1, 10), P(esc(b["text"]), "small")]
        else:
            out.append(Paragraph(_linkify(b["text"]), LS["p"]))
    return out


def render_pdf(letter: Obj) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN + 6 * mm, rightMargin=MARGIN, topMargin=MARGIN,
                            bottomMargin=MARGIN + 2 * mm, title=clean(f"Проект запроса: {letter['subject']}"),
                            author="SAQ Qurylys", subject=clean(DRAFT_NOTE), creator="SAQ Qurylys")
    footer = clean(f"SAQ Qurylys · проект запроса {letter['addresseeTo']} · {DRAFT_NOTE.lower()}")
    if len(footer) > 120:
        footer = clean(f"SAQ Qurylys · проект запроса · {DRAFT_NOTE.lower()}")
    doc.build(story(letter), canvasmaker=_canvas(footer))
    return buf.getvalue()


# ---------- DOCX(WordprocessingML без зависимостей) ----------

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/word/document.xml" '
       'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
       '<Override PartName="/word/styles.xml" '
       'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
       '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
       '</Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
         'Target="word/document.xml"/>'
         '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
         'Target="docProps/core.xml"/>'
         '</Relationships>')
_DOC_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
             'Target="styles.xml"/></Relationships>')
_STYLES = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W_NS}">'
           '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" '
           'w:cs="Times New Roman" w:eastAsia="Times New Roman"/><w:sz w:val="24"/><w:szCs w:val="24"/><w:lang w:val="ru-RU"/>'
           '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="264" w:lineRule="auto"/></w:pPr>'
           '</w:pPrDefault></w:docDefaults>'
           '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>')


def _xml(s: Any) -> str:
    # управляющие символы(кроме \t \n) в XML 1.0 недопустимы - Word не откроет файл
    return escape("".join(ch for ch in str(s) if ch in "\t\n" or ord(ch) >= 0x20))


def _run(text: str, bold: bool = False, size: Optional[int] = None, color: Optional[str] = None) -> str:
    rpr = ("<w:b/>" if bold else "") + (f'<w:color w:val="{color}"/>' if color else "") + \
          (f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>' if size else "")
    return f'<w:r>{f"<w:rPr>{rpr}</w:rPr>" if rpr else ""}<w:t xml:space="preserve">{_xml(text)}</w:t></w:r>'


def _par(runs: str, align: Optional[str] = None, indent: Optional[int] = None, hanging: Optional[int] = None,
         first: Optional[int] = None, before: int = 0, after: int = 80, shade: Optional[str] = None) -> str:
    ppr = f'<w:spacing w:before="{before}" w:after="{after}"/>'
    if shade:
        ppr += (f'<w:pBdr><w:top w:val="single" w:sz="4" w:space="2" w:color="8A6100"/><w:left w:val="single" w:sz="4" '
                f'w:space="2" w:color="8A6100"/><w:bottom w:val="single" w:sz="4" w:space="2" w:color="8A6100"/>'
                f'<w:right w:val="single" w:sz="4" w:space="2" w:color="8A6100"/></w:pBdr>'
                f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
    if indent is not None or first is not None:
        ppr += (f'<w:ind w:left="{indent or 0}"' + (f' w:hanging="{hanging}"' if hanging else "")
                + (f' w:firstLine="{first}"' if first else "") + "/>")
    if align:
        ppr += f'<w:jc w:val="{align}"/>'
    return f"<w:p><w:pPr>{ppr}</w:pPr>{runs}</w:p>"


def _lines(lines: List[str], **kw: Any) -> str:
    runs = '<w:r><w:br/></w:r>'.join(_run(x) for x in lines)
    return _par(runs, **kw)


def render_docx(letter: Obj, now: Optional[datetime] = None) -> bytes:
    now = now or datetime.now(timezone.utc)
    body = []
    for b in letter["blocks"]:
        t = b["type"]
        if t == "note":
            body.append(_par(_run(b["text"], size=20, color="8A6100"), shade="FFF4D6", after=200))
        elif t == "addr":
            body.append(_lines(b["lines"], align="right", after=240))
        elif t == "meta":
            body.append(_par(_run(b["text"]), after=40))
        elif t == "subject":
            body.append(_par(_run("Тема: ", bold=True) + _run(b["text"], bold=True), before=200, after=160))
        elif t == "h":
            body.append(_par(_run(b["text"], bold=True), before=200, after=80))
        elif t == "li":
            body.append(_par(_run(f"{b['n']}. {b['text']}"), indent=567, hanging=397, after=40))
        elif t == "bullet":
            body.append(_par(_run(f"— {b['text']}", size=22), indent=454, hanging=284, after=40))
        elif t == "sign":
            body.append(_lines(b["lines"], before=360))
        elif t == "small":
            body.append(_par(_run(b["text"], size=18, color="6B6B6B"), before=200))
        else:
            body.append(_par(_run(b["text"]), first=567, align="both"))
    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="850" w:bottom="1134" '
            'w:left="1701" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W_NS}"><w:body>'
           + "".join(body) + sect + "</w:body></w:document>")
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{_xml("Проект запроса: " + letter["subject"])}</dc:title><dc:creator>SAQ Qurylys</dc:creator>'
            f'<dc:description>{_xml(DRAFT_NOTE)}</dc:description>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created></cp:coreProperties>')
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("docProps/core.xml", core)
    return buf.getvalue()


# ---------- эндпоинты ----------

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("/{cid}/request-letter/preview")
def letter_preview(cid: str, body: LetterIn, db: Session = Depends(get_db)):
    """Текст проекта запроса для окна предпросмотра; в журнал не пишется."""
    return build(db, cid, body)


@router.post("/{cid}/request-letter", response_class=Response,
             responses={200: {"content": {"application/pdf": {}, DOCX_MIME: {}},
                              "description": "Проект запроса (черновик, без отправки)"}})
def letter_file(cid: str, body: LetterIn, fmt: Fmt = Query("pdf", alias="format"), db: Session = Depends(get_db)):
    """Проект запроса в PDF или DOCX; в журнал объекта - "Сформирован проект запроса в …"(kind = request)."""
    now = datetime.now(timezone.utc)
    letter = build(db, cid, body, now)
    data = render_pdf(letter) if fmt == "pdf" else render_docx(letter, now)
    topics = ", ".join(TOPIC_NAMES[t] for t in letter["topics"])
    what = (f"Сформирован проект запроса {letter['addresseeTo']} ({fmt.upper()}): {topics}. Черновик для внутреннего "
            "маршрута, автоматической отправки нет") + (f". Комментарий: {body.comment}" if body.comment else "")
    db.add(models.AuditEvent(complex_id=cid, kind="request", who=body.author, what=what, at=now))
    db.commit()
    name = f"{letter['fileBase']}.{fmt}"
    return Response(data, media_type="application/pdf" if fmt == "pdf" else DOCX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


TOPIC_NAMES = {"basis": "основание привлечения средств", "readiness": "подтверждённая готовность",
               "appeals": "обращения граждан", "list": "перечень ГАСК", "other": "другое"}
