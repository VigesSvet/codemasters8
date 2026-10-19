import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Complex, Developer, Discovered, GeoCandidate, GeoCheck } from '../data/types'
import { ADMIN_HEADER, NEED_ADMIN, clearAdminToken, setAdminToken } from './admin'
import { ApiError, NO_ROUTE, UNREACHABLE } from './api'
import {
  DISCOVERED_TABS, countDiscovered, createBody, createForm, createFromDiscovered, createProblem, decisionText, dismissDiscovered, geoPicks,
  linkDiscovered, listDiscovered, mergeChecks, parseDiscoveredTab, pickComplexes, runDiscoveredGeo, saleLine, signalTypesLine, sourcesLine,
  stageLine,
} from './discovered'

const TOKEN = 't-admin-0123456789abcdef0123'
const AUTHOR = 'Омарова Ж.'
const cand = (x: Partial<GeoCandidate> = {}): GeoCandidate => ({
  id: 'g1', name: 'ЖК Nova Park', fullName: 'Усть-Каменогорск, Сатпаева, 15', addressName: 'Сатпаева, 15', purpose: 'Жилой дом', type: 'building',
  precision: 'near', lat: 49.95, lon: 82.62, city: 'Усть-Каменогорск', distanceM: null, recommended: false, ...x,
})
const check = (x: Partial<GeoCheck> = {}): GeoCheck => ({
  id: 1, targetKind: 'discovered', targetId: '7', provider: '2gis', query: 'Казахстан, Усть-Каменогорск, ЖК Nova Park', status: 'candidates',
  candidates: [cand()], fetchedAt: '2026-10-18T10:00:00Z', cached: false, purged: false, applied: false, ...x,
})
const obj = (x: Partial<Discovered> = {}): Discovered => ({
  id: 7, name: 'Nova Park', normKey: 'novapark', aliases: ['Нова Парк'], addressHint: 'Сатпаева 15', status: 'new', origin: 'ads', adsCount: 3,
  accounts: ['@nova.park'], sourceUrls: [], sources: { parser: 2, bot: 1, manual: 0 }, quotes: [],
  stageHints: { underConstruction: 2, completed: 0, unknown: 1, quotes: [] },
  saleSignals: { ads: 3, concerning: 2, recentConcerning: 1, types: { booking: 2, installment: 1 }, lastAdAt: '2026-10-17T01:18' }, geo: [], ...x,
})
const cx = (id: string, name: string, address = '', developerId = 'd1'): Complex => ({ id, name, address, developerId } as Complex)

