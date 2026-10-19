import { describe, expect, it } from 'vitest'
import type { DeveloperScreening, Permit, Screening, ScreeningReason } from '../data/types'
import {
  DEV_SCREENING_LABEL, LEGEND_LABEL, OK_LEGEND, SCREENING_FILTER_LABEL, SCREENING_GLYPH, SCREENING_HINT, SCREENING_LABEL, SCREENING_ORDER,
  SCREENING_TONE, VARIANT_LABEL, VARIANT_SHORT, adReasons, anyScreening, compareDevScreening, compareScreening, countByStatus, countsLine,
  decidedCounts, decidedLine, decisionReasons, devCountsLine, hasPoint, inScreening, isScreeningStatus, labelOf, matchesScreening,
  parseScreeningParam, permitIndex, permitLinks, permitReason, permitSource, reasonParts, reasonShort, screeningRank, shortOf, signalName,
  statusOf, topReason, topReasons, variantOf,
} from './screening'

const DISCLAIMER = 'Предварительная автоматическая оценка по рекламе и открытым данным — не вывод о нарушении'
const PERMIT_TEXT = 'Нет в открытом реестре: ЖК известен только по рекламе'
/** Причины как у сервера: основание первым, затем — по важности; цитата вписана в текст */
const BOOKING: ScreeningReason = {
  text: 'Реклама 07.10.2026 (Instagram, @bs_holding.oskemen): бронь — «забронировать квартиру своей мечты»; название из рекламы привязано к ЖК аналитиком, публикация — гипотеза',
  adId: '9107be31', quote: 'забронировать квартиру своей мечты', signal: 'booking',
}
const STAGE: ScreeningReason = { text: 'Стадия по рекламе: строится — «открыто показываем все испытания на стройке»', adId: '9107be31', quote: 'открыто показываем все испытания на стройке', signal: null }
const scr = (x: Partial<Screening> = {}): Screening => ({
  status: 'suspect', label: SCREENING_LABEL.suspect, basis: 'hypothesis', inRegistry: false, disclaimer: DISCLAIMER,
  reasons: [{ text: PERMIT_TEXT }, BOOKING, STAGE], counts: { ads: 9, concerning: 1, lastAdAt: '2026-10-17T01:18', windowDays: 120 }, ...x,
})
const devScr = (x: Partial<DeveloperScreening> = {}): DeveloperScreening => ({
  status: 'check', label: DEV_SCREENING_LABEL.check, reasons: [], basis: 'confirmed', disclaimer: DISCLAIMER, worst: [],
  counts: { complexes: 3, ok: 1, check: 2, suspect: 0, unknown: 0 }, ...x,
})

describe('подписи и цвета', () => {
  it('четыре статуса: подписи как у сервера, знаки и цвета карты', () => {
    expect(SCREENING_LABEL).toEqual({ ok: 'В реестре — замечаний нет', check: 'Требует проверки', suspect: 'Подозрение: продажа без разрешения', unknown: 'Нет данных для оценки' })
    expect(DEV_SCREENING_LABEL.suspect).toBe('Подозрение: есть ЖК с продажей без разрешения')
    expect(DEV_SCREENING_LABEL.ok).toBe('Все ЖК в реестре — замечаний нет')
    expect(SCREENING_GLYPH).toEqual({ ok: '✓', check: '?', suspect: '!', unknown: '—' })
    expect(SCREENING_TONE).toEqual({ ok: 'green', check: 'yellow', suspect: 'red', unknown: 'gray' })
    expect(SCREENING_FILTER_LABEL).toEqual({ ok: 'Без замечаний', check: 'Требуют проверки', suspect: 'Подозрение', unknown: 'Нет данных' })
    expect(SCREENING_HINT.ok).toBe('Без замечаний: в реестре, дом сдан или проверено аналитиком')
    expect(OK_LEGEND).toBe('Зелёный ✓ — в реестре, сдан или проверено аналитиком')
    expect(LEGEND_LABEL.ok).toBe('В реестре, сдан или проверено аналитиком — замечаний нет')
    expect(LEGEND_LABEL.suspect).toBe(SCREENING_LABEL.suspect)
  })

  it('подпись — с сервера, а без неё — своя (ЖК и застройщик различаются)', () => {
    expect(labelOf(scr({ label: 'Подозрение (сервер)' }))).toBe('Подозрение (сервер)')
    expect(labelOf(scr({ label: '  ' }))).toBe(SCREENING_LABEL.suspect)
    expect(labelOf({ status: 'suspect', label: '' }, true)).toBe(DEV_SCREENING_LABEL.suspect)
    expect(labelOf(null)).toBe('')
  })

  it('статус сущности: неизвестное значение и отсутствие оценки — null', () => {
    expect(statusOf({ screening: scr() })).toBe('suspect')
    expect(statusOf({ screening: null })).toBeNull()
    expect(statusOf({})).toBeNull()
    expect(statusOf({ screening: { status: 'bad' } })).toBeNull()
    expect(isScreeningStatus('ok')).toBe(true)
    expect(isScreeningStatus('red')).toBe(false)
    // сервер старой версии: у ЖК нет оценки — показываем «доступно с сервером»
    expect(anyScreening([{}, { screening: null }])).toBe(false)
    expect(anyScreening([{}, { screening: scr() }])).toBe(true)
  })
})

