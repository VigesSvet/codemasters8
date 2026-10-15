// Паритет TS-движка с эталонным Python (backend/app/risk.py) на shared/risk_golden.json.
// Эталон перегенерируется: cd backend && .venv/bin/python -m app.golden
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Ad, AdScore, Appeal, Complex, Developer, RiskResult } from '../data/types'
import { model, quote, scoreAd, scoreComplex, zoneFor } from './risk'

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
