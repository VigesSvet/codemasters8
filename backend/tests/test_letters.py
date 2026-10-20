"""Проект запроса сведений в ЖАО(МИО) / Единому оператору / ГАСК: факты ЖК, нормы закона, PDF, DOCX, журнал."""
import io
import re
import zipfile
from datetime import datetime, UTC

from reportlab.platypus import Paragraph, Table

from app import letters

LEAD = "Омарова Ж."  # an2 демо-набора - "Ведущий аналитик · ДЭР ВКО"


def _body(**kw):
    return {"addressee": "mio", "author": LEAD, "topics": ["basis", "readiness"], **kw}


def _preview(c, cid="c2", **kw):
    r = c.post(f"/api/complexes/{cid}/request-letter/preview", json=_body(**kw))
    assert r.status_code == 200, r.text
    return r.json()


def _text(x) -> str:
    if isinstance(x, (list, tuple)):
        return "\n".join(_text(i) for i in x)
    if isinstance(x, Paragraph):
        return x.getPlainText()
    if isinstance(x, Table):
        return _text(x._cellvalues)
    return x if isinstance(x, str) else ""


def _requests(c, cid="c2"):
    return [e for e in c.get(f"/api/complexes/{cid}/audit").json() if e["kind"] == "request"]


def test_preview_has_complex_facts_ads_and_law(client):
    cx = client.get("/api/complexes/c2").json()
    dev = next(d for d in client.get("/api/developers").json() if d["id"] == cx["developerId"])
    ad = next(a for a in client.get("/api/ads").json() if a["id"] == "a1")
    L = _preview(client)
    t = L["text"]
    assert L["addressee"] == "mio" and L["addresseeName"] == "ЖАО (МИО) ВКО" and L["complexName"] == "Алтай Резиденс"
    assert L["subject"].startswith("О предоставлении сведений по ЖК «Алтай Резиденс»")
    assert f"Застройщик: {dev['name']}, БИН {dev['bin']}" in t and cx["address"] in t
    # документы основания с источником и датой сверки, публикация - цитата, ссылка, время фиксации
    assert "Гарантия ЕО" in t and "Разрешение МИО" in t and "сверено" in t
    assert ad["url"] in t and "опубликовано" in t and "«" in t
    # нормы: ст. 7, 8, 18, 23 - дословно, с адресом закона
    assert L["lawRefs"] == ["ст. 7 п. 1, 2", "ст. 8 п. 6", "ст. 18 п. 1, 8", "ст. 23"]
    for ref in ("ст. 7 п. 1", "ст. 7 п. 2", "ст. 8 п. 6", "ст. 18 п. 1", "ст. 23 ч. 1", "adilet.zan.kz", "№ 486-V"):
        assert ref in t, ref
    assert "Запрещается привлечение денег" in t  # дословная выдержка ст. 7 п. 2
    # запрос: разрешение МИО по адресату, исходящий номер, подпись - должность без имени, пометка "черновик"
    assert "разрешения на привлечение денег дольщиков" in t and "Исх. № ____" in t
    assert L["signature"] == "Ведущий аналитик ДЭР ВКО" and "Омарова" not in t
    assert letters.DRAFT_NOTE in t and L["note"] == letters.DRAFT_NOTE
    assert "Приложения" in t and L["attachments"]
    assert _requests(client) == []  # предпросмотр в журнал не пишется


def test_addressee_changes_request_and_refs(client):
    op = _preview(client, addressee="operator", topics=["basis"])
    assert "АО «Казахстанская жилищная компания»" in op["text"] and "гарантии Единого оператора" in op["text"]
    assert "ст. 8 п. 5, 6" in op["lawRefs"]
    gask = _preview(client, addressee="gask", topics=["list", "appeals"], comment="Уточнить очередь 2")
    t = gask["text"]
    assert "архитектурно-строительного контроля" in t and "перечень строящихся объектов" in t and "обращениях граждан" in t
    assert "Дополнительно: Уточнить очередь 2" in t
    other = _preview(client, topics=["other"], comment="Копии протоколов проверок")
    assert "1. Копии протоколов проверок." in other["text"]


def test_signal_in_attachments_and_unknown_author(client):
    sid = client.post("/api/signals", json={"complexId": "c2", "author": LEAD}).json()["code"]
    L = _preview(client, author="Кто-то Неизвестный")
    assert L["signalCode"] == sid and any(sid in a for a in L["attachments"])
    assert L["signature"].startswith("Должность: ___") and "Кто-то" not in L["text"]