describe('без замечаний по решению аналитика: «сдан» и «проверено»', () => {
  const ok = (variant: 'commissioned' | 'analyst' | null, label = '') => scr({ status: 'ok', label, variant })
  it('вариант — только у статуса ok и только известный', () => {
    expect(variantOf(ok('commissioned'))).toBe('commissioned')
    expect(variantOf(ok('analyst'))).toBe('analyst')
    expect(variantOf(ok(null))).toBeNull()
    expect(variantOf(scr({ status: 'check', variant: 'analyst' }))).toBeNull()
    expect(variantOf(scr({ status: 'ok', variant: 'future' as never }))).toBeNull()
    expect(variantOf(scr({ status: 'ok', variant: undefined }))).toBeNull() // сервер старой версии
    expect(variantOf(null)).toBeNull()
  })

  it('подписи: свои — коротко «сдан» / «проверено», полностью — как у сервера; зелёный цвет и ✓ — как у ok', () => {
    expect(VARIANT_LABEL).toEqual({ commissioned: 'Дом сдан — замечаний нет', analyst: 'Проверено — замечаний нет' })
    expect(VARIANT_SHORT).toEqual({ commissioned: 'сдан', analyst: 'проверено' })
    expect(shortOf(ok('commissioned'))).toBe('сдан')
    expect(shortOf(ok('analyst'))).toBe('проверено')
    expect(shortOf(ok(null))).toBe('без замечаний')
    expect(shortOf(scr())).toBe('подозрение')
    expect(labelOf(ok('commissioned', 'Дом сдан — замечаний нет (сервер)'))).toBe('Дом сдан — замечаний нет (сервер)')
    expect(labelOf(ok('commissioned'))).toBe('Дом сдан — замечаний нет')
    expect(labelOf(ok('analyst'))).toBe('Проверено — замечаний нет')
    expect(labelOf(ok(null))).toBe(SCREENING_LABEL.ok)
    expect(labelOf({ status: 'ok', label: '', variant: 'commissioned' }, true)).toBe(DEV_SCREENING_LABEL.ok)
    expect([SCREENING_TONE.ok, SCREENING_GLYPH.ok]).toEqual(['green', '✓'])
  })

  it('счётчики решений: по ЖК — как stats.screening.decisions; строка — только «сдан» и «проверено»', () => {
    const xs = [{ screening: ok('commissioned') }, { screening: ok('commissioned') }, { screening: ok('analyst') }, { screening: ok(null) },
      { screening: scr({ status: 'check' }), decision: { status: 'reopened' } }, { screening: null }]
    expect(decidedCounts(xs)).toEqual({ commissioned: 2, analyst: 1, reopened: 1 })
    expect(decidedLine(decidedCounts(xs))).toBe('сдан — 2 · проверено — 1')
    expect(decidedLine({ commissioned: 0, analyst: 3, reopened: 2 })).toBe('проверено — 3')
    expect(decidedLine({ commissioned: 0, analyst: 0, reopened: 0 })).toBe('')
    expect(decidedLine(undefined)).toBe('')
  })
})