const reply = (status: number, body: unknown) =>
  vi.fn(async (_url: string, _init?: RequestInit) => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
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

beforeEach(() => clearAdminToken())
afterEach(() => {
  clearAdminToken()
  vi.unstubAllGlobals()
})

describe('запросы', () => {
  it('список: массив; сервер без функции — null; нет связи — ошибка с текстом', async () => {
    const f = reply(200, [obj()])
    vi.stubGlobal('fetch', f)
    expect(await listDiscovered()).toHaveLength(1)
    expect(sent(f)).toMatchObject({ url: '/api/discovered', method: 'GET' })
    vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
    expect(await listDiscovered()).toBeNull()
    vi.stubGlobal('fetch', reply(200, '<html>'))
    expect(await listDiscovered()).toBeNull()
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('NetworkError') }))
    expect((await failure(listDiscovered())).message).toBe(UNREACHABLE)
  })

  it('«Найти в 2ГИС» — без токена; мимо кэша (force) — только администратор, токен заголовком', async () => {
    setAdminToken(TOKEN)
    const f = reply(201, { checks: [check()], errors: [] })
    vi.stubGlobal('fetch', f)
    await runDiscoveredGeo(7, AUTHOR)
    expect([sent(f).url, sent(f).method, sent(f).headers[ADMIN_HEADER], sent(f).body]).toEqual(['/api/discovered/7/geo', 'POST', undefined, { author: AUTHOR }])
    await runDiscoveredGeo(7, AUTHOR, true)
    expect([sent(f, 1).headers[ADMIN_HEADER], sent(f, 1).body]).toEqual([TOKEN, { author: AUTHOR, force: true }])
    clearAdminToken()
    const e = await failure(runDiscoveredGeo(7, AUTHOR, true))
    expect([e.status, e.message]).toEqual([401, NEED_ADMIN])
  })

  it('ошибки сервера — его текстом по-русски; старый сервер — «обновите бэкенд»', async () => {
    vi.stubGlobal('fetch', reply(503, { detail: '2ГИС не настроен на сервере (DGIS_API_KEY)' }))
    expect((await failure(runDiscoveredGeo(7, AUTHOR))).message).toBe('2ГИС не настроен на сервере (DGIS_API_KEY)')
    vi.stubGlobal('fetch', reply(409, { detail: 'ЖК «Nova Park» уже есть в справочнике ЖК (c3) — привяжите объект к нему' }))
    const e = await failure(createFromDiscovered(7, { author: AUTHOR, name: 'Nova Park' }))
    expect([e.status, e.message]).toEqual([409, 'ЖК «Nova Park» уже есть в справочнике ЖК (c3) — привяжите объект к нему'])
    vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
    expect((await failure(linkDiscovered(7, AUTHOR, 'c3'))).message).toBe(NO_ROUTE)
  })

  it('привязать и отклонить: тела запросов', async () => {
    const f = reply(200, obj({ status: 'linked', complexId: 'c3' }))
    vi.stubGlobal('fetch', f)
    await linkDiscovered(7, AUTHOR, 'c3')
    await dismissDiscovered(7, AUTHOR, '  подборка агентства  ')
    expect([sent(f).url, sent(f).body]).toEqual(['/api/discovered/7/link', { author: AUTHOR, complexId: 'c3' }])
    expect([sent(f, 1).url, sent(f, 1).body]).toEqual(['/api/discovered/7/dismiss', { author: AUTHOR, reason: 'подборка агентства' }])
  })
})

describe('вкладки и подписи', () => {
  it('вкладки: новые, привязаны, отклонены; неизвестное — новые', () => {
    expect(DISCOVERED_TABS.map(([k]) => k)).toEqual(['new', 'linked', 'dismissed'])
    expect([parseDiscoveredTab('linked'), parseDiscoveredTab('dismissed'), parseDiscoveredTab('x'), parseDiscoveredTab(null)]).toEqual(['linked', 'dismissed', 'new', 'new'])
    expect(countDiscovered([obj(), obj({ status: 'linked' }), obj()])).toEqual({ new: 2, linked: 1, dismissed: 0 })
  })

  it('стадия, признаки продажи, откуда', () => {
    expect(stageLine(obj().stageHints)).toBe('строится — 2')
    expect(stageLine({ underConstruction: 1, completed: 3, unknown: 0, quotes: [] })).toBe('строится — 1, сдан — 3')
    expect(stageLine(null)).toBe('в рекламе стадия не названа')
    expect(saleLine(obj().saleSignals)).toBe('Признаки приёма денег — в 2 из 3 публ. (за 120 дн. — 1)')
    expect(saleLine({ ads: 4, concerning: 0, recentConcerning: 0, types: {} })).toBe('Признаков приёма денег нет — 4 публ.')
    expect(saleLine({ ads: 0, concerning: 0, recentConcerning: 0, types: {} })).toBe('Публикаций с упоминанием нет')
    expect(signalTypesLine({ installment: 1, booking: 2, price: 0 })).toBe('бронь — 2 · рассрочка — 1')
    expect(sourcesLine(obj().sources)).toBe('сборщик — 2 · заявки жителей — 1')
  })

  it('решение: привязан или отклонён — с автором; новый — null', () => {
    expect(decisionText(obj())).toBeNull()
    expect(decisionText(obj({ status: 'linked', complexId: 'c3', complexName: 'Nova', decidedBy: AUTHOR }))).toBe(`Привязан к ЖК «Nova» — ${AUTHOR}`)
    expect(decisionText(obj({ status: 'dismissed', note: 'подборка', decidedBy: AUTHOR }))).toBe(`Отклонён: подборка — ${AUTHOR}`)
  })
})