def test_pdf_file_and_journal(client):
    r = client.post("/api/complexes/c2/request-letter?format=pdf", json=_body(comment="Срочно"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(re.findall(rb"/Type /Page\b(?!s)", r.content)) >= 1
    assert re.match(r'attachment; filename="request-c2-mio-\d{8}\.pdf"', r.headers["content-disposition"])
    ev = _requests(client)
    assert len(ev) == 1 and ev[0]["who"] == LEAD and ev[0]["complexId"] == "c2"
    assert ev[0]["what"].startswith("Сформирован проект запроса в ЖАО (МИО) ВКО (PDF): основание привлечения средств, "
                                    "подтверждённая готовность. Черновик для внутреннего маршрута, автоматической отправки нет")
    assert ev[0]["what"].endswith("Комментарий: Срочно")
    # текст PDF - те же блоки письма
    L = _preview(client)
    t = _text(letters.story(L))
    for s in ("ЖК «Алтай Резиденс»", "ст. 8 п. 6", "ст. 23 ч. 1", "Ведущий аналитик ДЭР ВКО", "Исх. №", letters.DRAFT_NOTE):
        assert s in t, s


def test_docx_file_is_valid_wordprocessingml(client):
    r = client.post("/api/complexes/c2/request-letter?format=docx", json=_body(addressee="operator"))
    assert r.status_code == 200 and r.headers["content-type"] == letters.DOCX_MIME
    assert r.headers["content-disposition"].endswith('.docx"')
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml"} <= set(z.namelist())
    doc = z.read("word/document.xml").decode()
    plain = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", doc))
    for s in ("ЖК «Алтай Резиденс»", "Казахстанская жилищная компания", "ст. 8 п. 6", "ст. 18 п. 1", "ст. 23 ч. 1",
              "Ведущий аналитик ДЭР ВКО", "Черновик для внутреннего маршрута"):
        assert s in plain.replace("&quot;", '"'), s
    assert _requests(client)[0]["what"].startswith("Сформирован проект запроса Единому оператору жилищного строительства (АО «Казахстанская жилищная компания») (DOCX)")


def test_validation(client):
    url = "/api/complexes/c2/request-letter"
    assert client.post(url, json=_body(addressee="akimat")).status_code == 422
    assert client.post(url, json=_body(topics=[])).status_code == 422
    assert client.post(url, json=_body(topics=["other"])).status_code == 422  # "другое" - только с комментарием
    assert client.post(url, json=_body(author="Система")).status_code == 422
    assert client.post(url + "?format=odt", json=_body()).status_code == 422
    assert client.post("/api/complexes/nope/request-letter", json=_body()).status_code == 404
    assert _requests(client) == []


def test_helpers_edge_cases():
    # дата в имени файла - как в письме(по Алматы), id - только безопасные символы(Content-Disposition)
    assert letters._file_base("c2", "mio", datetime(2026, 10, 20, 20, 0, tzinfo=UTC)) == "request-c2-mio-20261021"
    assert letters._file_base('../x"\r\n;', "gask", datetime(2026, 10, 20, 1, 0, tzinfo=UTC)) == "request-x-gask-20261020"
    # пункты закона: числа по значению, смесь чисел и "ч. N" не роняет сортировку
    assert sorted(["ч. 1", "10", "2", "1-1", "1"], key=letters._part_key) == ["1", "1-1", "2", "10", "ч. 1"]
    assert sorted(["2", "1²"], key=letters._part_key) == ["2", "1²"]  # "²" - isdigit, но не число для int()
    # DOCX: символы вне XML 1.0 вырезаются, разметка экранируется
    assert letters._xml("a\x01\ufffe\uffff\ud800b <w:t> & 😀\t\n") == "ab &lt;w:t&gt; &amp; 😀\t\n"


def test_ad_link_wording_matches_risk_engine():
    """Публикация со статусом "Нарушение подтверждено" - привязка факт(как в движке риска и PDF карточки), а не
    "гипотеза ИИ, аналитиком не подтверждена"; проценты гипотезы - половина вверх, как в PDF и во фронтенде."""
    ad = {"id": "x", "platform": "olx", "account": "@x", "text": "бронь", "matchConfidence": 0.125}
    assert letters._ad_line({**ad, "status": "confirmed"}, {"factors": []}).endswith("(привязка к ЖК подтверждена аналитиком)")
    assert letters._ad_line({**ad, "status": "new"}, {"factors": []}).endswith(
        "(привязка к ЖК — гипотеза ИИ (13%), аналитиком не подтверждена)")
    assert letters._doc_line({"label": "Разрешение МИО", "value": None}) == (
        "Разрешение МИО: нет данных — не проверялся: источника и даты сверки нет")


def test_law_intro_text():
    """Дата закона - из law.json(law.act_date_text), текст вступления - прежний."""
    assert letters.law_intro().startswith(
        "Сведения запрашиваются для проверки соблюдения требований Закона Республики Казахстан от 7 апреля 2016 года "
        "№ 486-V «О долевом участии в жилищном строительстве» (https://adilet.zan.kz/rus/docs/Z1600000486")


def test_letter_screens_only_its_complex(client, monkeypatch):
    """Письмо и предпросмотр берут "Проверку по рекламе" одного ЖК(screening.compute_complex), а не оценку всех ЖК;
    подпись та же, что в карточке ЖК."""
    from app import screening

    label = client.get("/api/complexes/c2").json()["screening"]["label"]

    def whole_db(*a, **kw):
        raise AssertionError("оценка всех ЖК ради одного письма")
    monkeypatch.setattr(screening, "compute", whole_db)
    monkeypatch.setattr(screening, "load_facts", whole_db)
    t = _preview(client)["text"]
    assert f"Проверка по рекламе (предварительная автоматическая оценка, не вывод о нарушении): {label}" in t
    r = client.post("/api/complexes/c2/request-letter", params={"format": "docx"}, json=_body())
    assert r.status_code == 200 and len(_requests(client)) == 1