describe('порядок «подозрение первыми»', () => {
  it('подозрение, проверка, нет данных, без замечаний; без оценки — в конце', () => {
    expect(SCREENING_ORDER).toEqual(['suspect', 'check', 'unknown', 'ok'])
    expect(['ok', undefined, 'unknown', 'suspect', 'check'].map((s) => screeningRank(s as never))).toEqual([3, 4, 2, 0, 1])
  })

  it('при равном статусе — больше свежей рекламы с приёмом денег, затем больше рекламы', () => {
    const a = scr({ status: 'check', counts: { ads: 2, concerning: 0, windowDays: 120 } })
    const b = scr({ status: 'check', counts: { ads: 1, concerning: 1, windowDays: 120 } })
    const c = scr({ status: 'check', counts: { ads: 5, concerning: 0, windowDays: 120 } })
    const s = scr({ status: 'suspect', counts: { ads: 1, concerning: 0, windowDays: 120 } })
    expect([a, b, c, null, s].sort(compareScreening)).toEqual([s, b, c, a, null])
  })

  it('застройщики: худший статус, затем больше подозрительных и требующих проверки ЖК', () => {
    const one = devScr({ status: 'check', counts: { complexes: 1, ok: 0, check: 1, suspect: 0, unknown: 0 } })
    const two = devScr({ status: 'check', counts: { complexes: 3, ok: 1, check: 2, suspect: 0, unknown: 0 } })
    const bad = devScr({ status: 'suspect', counts: { complexes: 4, ok: 3, check: 0, suspect: 1, unknown: 0 } })
    const ok = devScr({ status: 'ok', counts: { complexes: 2, ok: 2, check: 0, suspect: 0, unknown: 0 } })
    expect([ok, one, bad, two].sort(compareDevScreening)).toEqual([bad, two, one, ok])
  })
})

describe('причины', () => {
  it('reasons[0] — основание, остальное — по рекламе в порядке сервера', () => {
    expect(adReasons(scr())).toEqual([BOOKING, STAGE])
    expect(topReason(scr())).toBe(BOOKING)
    expect(topReasons(scr(), 1)).toEqual([BOOKING])
    expect(topReason(scr({ reasons: [{ text: PERMIT_TEXT }] }))).toBeNull()
    expect(topReason(null)).toBeNull()
    expect([permitIndex(scr()), permitReason(scr())?.text, permitIndex(null), permitReason(null)]).toEqual([0, PERMIT_TEXT, -1, null])
  })

  it('решение аналитика: его причины — первыми, основание — следом и в списке по рекламе не повторяется', () => {
    const EVIDENCE: ScreeningReason = { text: 'Сдан: акт № 12 от 01.09.2026, источник ЕГКН; подтвердил: Омарова Ж., 19.10.2026', kind: 'decision' }
    const s = scr({ status: 'ok', variant: 'commissioned', reasons: [EVIDENCE, { text: PERMIT_TEXT, kind: null }, { text: 'Без решения аналитика оценка — «Требует проверки»' }] })
    expect(permitIndex(s)).toBe(1)
    expect(permitReason(s)?.text).toBe(PERMIT_TEXT)
    expect(adReasons(s).map((r) => r.text)).toEqual([EVIDENCE.text, 'Без решения аналитика оценка — «Требует проверки»'])
    expect(topReason(s)).toBe(EVIDENCE)
    expect(decisionReasons(s)).toEqual([EVIDENCE])
    // снято новой рекламой: первая причина — эта реклама, со ссылкой и цитатой
    const NEW: ScreeningReason = { text: 'Новая реклама после решения аналитика (12.10.2026): …', adId: 'a9', quote: 'бронь', signal: 'booking', kind: 'decision' }
    const r = scr({ reasons: [NEW, { text: PERMIT_TEXT }, BOOKING] })
    expect(adReasons(r)).toEqual([NEW, BOOKING])
    expect(reasonShort(topReason(r)!)).toBe('бронь: «бронь»')
    expect(decisionReasons(scr())).toEqual([])
  })

  it('цитата, вписанная в текст, выделяется на месте, а не повторяется', () => {
    expect(reasonParts(STAGE)).toEqual({ before: 'Стадия по рекламе: строится — ', quote: STAGE.quote, after: '', inline: true })
    const p = reasonParts(BOOKING)
    expect([p.inline, p.before.endsWith('бронь — '), p.after.startsWith('; название')]).toEqual([true, true, true])
    expect(reasonParts({ text: 'Реклама', quote: 'бронь от 100 000' })).toEqual({ before: 'Реклама', quote: 'бронь от 100 000', after: '', inline: false })
    expect(reasonParts({ text: 'Рекламы по объекту не найдено', quote: null })).toEqual({ before: 'Рекламы по объекту не найдено', quote: null, after: '', inline: false })
  })

  it('коротко для списка: признак и цитата, иначе текст', () => {
    expect(reasonShort(BOOKING)).toBe('бронь: «забронировать квартиру своей мечты»')
    expect(reasonShort(STAGE)).toBe(STAGE.text)
    expect(reasonShort({ text: 'Реклама есть (3), но без признаков приёма денег за последние 120 дней', signal: 'sale' })).toBe('Реклама есть (3), но без признаков приёма денег за последние 120 дней')
  })

  it('признаки — по-русски, неизвестные — как есть', () => {
    expect(['booking', 'presale', 'ddu', 'installment', 'down_payment', 'money', 'deposit', 'xyz', null].map(signalName))
      .toEqual(['бронь', 'старт продаж', 'ДДУ', 'рассрочка', 'первоначальный взнос', 'предложение внести деньги', 'задаток', 'xyz', ''])
  })
})

