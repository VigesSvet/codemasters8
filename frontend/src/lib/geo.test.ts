import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GeoCandidate, GeoCheck } from '../data/types'
import { ADMIN_HEADER, NEED_ADMIN, clearAdminToken, setAdminToken } from './admin'
import { ApiError, UNREACHABLE } from './api'
import {
  acceptGeo, acceptLines, awaitingDecision, cacheLine, candidateDistance, candidateLine, candidatePoint, decisionLine, defaultGeoQuery, fmtDist,
  fmtPoint, getGeoChecks, getGeoStatus, haversineM, isExact, precisionLabel, quotaLine, rejectGeo, runGeoCheck, toLngLat, validPoint,
} from './geo'

const TOKEN = 't-admin-0123456789abcdef0123'
/** Точка объекта ASYLTAS в открытых данных (Homeportal) и кандидат 2ГИС «Увалиева 8/3» */
const ASYLTAS = { lat: 49.887791, lng: 82.610139 }
const cand = (x: Partial<GeoCandidate> = {}): GeoCandidate => ({
  id: '70030077103568943', name: 'ЖК Asyl Tas', fullName: 'Усть-Каменогорск, Увалиева, 8/3', addressName: 'Увалиева, 8/3', purpose: 'Жилой дом',
  type: 'building', precision: 'exact', lat: 49.88873, lon: 82.610583, city: 'Усть-Каменогорск', distanceM: 109.4, recommended: true, ...x,
})
const check = (x: Partial<GeoCheck> = {}): GeoCheck => ({
  id: 7, targetKind: 'complex', targetId: 'c1', provider: '2gis', query: 'Казахстан, Усть-Каменогорск, Увалиева 8/3', status: 'candidates',
  candidates: [cand(), cand({ id: '70030077103568944', name: '8/3а', fullName: 'Усть-Каменогорск, Увалиева, 8/3а', precision: 'number', recommended: false })],
  fetchedAt: '2026-10-16T05:00:00Z', cached: false, purged: false, applied: false, expiresAt: '2026-10-23T05:00:00Z', ...x,
})

const reply = (status: number, body: unknown, type = 'application/json') =>
  vi.fn(async (_url: string, _init?: RequestInit) => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers: { 'Content-Type': type } }))
const sent = (f: ReturnType<typeof reply>, i = 0) => {
  const [url, init] = f.mock.calls[i]
  return { url, method: init?.method ?? 'GET', headers: (init?.headers ?? {}) as Record<string, string>, body: init?.body ? JSON.parse(String(init.body)) : undefined }
}
async function failure(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError)
    return e as ApiError
  }
  throw new Error('запрос должен был завершиться ошибкой')
}

beforeEach(() => clearAdminToken(null))
afterEach(() => {
  clearAdminToken(null)
  vi.unstubAllGlobals()
})

describe('координаты и расстояние', () => {
  it('ASYLTAS в открытых данных и 2ГИС «Увалиева 8/3» — около 110 м', () => {
    const d = haversineM(ASYLTAS, cand())
    expect(Math.abs(d - 110)).toBeLessThanOrEqual(15)
    expect(haversineM(cand(), ASYLTAS)).toBeCloseTo(d, 6)
    expect(haversineM(ASYLTAS, ASYLTAS)).toBe(0)
  })

  it('порядок: у 2ГИС lat/lon, у ЖК lat/lng, у карты [lng, lat]', () => {
    expect(candidatePoint(cand())).toEqual({ lat: 49.88873, lng: 82.610583 })
    expect(toLngLat(cand())).toEqual([82.610583, 49.88873])
    expect(toLngLat(ASYLTAS)).toEqual([82.610139, 49.887791])
    expect(fmtPoint(cand())).toBe('49.888730, 82.610583')
  })

  it('пустые и выдуманные координаты не считаются', () => {
    expect([validPoint(ASYLTAS), validPoint(null), validPoint({ lat: 0, lng: 0 }), validPoint({ lat: 91, lng: 1 }), validPoint({ lat: Number.NaN, lng: 1 })])
      .toEqual([true, false, false, false, false])
    expect(candidateDistance(cand({ distanceM: 120 }), ASYLTAS)).toBe(120)
    expect(Math.round(candidateDistance(cand({ distanceM: null }), ASYLTAS)!)).toBeGreaterThan(95)
    expect(candidateDistance(cand({ distanceM: null }), null)).toBeNull()
  })

  it('расстояние по-русски', () => {
    expect([0, 109.6, 999.4, 999.6, 1234, 9960, 12345].map(fmtDist)).toEqual(['0 м', '110 м', '999 м', '1,0 км', '1,2 км', '10 км', '12 км'])
    expect(fmtDist(Number.NaN)).toBe('—')
  })
})

