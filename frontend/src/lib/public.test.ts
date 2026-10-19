// Помощники новых экранов: публичная проверка ЖК (/check), выезд (/field), горячие точки рекламы, словарь экранов RU/KZ
import { describe, expect, it } from 'vitest'
import type { Ad, Complex, Developer, RegistryObject, SourceRecord } from '../data/types'
import registryJson from '../../../shared/registry_ust_kamenogorsk.json'
import { adiletUrl, anyBasis, checkEntries, checkFacts, searchEntries } from './check'
import { FAR_M, geotagNote, isFar, localStamp, nearest, shotDistance } from './field'
import { haversineM } from './geo'
import { heatPoints, hotAreas, hotPoints, isPaymentOffer, streetOf } from './hotspots'
import { i18nFor } from './i18n'
import { kkPub, ruPub } from './i18n/public'

const reg = registryJson as unknown as { records: SourceRecord[]; objects: RegistryObject[]; withoutCoords: RegistryObject[] }
const objects = [...reg.objects, ...reg.withoutCoords]

const cx = (over: Partial<Complex>): Complex => ({
  id: 'c', name: 'ЖК', developerId: 'd', address: '', district: '', lng: 82.62, lat: 49.92, stage: '', progress: 0, floors: 0, flats: 0, start: '', plannedEnd: '',
  basis: { guarantee: 'unknown', mio: 'unknown', bvu: 'unknown' }, readiness: { declared: null, confirmed: null }, inPublishedList: 'unknown', cover: '', photos: [], idleDays: 0,
  ...over,
} as Complex)
const scr = (status: 'ok' | 'check' | 'suspect' | 'unknown', concerning: number) =>
  ({ status, label: '', reasons: [], counts: { ads: concerning, concerning, windowDays: 120 }, basis: 'confirmed', inRegistry: false, disclaimer: '' }) as unknown as Complex['screening']

describe('публичная проверка ЖК: выдача и поиск', () => {
  const advertised = cx({ id: 'a-iston', name: 'Easton', address: 'проспект Илияса Есенберлина, 38/3', developerId: 'ad-1', origin: 'advertised' })
  const registryCard = cx({ id: 'r-zhanym', name: 'Жаным', origin: 'registry', permit: { status: 'permitted', text: '', registryKey: 'zhanym', verified: false } })
  const demo = cx({ id: 'c14', name: 'Демо', origin: 'demo' })
  const devs = [{ id: 'ad-1', name: 'ТОО «BS Construction»', bin: '' } as Developer]
  const entries = checkEntries(objects, [advertised, registryCard, demo], devs)

  it('объекты реестра и ЖК без записи в реестре; карточка реестра не дублируется, демо не показывается', () => {
    expect(entries.filter((e) => e.registry)).toHaveLength(objects.length)
    expect(entries.filter((e) => !e.registry).map((e) => e.complexId)).toEqual(['a-iston'])
    expect(entries.find((e) => e.key === 'cx:a-iston')?.developer).toBe('ТОО «BS Construction»')
  })

  it('ищет по названию, адресу, застройщику и БИН; транслит и казахские буквы — как в шапке', () => {
    expect(searchEntries('Жаным', entries).map((e) => e.key)).toContain('zhanym')
    expect(searchEntries('zhanym', entries).map((e) => e.key)).toContain('zhanym')
    const byBin = searchEntries('220740026935', entries)
    expect(byBin.map((e) => e.key)).toContain('park-line')
    expect(byBin.every((e) => e.bin === '220740026935')).toBe(true)
    expect(searchEntries('Mercury', entries).map((e) => e.key)).toContain('park-line')
    expect(searchEntries('Easton', entries).map((e) => e.key)).toEqual(['cx:a-iston'])
    expect(searchEntries('', entries)).toEqual([])
    expect(searchEntries('несуществующий объект ххх', entries)).toEqual([])
  })

  it('объекты реестра в выдаче — раньше ЖК без записи', () => {
    const r = searchEntries('Есенберлина', entries, 50)
    expect(r.length).toBeGreaterThan(3)
    expect(r.at(-1)?.registry).toBe(false)
    expect(r[0].registry).toBe(true)
  })
})