describe('счётчики и строки', () => {
  it('реклама: публикации, свежие с признаком приёма денег, последняя', () => {
    expect(countsLine(scr().counts, () => '17 окт.')).toBe('9 публ. · 1 с признаком приёма денег за 120 дн. · последняя 17 окт.')
    expect(countsLine({ ads: 3, concerning: 0, windowDays: 90 })).toBe('3 публ. · без признаков приёма денег за 90 дн.')
    expect(countsLine({ ads: 0, concerning: 0, windowDays: 120 })).toBe('Рекламы не найдено')
    expect(countsLine(null)).toBe('Рекламы не найдено')
  })

  it('по статусам: none — без оценки', () => {
    const xs = [{ screening: scr() }, { screening: scr({ status: 'ok' }) }, { screening: scr({ status: 'ok' }) }, {}]
    expect(countByStatus(xs, statusOf)).toEqual({ ok: 2, check: 0, suspect: 1, unknown: 0, none: 1 })
  })

  it('застройщик: «3 ЖК: требует проверки — 2, без замечаний — 1»', () => {
    expect(devCountsLine(devScr().counts)).toBe('3 ЖК: требует проверки — 2, без замечаний — 1')
    expect(devCountsLine({ complexes: 0, ok: 0, check: 0, suspect: 0, unknown: 0 })).toBe('ЖК на контроле нет')
  })
})

describe('фильтры', () => {
  it('параметр адреса ?screening=: неизвестное и повторы отбрасываются', () => {
    expect(parseScreeningParam('suspect,check,red,suspect')).toEqual(['suspect', 'check'])
    expect(parseScreeningParam(null)).toEqual([])
    expect(parseScreeningParam(' ok ')).toEqual(['ok'])
  })

  it('пустой выбор — все; без оценки — только «все»', () => {
    expect(inScreening('ok', [])).toBe(true)
    expect(inScreening(null, [])).toBe(true)
    expect(inScreening(null, ['unknown'])).toBe(false)
    expect(inScreening('check', ['suspect', 'check'])).toBe(true)
    expect(matchesScreening(null, 'all')).toBe(true)
    expect(matchesScreening('suspect', 'suspect')).toBe(true)
    expect(matchesScreening('ok', 'suspect')).toBe(false)
  })
})

describe('основание и координаты', () => {
  const permit = (x: Partial<Permit> = {}): Permit => ({
    status: 'not_permitted', text: 'раздел «Без разрешения на дольщиков»', source: 'homeportal_section', url: 'https://homeportal.kz/ru/Illegal-projects/1813',
    registryKey: 'ab-residence 2', registryStage: null, verified: false, ...x,
  })
  it('ссылки: карточка открытых данных внутри приложения и страница источника (только http/https)', () => {
    expect(permitLinks(permit())).toEqual({ registry: '/registry/ab-residence%202', url: 'https://homeportal.kz/ru/Illegal-projects/1813' })
    expect(permitLinks(permit({ registryKey: null, url: 'javascript:alert(1)' }))).toEqual({})
    expect(permitLinks(null)).toEqual({})
  })

  it('источник по-русски', () => {
    expect(permitSource('homeportal')).toBe('Homeportal')
    expect(permitSource('analyst')).toBe('сверка аналитика')
    expect(permitSource('other')).toBe('other')
    expect(permitSource(null)).toBe('')
  })

  it('координаты: обе есть и похожи на настоящие; null и (0, 0) — нет', () => {
    expect(hasPoint({ lat: 49.92, lng: 82.6 })).toBe(true)
    expect(hasPoint({ lat: null, lng: null })).toBe(false)
    expect(hasPoint({ lat: 49.92, lng: null })).toBe(false)
    expect(hasPoint({ lat: 0, lng: 0 })).toBe(false)
    expect(hasPoint({ lat: 91, lng: 10 })).toBe(false)
  })
})
