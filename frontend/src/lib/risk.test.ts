// Паритет TS-движка с эталонным Python (backend/app/risk.py) на shared/risk_golden.json.
// Эталон перегенерируется: cd backend && .venv/bin/python -m app.golden
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Ad, AdScore, Appeal, Complex, Developer, RiskResult } from '../data/types'
import { linkConfirmed, model, quote, scoreAd, scoreComplex, termsRejected, zoneFor } from './risk'

const shared = (name: string) => fileURLToPath(new URL(`../../../shared/${name}`, import.meta.url))
const read = <T>(name: string): T => JSON.parse(readFileSync(shared(name), 'utf-8')) as T

interface Golden {
  asOf: string
  modelVersion: string
  seed: { complexes: Record<string, RiskResult>; ads: Record<string, AdScore> }
  fixtures: { complex: Record<string, RiskResult>; ad: Record<string, AdScore> }
}
interface Seed { developers: Developer[]; complexes: Complex[]; ads: Ad[]; appeals: Appeal[] }
interface Fixtures {
  complex?: { name: string; asOf?: string; complex: Complex; developer?: Developer | null; ads?: Ad[]; appeals?: Appeal[] }[]
  ad?: { name: string; ad: Ad; complex?: Complex | null }[]
}

const golden = read<Golden>('risk_golden.json')
const seed = read<Seed>('seed_data.json')
// risk_fixtures.json может ещё не существовать или быть пустым — тогда проверяем только сид
const fxRaw = existsSync(shared('risk_fixtures.json')) ? readFileSync(shared('risk_fixtures.json'), 'utf-8').trim() : ''
const fx: Fixtures = fxRaw ? JSON.parse(fxRaw) : {}

const devs = new Map(seed.developers.map((d) => [d.id, d]))
const cxs = new Map(seed.complexes.map((c) => [c.id, c]))
const scoreSeed = (c: Complex, asOf = golden.asOf) => scoreComplex(c, devs.get(c.developerId), seed.ads, seed.appeals, asOf)

describe('паритет с risk.py: сид', () => {
  it('версия модели совпадает с эталоном', () => expect(golden.modelVersion).toBe(model.version))
  it('эталон покрывает весь сид', () => {
    expect(Object.keys(golden.seed.complexes).sort()).toEqual(seed.complexes.map((c) => c.id).sort())
    expect(Object.keys(golden.seed.ads).sort()).toEqual(seed.ads.map((a) => a.id).sort())
  })
  it.each(seed.complexes.map((c) => [c.id, c] as const))('ЖК %s', (id, c) => {
    expect(scoreSeed(c)).toEqual(golden.seed.complexes[id])
  })
  it.each(seed.ads.map((a) => [a.id, a] as const))('публикация %s', (id, a) => {
    expect(scoreAd(a, a.complexId ? cxs.get(a.complexId) : undefined)).toEqual(golden.seed.ads[id])
  })
})

describe('паритет с risk.py: фикстуры', () => {
  const complexFx = fx.complex ?? [], adFx = fx.ad ?? []
  it('эталон построен по текущим фикстурам', () => {
    expect(Object.keys(golden.fixtures.complex).sort()).toEqual(complexFx.map((f) => f.name).sort())
    expect(Object.keys(golden.fixtures.ad).sort()).toEqual(adFx.map((f) => f.name).sort())
  })
  if (complexFx.length) it.each(complexFx.map((f) => [f.name, f] as const))('ЖК: %s', (name, f) => {
    expect(scoreComplex(f.complex, f.developer, f.ads ?? [], f.appeals ?? [], f.asOf ?? golden.asOf)).toEqual(golden.fixtures.complex[name])
  })
  if (adFx.length) it.each(adFx.map((f) => [f.name, f] as const))('публикация: %s', (name, f) => {
    expect(scoreAd(f.ad, f.complex)).toEqual(golden.fixtures.ad[name])
  })
})

