"""PDF сигнальной карточки: много доказательств и длинные тексты переносятся постранично, формулировки - по данным."""
import re
from datetime import datetime, UTC

from reportlab.platypus import KeepTogether, Paragraph, Table

from app import pdf

AUTHOR = "Тест"


def _create(c, cid):
    r = c.post("/api/signals", json={"complexId": cid, "author": AUTHOR})
    assert r.status_code == 201, r.text
    return r.json()


def _pdf(c, sid):
    r = c.get(f"/api/signals/{sid}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF"), r.text[:300]
    return r.content


def _pages(data: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b(?!s)", data))


def _text(x) -> str:
    """Текст всех абзацев карточки(таблицы и KeepTogether - рекурсивно)."""
    if isinstance(x, (list, tuple)):
        return "\n".join(_text(i) for i in x)
    if isinstance(x, Paragraph):
        return x.getPlainText()
    if isinstance(x, Table):
        return _text(x._cellvalues)
    if isinstance(x, KeepTogether):
        return _text(x._content)
    return x if isinstance(x, str) else ""


def _story_text(c, sid) -> str:
    return _text(pdf.story(c.get(f"/api/signals/{sid}").json(), datetime.now(UTC)))


def test_many_linked_ads(client):
    for i in range(40):
        r = client.post("/api/ads", json={
            "platform": "instagram", "account": f"@altai.sale{i}", "complexId": "c2", "linkConfirmed": True, "author": AUTHOR,
            "url": f"https://instagram.com/p/altai{i}", "date": f"2026-10-{1 + i % 14:02d}T10:00",
            "text": f"ЖК «Алтай Резиденс», квартира №{i}: бронь от 500 000 ₸, первый взнос 30%. Старт продаж!"})
        assert r.status_code == 201
    s = _create(client, "c2")
    assert len(s["evidence"]["ads"]) == 41
    assert len(next(f for f in s["factors"] if f["code"] == "R2")["items"]) == 41
    data = _pdf(client, s["id"])
    assert _pages(data) >= 4


def test_many_appeals_and_photos(client):
    sig = client.get(f"/api/signals/{_create(client, 'c4')['id']}").json()
    r4 = next(f for f in sig["factors"] if f["code"] == "R4")
    r6 = next(f for f in sig["factors"] if f["code"] == "R6")
    photo = {"kind": "photo", "label": "Кран не работает", "source": "Облёт (демо)", "date": "2026-09-01", "value": "нет"}
    r4["items"] = [{**r4["items"][0], "ref": f"ap{i}"} for i in range(80)]
    r6["items"] = [photo] * 80
    sig["evidence"]["photos"] = [{"date": "2026-09-01", "source": "Облёт (демо)", "activity": "нет", "note": "x" * 200}] * 80
    data = pdf.render_signal(sig)
    assert data.startswith(b"%PDF") and _pages(data) >= 5


def test_long_comment_and_author(client):
    sid = _create(client, "c2")["id"]
    comment = ("Проверено по реестру и фото-мониторингу. " * 200)[:4000]
    r = client.post(f"/api/signals/{sid}/confirm", json={"author": AUTHOR, "comment": comment})
    assert r.status_code == 200 and len(r.json()["decisionComment"]) == 3999  # strip хвостового пробела
    _pdf(client, sid)
    text = _story_text(client, sid)
    assert comment.strip() in text  # полный комментарий - в "Статусе проверки"
    event = pdf.line(f"Сигнал подтверждён. {comment}", 300)  # в журнале событие обрезано
    assert event in text and event.endswith("…") and len(event) <= 300

    # комментарий из множества строк и одного "слова" без пробелов; автор с переводами строк
    sid = _create(client, "c2")["id"]
    comment = "строка\n" * 400 + "ж" * 1000
    author = "\n".join(["Тест"] * 25)
    assert client.post(f"/api/signals/{sid}/return", json={"author": author, "comment": comment[:4000]}).status_code == 200
    client.get(f"/api/signals/{sid}/pdf", params={"author": "А\nБ"})
    _pdf(client, sid)


def test_readiness_wording(client):
    # c14: ни заявленной, ни подтверждённой готовности - "подтверждённая готовность" не печатается
    client.post("/api/ads", json={"platform": "telegram", "account": "@sun", "complexId": "c14", "matchConfidence": 0.5,
                                  "text": "Солнечный квартал: бронь квартир"})
    text = _story_text(client, _create(client, "c14")["id"])
    assert "подтверждённая готовность" not in text.lower()
    assert "котлован; готовность по карточке объекта 3%, без источника" in text and "Подтверждено: нет данных" in text
    # привязка публикации: факт(a10 - подтвердил аналитик, кто и когда) и гипотеза ИИ(50%) подписаны по-разному
    assert "привязка — факт (подтвердил Исаев Т., 09.10.2026 11:40)" in text
    assert "привязка — гипотеза ИИ 50%, в балл не входит" in text
    assert "Привязка к ЖК — факт только после подтверждения аналитиком; гипотеза ИИ (с уверенностью) в балл не входит" in text

    # c1: подтверждённая готовность - из readiness.confirmed, с источником и датой
    text = _story_text(client, _create(client, "c1")["id"])
    assert "каркас, подтверждённая готовность 58% — Фото-мониторинг, облёт (демо) · 15.08.2026" in text
    assert "Заявлено: 60% — Сайт ertis-invest.kz (демо) · 01.10.2026" in text

    lines = pdf.readiness_lines({"stage": "каркас", "progress": 40, "readiness": {"declared": None, "confirmed": None}})
    assert lines == ["каркас; готовность по карточке объекта 40%, без источника", "Заявлено: нет данных",
                     "Подтверждено: нет данных"]


def test_time_in_almaty():
    assert pdf.when("2026-10-15T14:03:00+00:00") == "15.10.2026 19:03"
    assert pdf.when(datetime(2026, 10, 15, 20, 30)) == "16.10.2026 01:30"  # наивное время сервера - UTC
    assert pdf.when("2026-10-09T18:22") == "09.10.2026 18:22"  # дата публикации из источника - как есть
    assert pdf.when("2026-10-09") == "09.10.2026" and pdf.when(None) == "—"
    assert pdf.line("а  б\nв " * 100, 20) == "а б в а б в а б в…" and pdf.line("ж" * 50, 10) == "ж" * 9 + "…"


def test_demo_marks_and_document_provenance(client):
    """Демо-ЖК и демо-застройщик подписаны как вымышленные; у документов-оснований - источник, дата и ссылка по документу,
    непроверенный документ - "не проверялся"."""
    text = _story_text(client, _create(client, "c2")["id"])
    assert "ЖК «Алтай Резиденс» · вымышленный ЖК (демо)" in text
    assert "ТОО «Altai Build Group» · БИН 200540098761 · вымышленная организация (демо)" in text
    assert ("Гарантия ЕО (КЖК): нет — источник: Реестр гарантий КЖК, homeportal.kz (демо) · сверено 09.10.2026 · "
            "https://homeportal.kz") in text
    assert "Разрешение МИО: нет — источник: Реестр разрешений МИО, elicense.kz (демо) · сверено 09.10.2026 · https://elicense.kz" in text
    assert "Банк второго уровня: нет — источник: Ответ БВУ на запрос ДЭР (демо) · сверено 09.10.2026" in text
    docs = pdf._document({"label": "Гарантия ЕО (КЖК)", "value": "yes", "source": "Реестр", "date": "2026-10-09",
                          "url": 'https://example.kz/a?b=1&c="x"'})
    assert '<a href="https://example.kz/a?b=1&amp;c=&quot;x&quot;"' in docs
    assert pdf.link("javascript:alert(1)") == "javascript:alert(1)" and pdf.link(None) == ""  # не http(s) - без ссылки
    assert pdf._document({"label": "Разрешение МИО", "value": "unknown"}) == "<b>Разрешение МИО</b>: нет данных — не проверялся"
    assert pdf._document({"label": "Разрешение МИО", "value": "no"}).endswith("— источник не указан")
    # снимок сигнала без поля origin(созданный до блока E) - тоже демо
    assert pdf._demo({"name": "x"}) and not pdf._demo({"origin": "analyst"}) and not pdf._demo(None)


def test_markup_is_escaped_everywhere():
    """Текст из данных не становится разметкой ReportLab: <, > и & экранируются и в строковом значении таблицы
    "название - значение", и в ссылках; пустой БИН застройщика не печатается как "БИН "."""
    t = pdf._kv([("Ключ <b>", "значение <font size=40>&amp;")])
    assert _text(t) == "Ключ <b>\nзначение <font size=40>&amp;"
    assert pdf.link('https://x.kz/?a=1&b="2"<script>') == (
        '<a href="https://x.kz/?a=1&amp;b=&quot;2&quot;&lt;script&gt;" color="#1a56db">'
        'https://x.kz/?a=1&amp;b="2"&lt;script&gt;</a>')
    assert pdf.link_text({"matchConfidence": 0.125}) == "привязка — гипотеза ИИ 13%, в балл не входит"
    assert pdf.link_text({}) == "привязка — гипотеза ИИ 0%, в балл не входит"
    sig_dev = {"name": "ТОО «X»", "bin": "", "origin": "analyst"}
    row = pdf._object({"evidence": {"complex": {"name": "Y", "origin": "analyst"}, "developer": sig_dev}})
    assert "ТОО «X»" in _text(row) and "БИН" not in _text(row)
    # документ без значения - "нет данных"(как unknown), а не пустое место
    assert pdf._document({"label": "Разрешение МИО", "value": None}) == "<b>Разрешение МИО</b>: нет данных — не проверялся"