describe('кандидаты 2ГИС', () => {
  it('точность адреса', () => {
    expect(['exact', 'number', 'near', 'street', 'other', undefined, null, 'что-то'].map(precisionLabel))
      .toEqual(['точный адрес', 'отличается литера/корпус', 'ближайший дом', 'только улица', 'неточно', 'неточно', 'неточно', 'неточно'])
    expect(precisionLabel('toString')).toBe('неточно')
  })

  it('точный дом — только exact: «8/3а» вместо «8/3» не точный', () => {
    expect(check().candidates.map(isExact)).toEqual([true, false])
  })

  it('строка кандидата и окно «Принять»', () => {
    expect(candidateLine(cand(), ASYLTAS)).toBe('Жилой дом · точный адрес · 49.888730, 82.610583 · в 109 м от точки в карточке')
    expect(candidateLine(cand({ distanceM: null, purpose: null }), null)).toBe('точный адрес · 49.888730, 82.610583')
    expect(acceptLines(cand(), ASYLTAS)).toEqual({ now: 'Сейчас в карточке: 49.887791, 82.610139', next: '2ГИС: 49.888730, 82.610583 (109 м)' })
    expect(acceptLines(cand({ distanceM: null }), null)).toEqual({ now: 'Сейчас в карточке: координат нет', next: '2ГИС: 49.888730, 82.610583' })
  })
})

describe('подписи панели', () => {
  const fmt = (iso: string) => `[${iso.slice(0, 10)}]`

  it('запрос по умолчанию — как на сервере', () => {
    expect(defaultGeoQuery(' Увалиева 8/3 ')).toBe('Казахстан, Усть-Каменогорск, Увалиева 8/3')
  })

  it('квота', () => {
    expect(quotaLine({ configured: true, used: 3, limit: 1000, usedToday: 1, dailyLimit: 50, cacheDays: 7 }))
      .toBe('Запросов 2ГИС по ключу: 3 из 1000 · сегодня 1 из 50')
    expect(quotaLine({ configured: true, used: 3, limit: null, usedToday: 1, dailyLimit: null, cacheDays: 7 })).toBe('Запросов 2ГИС по ключу: 3 · сегодня 1')
  })

  it('кэш и удалённые по сроку кандидаты', () => {
    expect(cacheLine(check(), fmt)).toBe('Кандидаты от [2026-10-16] (кэш до [2026-10-23])')
    expect(cacheLine(check({ purged: true, candidates: [] }), fmt)).toBe('Кандидаты удалены по сроку хранения — выполните сверку заново')
    expect(awaitingDecision(check())).toBe(true)
    expect(awaitingDecision(check({ purged: true }))).toBe(false)
  })

  it('решение аналитика', () => {
    const base = { decidedBy: 'Омарова Ж.', decidedAt: '2026-10-16T06:00:00Z', chosenId: '70030077103568943' }
    expect(decisionLine(check({ ...base, status: 'accepted', applied: true }), fmt))
      .toBe('Принято: Усть-Каменогорск, Увалиева, 8/3 — Омарова Ж., [2026-10-16], координаты перенесены')
    expect(decisionLine(check({ ...base, status: 'accepted', candidates: [] }), fmt))
      .toBe('Принято: объект 2ГИС 70030077103568943 — Омарова Ж., [2026-10-16], координаты не менялись')
    expect(decisionLine(check({ ...base, status: 'rejected' }), fmt)).toBe('Кандидаты отклонены — Омарова Ж., [2026-10-16]')
    expect(decisionLine(check({ status: 'not_found', candidates: [] }), fmt)).toBe('2ГИС не нашёл адрес — уточните запрос')
    expect(decisionLine(check(), fmt)).toBeNull()
  })
})