describe('кандидаты 2ГИС', () => {
  it('точный дом — первым, затем «рекомендуется»; без повторов, удалённых и решённых не в пользу кандидата', () => {
    const exact = cand({ id: 'g2', precision: 'exact' })
    const rec = cand({ id: 'g3', recommended: true })
    const picks = geoPicks([
      check({ id: 2, candidates: [cand(), rec, exact] }),
      check({ id: 1, candidates: [cand()] }),
      check({ id: 3, purged: true, candidates: [cand({ id: 'g4' })] }),
      check({ id: 4, status: 'rejected', candidates: [cand({ id: 'g5' })] }),
      check({ id: 5, status: 'accepted', chosenId: 'g6', candidates: [cand({ id: 'g6' }), cand({ id: 'g7' })] }),
    ])
    expect(picks.map((p) => p.cand.id)).toEqual(['g2', 'g3', 'g1', 'g6'])
    expect(picks[2].check.id).toBe(2)
    expect(geoPicks(null)).toEqual([])
  })

  it('свежие сверки — впереди сохранённых, без повторов', () => {
    expect(mergeChecks([check({ id: 3 }), check({ id: 1 })], [check({ id: 1 }), check({ id: 2 })]).map((c) => c.id)).toEqual([3, 1, 2])
  })
})

describe('создать карточку ЖК', () => {
  it('форма: название и адрес из рекламы; кандидат — выбранный', () => {
    expect(createForm(obj(), 'g1')).toEqual({ name: 'Nova Park', address: 'Сатпаева 15', developerName: '', developerBin: '', candidateId: 'g1' })
    expect(createForm(obj({ addressHint: null })).address).toBe('')
  })

  it('проверка: название обязательно, БИН — 12 цифр', () => {
    expect(createProblem(createForm(obj()))).toBeNull()
    expect(createProblem({ ...createForm(obj()), name: ' x ' })).toBe('Укажите название ЖК')
    expect(createProblem({ ...createForm(obj()), developerBin: '12345' })).toBe('БИН застройщика — 12 цифр')
    expect(createProblem({ ...createForm(obj()), developerBin: '191040019288' })).toBeNull()
  })

  it('тело: пустые поля не отправляются, координаты — через кандидата', () => {
    expect(createBody({ name: ' Nova Park ', address: ' ', developerName: '', developerBin: '', candidateId: '' }, AUTHOR)).toEqual({ author: AUTHOR, name: 'Nova Park' })
    expect(createBody({ name: 'Nova', address: 'Сатпаева 15', developerName: 'ТОО «Нова»', developerBin: '191040019288', candidateId: 'g2' }, AUTHOR))
      .toEqual({ author: AUTHOR, name: 'Nova', address: 'Сатпаева 15', developerName: 'ТОО «Нова»', developerBin: '191040019288', candidateId: 'g2' })
  })
})

describe('«Это существующий ЖК»', () => {
  const complexes = [cx('c2', 'Орда', 'Кокжал Барака 19а'), cx('c1', 'Asyl Tas', 'Увалиева 8/3', 'd2'), cx('c3', 'Нова', 'Сатпаева 15')]
  const developers = [{ id: 'd2', name: 'ТОО «Асыл»', bin: '191040019288' } as Developer]
  it('пустой запрос — все по названию (русская сортировка: кириллица раньше латиницы); с запросом — найденные', () => {
    expect(pickComplexes('', complexes, developers).map((c) => c.id)).toEqual(['c3', 'c2', 'c1'])
    expect(pickComplexes('орда', complexes, developers).map((c) => c.id)).toEqual(['c2'])
    expect(pickComplexes('Сатпаева', complexes, developers).map((c) => c.id)).toEqual(['c3'])
    expect(pickComplexes('191040019288', complexes, developers).map((c) => c.id)).toEqual(['c1'])
    expect(pickComplexes('', complexes, developers, 2)).toHaveLength(2)
  })
})