describe('контрольные кейсы и правила §5', () => {
  it.each(model.cases.map((k) => [k.id, k] as const))('кейс %s', (_, k) => {
    const r = scoreSeed(cxs.get(k.complexId)!)
    expect(r.factors.filter((f) => f.state === 'hit').map((f) => f.code)).toEqual(k.hits)
    expect(r.factors.filter((f) => f.state === 'unknown').map((f) => f.code)).toEqual(expect.arrayContaining(k.unknown))
    expect([r.score, r.zone]).toEqual([k.score, k.zone])
  })
  it('красная зона только при R1 + R2', () => {
    expect(zoneFor(70, ['R1', 'R2'])).toBe('red')
    expect(zoneFor(70, ['R1', 'R3', 'R4'])).toBe('yellow')
    expect(zoneFor(24, [])).toBe('green')
  })
  it('привязка — факт только после подтверждения аналитиком, уверенность ИИ не в счёт', () => {
    for (const matchConfidence of [0.79, 0.8, 0.97, 1]) {
      expect(linkConfirmed({ status: 'new', matchConfidence } as Ad)).toBe(false)
    }
    expect(linkConfirmed({ status: 'new', linkConfirmedBy: 'Нестерова А.' })).toBe(true)
    expect(linkConfirmed({ status: 'confirmed', linkConfirmedBy: null })).toBe(true)
    // пустое имя — не подтверждение; пробелы — те же, что отрезает str.strip() в risk.py
    for (const blank of ['', '  ', '\u00a0\u3000\t\n']) expect(linkConfirmed({ status: 'new', linkConfirmedBy: blank })).toBe(false)
    expect(model.thresholds).not.toHaveProperty('linkConfirmed')
    expect(model.thresholds).not.toHaveProperty('autoLinkConfidence')
  })
  it('вето аналитика на условия оплаты снимает только R2', () => {
    expect([termsRejected({ termsReview: 'rejected' }), termsRejected({ termsReview: 'accepted' }), termsRejected({})])
      .toEqual([true, false, false])
    const a = { ...seed.ads.find((x) => x.id === 'a1')!, termsReview: 'rejected' } as Ad
    const base = scoreAd(seed.ads.find((x) => x.id === 'a1')!, cxs.get('c2')), r = scoreAd(a, cxs.get('c2'))
    expect(r.factors.map((f) => [f.code, f.state, f.reason])).toEqual([['R1', 'hit', 'absent'], ['R2', 'clear', 'R2_rejected'], ['R3', 'hit', 'R3_ads']])
    expect(r.factors[2]).toEqual(base.factors[2])
    expect([r.score, r.zone]).toEqual([45, 'yellow'])
  })
  it('отклонённая публикация с привязкой-гипотезой не попадает в R2 ЖК', () => {
    // a4 привязана к c8 гипотезой ИИ (71%), a8 — фактом; аналитик отклонил условия оплаты у обеих
    const ads = seed.ads.map((a) => (a.id === 'a4' || a.id === 'a8' ? { ...a, termsReview: 'rejected' } as Ad : a))
    const c = cxs.get('c8')!
    const r2 = scoreComplex(c, devs.get(c.developerId), ads, seed.appeals, golden.asOf).factors.find((f) => f.code === 'R2')!
    expect([r2.state, r2.reason, r2.items.map((i) => i.ref)]).toEqual(['clear', 'offer_rejected', ['a8']])
    expect(r2.evidence).toMatch(/^Аналитик отклонил условия оплаты в публикациях: 1 /)
  })
  it('R1 по провенансу документов: своя запись целиком, иначе общая сверка; ссылка — только своя', () => {
    const c2 = cxs.get('c2')!, r1 = scoreSeed(c2).factors[0]
    expect([r1.state, r1.reason]).toEqual(['hit', 'absent'])
    expect(r1.evidence).toBe('Проверкой установлено: гарантии ЕО нет (Реестр гарантий КЖК, homeportal.kz (демо), 09.10.2026); ' +
      'разрешения МИО нет (Реестр разрешений МИО, elicense.kz (демо), 09.10.2026)')
    expect(r1.items.map((i) => [i.ref, i.url])).toEqual([['guarantee', 'https://homeportal.kz'], ['mio', 'https://elicense.kz'], ['bvu', undefined]])
    const basis = (sources: object, general = true) => ({
      ...c2, basis: { guarantee: 'no', mio: 'no', bvu: 'no', sources, ...(general ? { source: 'Картотека ГАСК (демо)', checkedAt: '2026-10-09' } : {}) },
    } as unknown as Complex)
    const own = { source: 'Реестр (демо)', url: 'https://example.kz', checkedAt: '2026-10-08' }
    // только у гарантии, у МИО ни своей записи, ни общей сверки → нужна проверка с перечнем документов
    const only = scoreSeed(basis({ guarantee: own }, false)).factors[0]
    expect([only.state, only.reason, only.evidence]).toEqual(['unknown', 'absent_unsourced',
      'Отсутствие основания указано, но нет источника или даты сверки по документам: Разрешение МИО — нужна проверка'])
    // своя запись без даты чужую дату не занимает
    expect(scoreSeed(basis({ mio: { ...own, checkedAt: null } })).factors[0].reason).toBe('absent_unsourced')
    // один источник и дата у обоих — короткий текст
    expect(scoreSeed(basis({})).factors[0].evidence)
      .toBe('Проверкой установлено: гарантии ЕО нет и разрешения МИО нет (Картотека ГАСК (демо), 09.10.2026)')
    // документ «нет данных» не сверялся — без источника и даты, даже при общей сверке (c6: гарантия)
    const c6 = scoreSeed(cxs.get('c6')!).factors[0].items.find((i) => i.ref === 'guarantee')!
    expect(c6).toEqual({ kind: 'document', ref: 'guarantee', label: 'Гарантия ЕО (КЖК)', value: 'unknown' })
  })
  it('цитата: длинное предложение обрезается вокруг маркера', () => {
    const text = 'Раз два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать бронь тринадцать ' +
      'четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать двадцать двадцать один двадцать два. Второе.'
    expect(quote(text, 'бронь')).toMatch(/^… пять .* бронь .* …$/)
    expect(quote('Звоните! Бронь от 500 000 ₸.', 'бронь')).toBe('Бронь от 500 000 ₸.')
  })
})

describe('дата расчёта по умолчанию — сегодня по UTC+5, как в risk.py', () => {
  afterEach(() => { vi.useRealTimers() })
  // r6_overdue_179_clear: срок сдачи 19.04.2026 — 15.10 это 179 дн. (clear), 16.10 — 180 дн. (hit)
  it.each([
    ['2026-10-15T18:59:00Z', '2026-10-15', 'clear'], // 23:59 в Усть-Каменогорске
    ['2026-10-15T19:00:00Z', '2026-10-16', 'hit'], // полночь по UTC+5, хотя по UTC ещё 15-е
    ['2026-10-15T23:30:00Z', '2026-10-16', 'hit'],
  ])('%s → %s', (now, day, state) => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(now))
    const f = (fx.complex ?? []).find((x) => x.name === 'r6_overdue_179_clear')!
    const r = scoreComplex(f.complex, f.developer, f.ads ?? [], f.appeals ?? [])
    expect(r).toEqual(scoreComplex(f.complex, f.developer, f.ads ?? [], f.appeals ?? [], day))
    expect(r.factors.find((x) => x.code === 'R6')?.state).toBe(state)
  })
})