describe('публичная проверка ЖК: факты реестра', () => {
  const of = (key: string) => checkFacts(reg.records.filter((r) => r.key === key))

  it('гарантия ЕО по Homeportal: номер, дата, ссылка, дата записи; свежий документ — первым', () => {
    const f = of('zhanym')
    expect(f.docs.guarantee.length).toBe(3)
    expect(f.docs.guarantee[0].date).toBe('2024-08-12')
    expect(f.docs.guarantee[0].url).toMatch(/^https:\/\/homeportal\.kz\//)
    expect(f.docs.mio).toEqual([])
    expect(f.kazreestr.length).toBeGreaterThan(0)
    expect(f.providerLoadedAt).toBe('2026-06-15T17:35:09')
    expect(f.asOf?.slice(0, 10)).toBe('2026-10-15')
    expect(anyBasis(f)).toBe(true)
  })

  it('разрешение МИО и участие банка — только по полю источника, не по виду номера', () => {
    expect(of('park-line').docs.mio[0]).toMatchObject({ number: 'KZ25PDD00004205', date: '2026-10-07', source: 'homeportal' })
    expect(of('asyl-tau').docs.bvu[0].number).toBe('KZ62VWE00002712')
    expect(of('asyl-tau').docs.mio).toEqual([])
  })

  it('отдельный раздел Homeportal — только факт источника, без документов основания', () => {
    const f = of('ab-residence-2')
    expect(f.section?.section).toBe('Без разрешения на дольщиков')
    expect(f.docs.guarantee.length + f.docs.mio.length + f.docs.bvu.length + f.kazreestr.length).toBe(0)
    expect(anyBasis(f)).toBe(false)
  })

  it('нет записей — нет даты; ссылка «Әділет» — на языке интерфейса', () => {
    expect(checkFacts([]).asOf).toBeNull()
    expect(adiletUrl('https://adilet.zan.kz/rus/docs/Z1600000486#z8', 'kk')).toBe('https://adilet.zan.kz/kaz/docs/Z1600000486#z8')
    expect(adiletUrl('https://adilet.zan.kz/rus/docs/Z1600000486#z8', 'ru')).toBe('https://adilet.zan.kz/rus/docs/Z1600000486#z8')
  })
})

describe('выезд: ближайшие ЖК и геометка снимка', () => {
  const a = cx({ id: 'a', name: 'Альфа', lat: 49.8904, lng: 82.6181 })
  const b = cx({ id: 'b', name: 'Бета', lat: 49.9446, lng: 82.6227 })
  const none = cx({ id: 'n', name: 'Без точки', lat: null, lng: null })

  it('по удалённости, ближайший первым; без координат — не в списке; без точки — по названию', () => {
    const here = { lat: 49.9439, lng: 82.6227 }
    expect(nearest([a, none, b], here).map((x) => x.c.id)).toEqual(['b', 'a'])
    expect(nearest([a, none, b], here)[0].m).toBeLessThan(100)
    expect(nearest([b, a], null).map((x) => [x.c.id, x.m])).toEqual([['a', null], ['b', null]])
  })

  it('расстояние снимка до точки ЖК и порог 300 м', () => {
    const near = { lat: 49.8904 + 0.0018, lng: 82.6181 } // ~200 м к северу
    const far = { lat: 49.8904 + 0.0036, lng: 82.6181 } // ~400 м
    expect(Math.round(shotDistance(a, near)!)).toBe(Math.round(haversineM(a as never, near)))
    expect(isFar(shotDistance(a, near))).toBe(false)
    expect(isFar(shotDistance(a, far))).toBe(true)
    expect(shotDistance(none, near)).toBeNull()
    expect(shotDistance(a, null)).toBeNull()
    expect(isFar(null)).toBe(false)
    expect(FAR_M).toBe(300)
  })

  it('примечание: геометка, точность, расстояние, время UTC+5, комментарий; без геометки — так и написано; не длиннее 2000', () => {
    const n = geotagNote({ at: { lat: 49.890363, lng: 82.618099, acc: 12.4 }, takenAt: '2026-10-19T09:32:00Z', m: 412, comment: 'Кран стоит' })
    expect(n).toBe('Выезд (мобильный режим); геометка 49.890363, 82.618099 (±12 м); 412 м от точки ЖК — дальше 300 м; снято 19.10.2026 14:32 (UTC+5). Комментарий: Кран стоит')
    expect(geotagNote({ at: null, takenAt: '2026-10-19T09:32:00Z', m: null })).toBe('Выезд (мобильный режим); геометки нет (геолокация недоступна); снято 19.10.2026 14:32 (UTC+5)')
    expect(geotagNote({ at: { lat: 1, lng: 2 }, takenAt: '2026-10-19T09:32:00Z', m: 5, comment: 'х'.repeat(5000) }).length).toBeLessThanOrEqual(2000)
    expect(localStamp('2026-12-31T20:00:00Z')).toBe('01.01.2027 01:00 (UTC+5)')
  })
})

describe('горячие точки рекламы', () => {
  const noBasis = cx({ id: 'x', name: 'Икс', address: 'г. Усть-Каменогорск, проспект Илияса Есенберлина, 38/3', lat: 49.9248, lng: 82.6193, screening: scr('suspect', 3) })
  const withBasis = cx({ id: 'y', name: 'Игрек', address: 'пр. И. Есенберлина, д. 92', lat: 49.9240, lng: 82.6177, screening: scr('ok', 2),
    permit: { status: 'permitted', text: '', verified: false } })
  const quiet = cx({ id: 'q', name: 'Тихий', address: 'ул. Кайсенова, уч. 55/1', lat: 49.9455, lng: 82.6229, screening: scr('ok', 0), permit: { status: 'permitted', text: '', verified: false } })
  const check = cx({ id: 'k', name: 'Ка', address: 'улица Пермитина, 11/2', lat: 49.9423, lng: 82.6287, screening: scr('check', 0) })
  const district = cx({ id: 'd', name: 'Дэ', district: 'Левобережье', lat: null, lng: null, screening: scr('suspect', 1) })

  it('улица адреса в коротком виде', () => {
    expect(streetOf('г. Усть-Каменогорск, проспект Илияса Есенберлина, 38/3')).toBe('пр. Есенберлина')
    expect(streetOf('пр. И. Есенберлина, д. 92')).toBe('пр. Есенберлина')
    expect(streetOf('ул. Кокжал Барака, д. 19а')).toBe('ул. Кокжал Барака')
    expect(streetOf('в 19-ом жилом районе, прсч. ул. Ю. Увалиева и Кокжал Барака')).toBe('ул. Увалиева')
    expect(streetOf('г. Усть-Каменогорск, проспект Казыбек би, 1/3')).toBe('пр. Казыбек би')
    expect(streetOf('19 жилой район')).toBeNull()
  })

  it('точки: только с координатами и с публикациями или отметкой; цвет — по основанию', () => {
    expect(hotPoints([noBasis, withBasis, quiet, check, district]).map((p) => p.id)).toEqual(['x', 'y', 'k'])
    expect(heatPoints([noBasis, withBasis, check])).toEqual([
      { id: 'x', lng: 82.6193, lat: 49.9248, w: 3, tone: 'red' },
      { id: 'y', lng: 82.6177, lat: 49.9240, w: 2, tone: 'amber' },
      { id: 'k', lng: 82.6287, lat: 49.9423, w: 1, tone: 'red' },
    ])
    // без «Проверки по рекламе» — локальный счётчик
    expect(hotPoints([cx({ id: 'z', screening: null })], () => 4)[0].offers).toBe(4)
  })

  it('районы: поле district или ячейка ~1,5 км; порядок — публикации у ЖК без основания, затем отметки, затем все', () => {
    const areas = hotAreas([noBasis, withBasis, quiet, check, district])
    expect(areas.map((a) => [a.district ?? a.street, a.offers, a.offersNoBasis, a.flagged])).toEqual([
      ['пр. Есенберлина', 5, 3, 1],
      ['Левобережье', 1, 1, 1],
      ['ул. Кайсенова', 0, 0, 1],
    ])
    expect(areas[0].names).toEqual(['Игрек', 'Икс'])
    expect(areas[2].names).toEqual(['Ка', 'Тихий'])
    expect(hotAreas([quiet])).toEqual([])
    expect(areas.every((a) => a.part === null)).toBe(true)
  })

  it('одна улица в нескольких ячейках — подпись части по широте: север, центр, юг', () => {
    const at = (id: string, lat: number) => cx({ id, name: id, address: 'пр. И. Есенберлина, д. 1', lat, lng: 82.61, screening: scr('check', 1) })
    const parts = hotAreas([at('a', 49.89), at('b', 49.92), at('c', 49.95)]).map((a) => [a.names[0], a.part])
    expect(parts.sort()).toEqual([['a', 's'], ['b', 'c'], ['c', 'n']])
  })

  it('публикация с предложением оплаты: Claude, без него — словарь; отклонённые и «не предложение» не считаются', () => {
    const base = { text: '', status: 'new', termsReview: null, llm: null } as unknown as Ad
    const llm = (types: string[]) => ({ status: 'done', saleSignals: types.map((type) => ({ type, quote: 'q' })) }) as unknown as Ad['llm']
    expect(isPaymentOffer({ ...base, llm: llm(['booking']) })).toBe(true)
    expect(isPaymentOffer({ ...base, llm: llm(['price', 'discount']) })).toBe(false)
    expect(isPaymentOffer({ ...base, llm: llm(['installment']), termsReview: 'rejected' })).toBe(false)
    expect(isPaymentOffer({ ...base, llm: llm(['ddu']), status: 'dismissed' })).toBe(false)
    expect(isPaymentOffer({ ...base, text: 'Рассрочка 0%, первоначальный взнос от 10%' })).toBe(true)
    expect(isPaymentOffer({ ...base, text: 'Красивый вид на реку' })).toBe(false)
  })
})

describe('словарь новых экранов RU/KZ', () => {
  it('те же ключи, казахский текст свой; на публичной странице — оговорка и чек-лист на обоих языках', () => {
    expect(Object.keys(kkPub).sort()).toEqual(Object.keys(ruPub).sort())
    for (const k of Object.keys(ruPub) as (keyof typeof ruPub)[]) expect(kkPub[k], k).not.toBe(ruPub[k])
    const ru = i18nFor('ru'), kk = i18nFor('kk')
    expect(ru.t('chk.disc', { d: '15 октября 2026' })).toContain('отсутствие записи не означает нарушения')
    expect(ru.t('chk.disc', { d: '15 октября 2026' })).toContain('не юридическое заключение')
    expect(kk.t('chk.disc', { d: '2026 ж. 15 қазан' })).toContain('заңгерлік қорытынды емес')
    expect(kk.t('chk.listH')).not.toBe(ru.t('chk.listH'))
    expect(ru.tn('chk.count', 15)).toBe('В реестре 15 объектов Усть-Каменогорска')
  })

  it('на публичной странице нет слов оценки: балла, подозрения, нарушителя', () => {
    const pub = Object.entries(ruPub).filter(([k]) => k.startsWith('chk.')).map(([, v]) => v).join(' ').toLowerCase()
    for (const w of ['подозрен', 'балл', 'риск', 'нарушител', 'незаконн']) expect(pub, w).not.toContain(w)
  })
})
