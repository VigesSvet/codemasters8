"""Проверка застройщика по БИН - офлайн, только по нашим данным, плюс ссылки на официальные сервисы.

Что собирается(compute): БИН и верность его контрольной цифры(sellers.bin_valid), руководитель(поле developers.director;
пусто - "нет в данных"), дата регистрации, ЖК застройщика и публикации о них(ads.complex_id), аккаунты и телефоны из этих
публикаций, первое появление(самая ранняя публикация или запись открытого реестра); уполномоченные компании из записей
открытого реестра с этим БИН(source_records.agent / agent_bin).
Совпадения с другими застройщиками: тот же руководитель(без регистра, кавычек и "ё"), тот же телефон(из публикаций
их ЖК и developers.phones), та же уполномоченная компания(по БИН или названию), общая группа продавцов(app.sellers).
Совпадение - гипотеза для проверки, а не вывод об аффилированности.

Ссылки - на официальные сервисы поиска по БИН(адреса проверены 19.10.2026); система их не опрашивает и ничего не
скачивает: аналитик открывает их сам.
"""
import re
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .sellers import NOTE, bin_valid, contacts, fmt_phone, norm_phone

Obj = dict[str, Any]

LOOKUPS = [
    {"label": "КГД МФ РК — поиск налогоплательщика", "url": "https://portal.kgd.gov.kz/ru/pages/info-services/find-taxpayer",
     "note": "Регистрация, статус налогоплательщика, руководитель — по БИН"},
    {"label": "Бюро национальной статистики — поиск по БИН/ИИН", "url": "https://stat.gov.kz/ru/juridical/by/bin/",
     "note": "Вид деятельности (ОКЭД), адрес, размер предприятия, руководитель"},
    {"label": "Госзакупки — реестр недобросовестных участников", "url": "https://www.goszakup.gov.kz/ru/registry/rnu",
     "note": "Есть ли компания в реестре недобросовестных поставщиков"},
    {"label": "Е-лицензирование — лицензии на строительство", "url": "https://www.elicense.kz/",
     "note": "Лицензия на изыскательскую, проектную и строительно-монтажную деятельность"},
]
HYPOTHESIS = "Совпадение руководителя, телефона или уполномоченной компании — гипотеза для проверки, а не вывод об аффилированности."