describe('запросы 2ГИС', () => {
  it('открытие карточки — только сохранённые сверки и квота, без 2ГИС; недоступно — null', async () => {
    const f = reply(200, [check()])
    vi.stubGlobal('fetch', f)
    expect(await getGeoChecks('complex', 'c 1')).toHaveLength(1)
    expect(sent(f)).toMatchObject({ url: '/api/geo/checks?targetKind=complex&targetId=c+1', method: 'GET' })
    vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
    expect(await getGeoChecks('registry', 'asyltas')).toBeNull()
    expect(await getGeoStatus()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('NetworkError') }))
    expect(await getGeoStatus()).toBeNull()
  })

  it('«Сверить адрес» — без токена администратора, даже если он введён', async () => {
    setAdminToken(TOKEN)
    const f = reply(201, check())
    vi.stubGlobal('fetch', f)
    await runGeoCheck({ targetKind: 'complex', targetId: 'c1', query: '  ', author: 'Омарова Ж.' })
    const r = sent(f)
    expect([r.url, r.method, r.headers[ADMIN_HEADER], r.body]).toEqual(['/api/geo/checks', 'POST', undefined, { targetKind: 'complex', targetId: 'c1', author: 'Омарова Ж.' }])
  })

  it('«Обновить запрос» (force) — только администратор, токен уходит заголовком', async () => {
    const f = reply(201, check())
    vi.stubGlobal('fetch', f)
    const e = await failure(runGeoCheck({ targetKind: 'complex', targetId: 'c1', author: 'Омарова Ж.', force: true }))
    expect([e.status, e.message]).toEqual([401, NEED_ADMIN])
    expect(f).not.toHaveBeenCalled()
    setAdminToken(TOKEN)
    await runGeoCheck({ targetKind: 'complex', targetId: 'c1', author: 'Омарова Ж.', force: true })
    expect(sent(f).headers[ADMIN_HEADER]).toBe(TOKEN)
    expect(sent(f).body.force).toBe(true)
  })

  it('503/502/429 сервера — его текст, а не «нет связи»; 502 прокси — нет связи', async () => {
    const body = { targetKind: 'complex' as const, targetId: 'c1', author: 'Омарова Ж.' }
    vi.stubGlobal('fetch', reply(503, { detail: '2ГИС не настроен на сервере (DGIS_API_KEY)' }))
    expect((await failure(runGeoCheck(body))).message).toBe('2ГИС не настроен на сервере (DGIS_API_KEY)')
    vi.stubGlobal('fetch', reply(502, { detail: '2ГИС не ответил — повторите позже' }))
    const e = await failure(runGeoCheck(body))
    expect([e.status, e.message]).toEqual([502, '2ГИС не ответил — повторите позже'])
    vi.stubGlobal('fetch', reply(429, { detail: 'Сверку этого объекта уже запрашивали — повторите через 7 мин' }))
    expect((await failure(runGeoCheck(body))).message).toBe('Сверку этого объекта уже запрашивали — повторите через 7 мин')
    vi.stubGlobal('fetch', reply(502, '', 'text/plain'))
    expect((await failure(runGeoCheck(body))).message).toBe(UNREACHABLE)
  })

  it('принять и отклонить', async () => {
    const f = reply(200, check({ status: 'accepted' }))
    vi.stubGlobal('fetch', f)
    await acceptGeo(7, { candidateId: '70030077103568943', author: 'Омарова Ж.', apply: true, comment: '  ' })
    await rejectGeo(7, { author: 'Омарова Ж.', comment: ' не тот дом ' })
    expect(sent(f, 0)).toMatchObject({ url: '/api/geo/checks/7/accept', method: 'POST', body: { candidateId: '70030077103568943', author: 'Омарова Ж.', apply: true } })
    expect(sent(f, 0).body.comment).toBeUndefined()
    expect(sent(f, 1)).toMatchObject({ url: '/api/geo/checks/7/reject', body: { author: 'Омарова Ж.', comment: 'не тот дом' } })
  })
})