def _norm(s: str | None) -> str:
    t = re.sub(r"[«»\"'“”„]", "", (s or "").lower().replace("ё", "е"))
    t = re.sub(r"\b(тоо|ао|ип|too|llp)\b|товарищество с ограниченной ответственностью", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _ad_phones(text: str | None, ocr: str | None) -> list[str]:
    return [c.value for c in contacts(text, ocr) if c.kind in ("phone", "wa")]


def compute(db: Session, dev_id: str, groups: list[Obj] | None = None) -> Obj | None:
    """Связи застройщика; None - застройщика нет. groups - группы продавцов(app.sellers.compute), чтобы назвать общие."""
    D, C, A, R = models.Developer, models.Complex, models.Ad, models.SourceRecord
    dev = db.get(D, dev_id)
    if dev is None:
        return None
    devs = list(db.scalars(select(D)))
    dev_by_id = {d.id: d for d in devs}
    cx = list(db.scalars(select(C)))
    dev_of = {c.id: c.developer_id for c in cx}
    ads = list(db.execute(select(A.id, A.platform, A.account, A.date, A.text, A.ocr, A.complex_id,
                                 A.link_confirmed_by)).all())

    # телефоны каждого застройщика: из публикаций его ЖК и из карточки(как в публикациях - 10 цифр номера РК; пустой
    # или не номер РК не учитывается, иначе два застройщика с пустым телефоном совпали бы по "")
    phones: dict[str, dict[str, list[str]]] = {d.id: {} for d in devs}
    for d in devs:
        for p in d.phones or []:
            digits = norm_phone(p) if isinstance(p, str) else None
            if digits:
                phones[d.id].setdefault(digits, [])
    for a in ads:
        did = dev_of.get(a.complex_id)
        if did in phones:  # ЖК без застройщика или со ссылкой на удалённого(SQLite не проверяет внешний ключ)
            for p in _ad_phones(a.text, a.ocr):
                phones[did].setdefault(p, []).append(a.id)

    own_cx = [c for c in cx if c.developer_id == dev.id]
    own_ads = [a for a in ads if dev_of.get(a.complex_id) == dev.id]
    recs = list(db.scalars(select(R).where(R.developer_bin == dev.bin))) if dev.bin else []
    agents: dict[str, Obj] = {}
    for r in recs:
        if r.agent:
            k = r.agent_bin or _norm(r.agent)
            x = agents.setdefault(k, {"name": r.agent, "bin": r.agent_bin, "binValid": bin_valid(r.agent_bin or ""),
                                      "records": 0, "sources": set()})
            x["records"] += 1
            x["sources"].add(r.source_name)
    dates = [a.date[:10] for a in own_ads if a.date]
    rec_dates = [r.collected_at.date().isoformat() for r in recs if r.collected_at]

    # совпадения с другими застройщиками
    others: dict[str, Obj] = {}

    def other(d) -> Obj:
        return others.setdefault(d.id, {"id": d.id, "name": d.name, "bin": d.bin or None, "reasons": []})

    director = _norm(dev.director)
    my_phones = set(phones[dev.id])
    agent_keys = set(agents) | {_norm(x["name"]) for x in agents.values()}
    other_agents: dict[str, set] = {}
    if agents:  # своих уполномоченных нет - сравнивать не с чем
        q = select(R.developer_bin, R.agent, R.agent_bin).where(R.developer_bin.is_not(None), R.agent.is_not(None),
                                                                 R.developer_bin != (dev.bin or "-"))
        for d_bin, agent, agent_bin in db.execute(q):
            if agent:
                other_agents.setdefault(d_bin, set()).update({agent_bin or _norm(agent), _norm(agent)})
    for d in devs:
        if d.id == dev.id:
            continue
        if director and _norm(d.director) == director:
            other(d)["reasons"].append({"kind": "director", "label": "тот же руководитель", "value": dev.director})
        shared = sorted(my_phones & set(phones[d.id]))
        if shared:
            other(d)["reasons"].append({"kind": "phone", "label": "тот же телефон",
                                        "value": ", ".join(fmt_phone(p) for p in shared),
                                        "adIds": sorted({i for p in shared for i in phones[d.id].get(p, []) + phones[dev.id].get(p, [])})})
        common = (agent_keys & other_agents.get(d.bin or "-", set())) - {""}
        if common:
            other(d)["reasons"].append({"kind": "agent", "label": "та же уполномоченная компания",
                                        "value": next((x["name"] for k, x in agents.items() if k in common or _norm(x["name"]) in common), "")})
    # общие группы продавцов
    seller_groups = []
    own_ids = {a.id for a in own_ads}
    for g in groups or []:
        hit = own_ids & set(g["adIds"])
        if not hit:
            continue
        g_devs = {dev_of.get(c["id"]) for c in g["complexes"]} - {None, dev.id}
        seller_groups.append({"key": g["key"], "title": g["title"], "accounts": len(g["accounts"]), "ads": g["adsCount"],
                              "otherDevelopers": sorted(g_devs)})
        for did in g_devs:
            d = dev_by_id.get(did)
            if d is not None:
                other(d)["reasons"].append({"kind": "seller", "label": "общий продавец в рекламе", "value": g["title"],
                                            "groupKey": g["key"]})

    accounts = Counter((a.platform, a.account) for a in own_ads)
    return {
        "developer": {"id": dev.id, "name": dev.name, "bin": dev.bin or None, "binValid": bin_valid(dev.bin or ""),
                      "director": dev.director or None, "registered": dev.registered or None, "origin": dev.origin},
        "complexes": [{"id": c.id, "name": c.name, "ads": sum(a.complex_id == c.id for a in own_ads)} for c in own_cx],
        "adsCount": len(own_ads), "confirmedAds": sum(bool(a.link_confirmed_by) for a in own_ads),
        "accounts": [{"account": acc, "platform": pl, "ads": n} for (pl, acc), n in accounts.most_common()],
        "phones": [{"phone": fmt_phone(p), "digits": p, "ads": len(set(ids))} for p, ids in sorted(phones[dev.id].items())],
        "firstSeen": min(dates + rec_dates, default=None), "firstAd": min(dates, default=None),
        "agents": [{**x, "sources": sorted(x["sources"])} for x in agents.values()],
        "registryRecords": len(recs),
        "related": sorted(others.values(), key=lambda x: (-len(x["reasons"]), x["name"])),
        "sellerGroups": seller_groups,
        "lookups": LOOKUPS, "note": HYPOTHESIS, "sellersNote": NOTE,
    }
