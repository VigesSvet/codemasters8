import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CollectionRun, Connector, ConnectorStatus, RunStatus } from '../data/types'
import {
  ADMIN_HEADER, ADMIN_KEY, NEED_ADMIN, NO_ADMIN_ROUTE, adminHeaders, checkAdmin, clearAdminToken, getAdminToken, isAdmin, onAdminChange,
  setAdminToken, signInAdmin, tokenProblem,
} from './admin'
import { ApiError, NO_ROUTE, UNREACHABLE, mediaSrc, mediaUrl, messageOf } from './api'
import {
  RUN_LABEL, RUN_TERMINAL, SCHEDULABLE, STATUS_LABEL, STATUS_TONE, accountState, blockReason, collectAll, collectAllToast, collectConnector,
  collectable, collectedLine, configSummary, effectiveStatus, finishedWithData, getConnectors, getRun, getRuns, getVersions, groupConnectors,
  intervalProblem, intervalText, isBusy, isTerminal, lastOkRow, nextRun, olxStart, parseLines, patchConnector, processingNote, retryLine,
  runLine, runSummary, runToast, runsSeen, runsToast, safeAuthorizeUrl, sourcesWord, splitMissing, statusText, testConnector, testOkLine,
  toneOf, when, workingCollectors,
} from './connectors'

const TOKEN = 't-admin-0123456789abcdef0123'

const STATUSES: ConnectorStatus[] = ['not_configured', 'unchecked', 'checking', 'queued', 'running', 'ready', 'working', 'partial', 'waiting',
  'off', 'error', 'expired', 'denied', 'access_check', 'rate_limited']
const RUNS: RunStatus[] = ['queued', 'running', 'succeeded', 'partial', 'failed', 'access_check', 'rate_limited', 'not_configured', 'cancelled']

const caps = { test: true, collect: true, toggle: true, configure: true, connect: false }
const conn = (x: Partial<Connector> = {}): Connector => ({
  code: 'instagram', kind: 'collector', group: 'collectors', platform: 'instagram', name: 'Instagram', via: 'Meta Graph API · Business Discovery',
  status: 'working', statusLabel: 'работает', enabled: true, configured: true, missing: [], secretRefs: [], config: {}, configFields: ['accounts'],
  options: [], facts: {}, counts: { items: 0, ads: 0, media: 0 }, capabilities: caps, paid: false, stopped: false, ...x,
})
const run = (x: Partial<CollectionRun> = {}): CollectionRun => ({
  id: 12, source: 'instagram', trigger: 'manual', status: 'succeeded', statusLabel: 'успешно', notes: [],
  counts: { seen: 24, new: 3, updated: 1, unchanged: 20, failed: 0, mediaSaved: 4, mediaFailed: 0 }, ...x,
})

/** Простой storage в памяти — как sessionStorage */
function memory(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init))
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => { m.set(k, String(v)) },
    removeItem: (k: string) => { m.delete(k) },
    raw: m,
  }
}
/** storage, который бросает на каждое обращение (приватное окно, запрет cookies) */
const broken = () => ({
  getItem: () => { throw new DOMException('denied', 'SecurityError') },
  setItem: () => { throw new DOMException('quota', 'QuotaExceededError') },
  removeItem: () => { throw new DOMException('denied', 'SecurityError') },
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

let box: ReturnType<typeof memory>
beforeEach(() => {
  box = memory()
  vi.stubGlobal('sessionStorage', box)
  clearAdminToken(null)
})
afterEach(() => {
  clearAdminToken(null)
  vi.unstubAllGlobals()
})

describe('статусы подключений (§10.4)', () => {
  it('цвет и подпись есть у каждого статуса; «работает частично» — жёлтый', () => {
    expect(Object.keys(STATUS_TONE).sort()).toEqual([...STATUSES].sort())
    expect(Object.keys(STATUS_LABEL).sort()).toEqual([...STATUSES].sort())
    for (const s of STATUSES) expect(['gray', 'blue', 'green', 'yellow', 'red']).toContain(STATUS_TONE[s])
    expect(STATUS_TONE.partial).toBe('yellow')
    expect(STATUS_LABEL.partial).toBe('работает частично')
    expect([STATUS_TONE.error, STATUS_TONE.access_check, STATUS_TONE.expired, STATUS_TONE.not_configured]).toEqual(['red', 'yellow', 'yellow', 'gray'])
    expect([STATUS_LABEL.access_check, STATUS_LABEL.expired, STATUS_LABEL.not_configured]).toEqual(['нужно ручное действие', 'истёк доступ', 'не настроен'])
  })

  it('без ключей на сервере — «не настроен», что бы ни пришло в status', () => {
    const c = conn({ configured: false, status: 'working', statusLabel: 'работает', missing: ['META_USER_ACCESS_TOKEN'] })
    expect(effectiveStatus(c)).toBe('not_configured')
    expect(statusText(c)).toBe('не настроен')
    expect(toneOf(c)).toBe('gray')
  })

  it('«заявка отправлена» и «ключ создан» — не «работает»', () => {
    const olx = conn({ code: 'olx', kind: 'oauth', group: 'limited', platform: 'olx', configured: false, status: 'not_configured', statusLabel: 'заявка отправлена' })
    expect(statusText(olx)).toBe('заявка отправлена')
    expect(statusText({ ...olx, statusLabel: '' })).toBe('заявка отправлена')
    // подключённый OLX ограничен: даже если сервер назовёт его «работает»
    expect(statusText({ ...olx, configured: true, status: 'ready', statusLabel: 'работает' })).toBe('подключено · ограничено')
    expect(statusText({ ...olx, configured: true, status: 'working', statusLabel: 'работает' })).toBe('подключено · ограничено')
    // ключ 2ГИС есть — геосервис «готов», а не «работает»
    expect(statusText(conn({ code: 'dgis', kind: 'geo', group: 'geo', status: 'working', statusLabel: 'работает' }))).toBe('готов')
    // «работает» при другом статусе — подпись по статусу
    expect(statusText(conn({ status: 'ready', statusLabel: 'работает' }))).toBe('готов к сбору')
    expect(statusText(conn({ code: 'anthropic', kind: 'analyzer', group: 'analyzer', status: 'off', statusLabel: '' }))).toBe('не используется')
    // анализатор Claude: «готов» после проверки, «анализирует публикации» после анализа, бюджет — «пауза: лимит»
    const ai = conn({ code: 'anthropic', kind: 'analyzer', group: 'analyzer', statusLabel: '' })
    expect(statusText({ ...ai, status: 'ready' })).toBe('готов')
    expect(statusText({ ...ai, status: 'working' })).toBe('анализирует публикации')
    expect(statusText({ ...ai, status: 'working', statusLabel: 'работает' })).toBe('анализирует публикации')
    expect(statusText({ ...ai, status: 'rate_limited' })).toBe('пауза: лимит')
    expect(statusText({ ...ai, configured: false, status: 'ready' })).toBe('не настроен')
  })

  it('сборщик со сбором — «работает»; приём — свои подписи', () => {
    expect(statusText(conn())).toBe('работает')
    expect(statusText(conn({ code: 'intake', kind: 'intake', group: 'intake', statusLabel: '' }))).toBe('принимает публикации')
    expect(statusText(conn({ code: 'telegram_bot', kind: 'intake', group: 'intake', statusLabel: 'принимает заявки' }))).toBe('принимает заявки')
  })

  it('незнакомый статус новой версии сервера — его подпись, серый цвет, без «работает»', () => {
    const c = conn({ status: 'paused' as ConnectorStatus, statusLabel: 'приостановлен' })
    expect([statusText(c), toneOf(c)]).toEqual(['приостановлен', 'gray'])
    expect(statusText({ ...c, statusLabel: 'работает' })).toBe('не проверен')
    // ключи прототипа объекта — не статусы
    expect(effectiveStatus(conn({ status: 'constructor' as ConnectorStatus }))).toBe('unchecked')
    expect(statusText(conn({ code: 'constructor', status: 'ready', statusLabel: '' }))).toBe('готов к сбору')
  })

  it('опрос — пока проверка, очередь или сбор', () => {
    expect(STATUSES.filter((s) => isBusy(conn({ status: s })))).toEqual(['checking', 'queued', 'running'])
    expect(isBusy(conn({ status: 'running', configured: false }))).toBe(false)
  })
})

describe('группы подключений', () => {
  const list = [
    conn({ code: 'olx', kind: 'oauth', group: 'limited' }),
    conn({ code: 'anthropic', kind: 'analyzer', group: 'analyzer' }),
    conn({ code: 'instagram' }),
    conn({ code: 'dgis', kind: 'geo', group: 'geo' }),
    conn({ code: 'telegram' }),
    conn({ code: 'intake', kind: 'intake', group: 'intake' }),
  ]

  it('сборщики → приём → геосервис → анализатор → ограниченно; внутри — порядок сервера', () => {
    const g = groupConnectors(list)
    expect(g.map((s) => s.title)).toEqual(['Сборщики публикаций', 'Приём заявок и публикаций', 'Геосервис', 'Анализатор', 'Ограниченно'])
    expect(g[0].items.map((c) => c.code)).toEqual(['instagram', 'telegram'])
  })

  it('без group — по виду: oauth → «Ограниченно»; пустые группы не выводятся', () => {
    const g = groupConnectors([conn({ code: 'olx', kind: 'oauth', group: undefined as unknown as Connector['group'] })])
    expect(g).toHaveLength(1)
    expect(g[0].group).toBe('limited')
    expect(groupConnectors([])).toEqual([])
  })
})

describe('запуски сбора (§10.5)', () => {
  it('конечные — все, кроме «в очереди» и «выполняется»', () => {
    expect(RUNS.filter((s) => !isTerminal({ status: s }))).toEqual(['queued', 'running'])
    expect(RUN_TERMINAL.size).toBe(7)
    expect(Object.keys(RUN_LABEL).sort()).toEqual([...RUNS].sort())
  })

  it('сводка счётчиков', () => {
    expect(runSummary(run())).toBe('новых 3 · изменено 1 · без изменений 20 · ошибок 0')
    expect(runSummary({ counts: {} as CollectionRun['counts'] })).toBe('новых 0 · изменено 0 · без изменений 0 · ошибок 0')
  })

  it('строка последнего запуска: проверка без счётчиков, платный — со стоимостью', () => {
    expect(runLine(run())).toBe('№12 · успешно · новых 3 · изменено 1 · без изменений 20 · ошибок 0')
    expect(runLine(run({ trigger: 'test', statusLabel: '' }))).toBe('№12 · успешно · проверка подключения')
    expect(runLine(run({ costUsd: 0.1234 }))).toMatch(/ · \$0\.12$/)
    expect(runLine(run({ costUsd: 0.001 }))).toMatch(/ · <\$0\.01$/)
  })

  it('повтор и тост о новых данных', () => {
    expect(retryLine(run({ retryAt: '2026-10-16T10:30:00Z' }), () => '15:30')).toBe('повтор после 15:30')
    expect(retryLine(run())).toBeNull()
    expect(runToast('Instagram', run())).toBe('Сбор «Instagram»: новых 3, изменено 1 — событие в журнале')
    expect(runToast('Instagram', run({ counts: { ...run().counts, new: 0, updated: 0 } }))).toBeNull()
    expect(runToast('Instagram', run({ status: 'running' }))).toBeNull()
  })
})

describe('счётчики и склонения', () => {
  it('источник / источника / источников', () => {
    expect([1, 2, 5, 11, 21, 22, 25].map((n) => `${n} ${sourcesWord(n)}`))
      .toEqual(['1 источник', '2 источника', '5 источников', '11 источников', '21 источник', '22 источника', '25 источников'])
  })

  it('«Собрано»', () => {
    expect(collectedLine({ items: 12, ads: 10, media: 7 })).toBe('12 материалов · 10 публикаций · 7 файлов')
    expect(collectedLine({ items: 1, ads: 1, media: 1 })).toBe('1 материал · 1 публикация · 1 файл')
    expect(collectedLine({ items: 3, ads: 2, media: 4 })).toBe('3 материала · 2 публикации · 4 файла')
  })

  it('тост «Обновить всё»: имена, причины пропуска', () => {
    const names: Record<string, string> = { krisha: 'Krisha.kz', tiktok: 'TikTok' }
    const res = { queued: [{ code: 'instagram', runId: 1 }, { code: 'telegram', runId: 2 }], skipped: [{ code: 'krisha', reason: 'выключен' }, { code: 'tiktok', reason: 'не настроен' }] }
    expect(collectAllToast(res, (c) => names[c] ?? c)).toBe('Сбор поставлен в очередь: 2 источника, пропущено: Krisha.kz — выключен, TikTok — не настроен')
    expect(collectAllToast({ queued: [{ code: 'instagram', runId: 1 }], skipped: [] })).toBe('Сбор поставлен в очередь: 1 источник')
    expect(collectAllToast({ queued: [], skipped: [{ code: 'krisha', reason: 'нужно ручное действие' }] })).toBe('Сбор не поставлен в очередь — пропущено: krisha — нужно ручное действие')
  })
})

describe('кнопки и «Обновить всё»', () => {
  it('без режима администратора — всё неактивно с пояснением', () => {
    expect(blockReason(conn(), 'test', false)).toBe('Нужен режим администратора')
    expect(blockReason(conn(), 'collect', true)).toBeNull()
  })

  it('не настроено на сервере — имена переменных; OLX — «заявка отправлена»', () => {
    expect(blockReason(conn({ configured: false, missing: ['META_USER_ACCESS_TOKEN'] }), 'test', true))
      .toBe('Сначала настройте подключение на сервере: META_USER_ACCESS_TOKEN')
    expect(blockReason(conn({ code: 'olx', configured: false, capabilities: { ...caps, connect: true } }), 'connect', true)).toMatch(/^OLX не настроен: заявка отправлена/)
    expect(blockReason(conn({ capabilities: { ...caps, toggle: false } }), 'toggle', true)).toBe('У этого подключения нет сбора по расписанию')
  })

  it('в очередь — только включённые, проверенные, не остановленные и не на паузе сборщики', () => {
    const now = new Date('2026-10-16T10:00:00Z')
    const list = [
      conn({ code: 'a' }),
      conn({ code: 'b', enabled: false }),
      conn({ code: 'c', status: 'unchecked' }),
      conn({ code: 'd', status: 'error', stopped: true }),
      conn({ code: 'e', status: 'rate_limited', nextRunAt: '2026-10-16T11:00:00Z' }),
      conn({ code: 'f', status: 'rate_limited', nextRunAt: '2026-10-16T09:00:00Z' }),
      conn({ code: 'g', status: 'access_check' }),
      conn({ code: 'h', kind: 'intake', group: 'intake' }),
      conn({ code: 'i', configured: false }),
    ]
    expect(collectable(list, now).map((c) => c.code)).toEqual(['a', 'f'])
    expect(workingCollectors(list).map((c) => c.code)).toEqual(['a'])
  })
})

describe('строки карточки подключения', () => {
  const now = new Date('2026-10-16T10:00:00Z')
  const later = '2026-10-16T14:00:00Z'
  const earlier = '2026-10-16T08:00:00Z'

  it('«Следующий запуск» — только если планировщик его поставит (как schedule_due)', () => {
    expect([...SCHEDULABLE].sort()).toEqual(['error', 'partial', 'rate_limited', 'ready', 'working'])
    expect(nextRun(conn({ nextRunAt: later }), now)).toEqual({ text: when(later), at: later })
    expect(nextRun(conn({ status: 'error', nextRunAt: later }), now)).toEqual({ text: when(later), at: later })
    expect(nextRun(conn({ status: 'rate_limited', nextRunAt: later }), now)?.at).toBe(later)
    // срок прошёл или его нет — сбор будет при ближайшем проходе планировщика, прошедшее время не показываем
    expect(nextRun(conn({ nextRunAt: earlier }), now)).toEqual({ text: 'скоро — при ближайшем проходе планировщика' })
    expect(nextRun(conn({ status: 'ready', nextRunAt: null }), now)?.at).toBeUndefined()
  })

  it('остановленный, ждущий ручного действия, без ключей — «не запланирован» без времени', () => {
    // платный сбор после ошибки: сервер оставил next_run_at в будущем, но планировщик пропускает stopped
    expect(nextRun(conn({ code: 'tiktok', status: 'error', stopped: true, paid: true, nextRunAt: later }), now))
      .toEqual({ text: 'не запланирован — сбор остановлен после ошибки' })
    for (const status of ['expired', 'denied', 'access_check'] as const) {
      expect(nextRun(conn({ status, nextRunAt: earlier }), now)).toEqual({ text: 'не запланирован — ждёт ручного действия' })
    }
    expect(nextRun(conn({ status: 'unchecked', nextRunAt: later }), now)?.text).toBe('не запланирован — сначала «Проверить подключение»')
    expect(nextRun(conn({ configured: false, missing: ['APIFY_TOKEN'], nextRunAt: later }), now)?.text).toMatch(/^не запланирован — подключение не настроено/)
  })

  it('выключенный, идущий сбор и не сборщик — строки нет', () => {
    expect(nextRun(conn({ enabled: false, nextRunAt: later }), now)).toBeNull()
    for (const status of ['checking', 'queued', 'running'] as const) expect(nextRun(conn({ status, nextRunAt: earlier }), now)).toBeNull()
    expect(nextRun(conn({ code: 'intake', kind: 'intake', group: 'intake', nextRunAt: later }), now)).toBeNull()
    expect(nextRun(conn({ code: 'dgis', kind: 'geo', group: 'geo', status: 'ready', nextRunAt: later }), now)).toBeNull()
  })

  it('«Последний успех» — у сборщика; у 2ГИС и OLX — «Последняя проверка»; у приёма — только если есть; у анализатора — «Последний анализ»', () => {
    expect(lastOkRow(conn({ lastSuccessAt: null }))).toEqual({ label: 'Последний успех', at: null })
    expect(lastOkRow(conn({ lastSuccessAt: earlier }))).toEqual({ label: 'Последний успех', at: earlier })
    expect(lastOkRow(conn({ kind: 'geo', lastSuccessAt: null, checkedAt: earlier }))).toEqual({ label: 'Последняя проверка', at: earlier })
    expect(lastOkRow(conn({ kind: 'oauth', checkedAt: earlier }))).toEqual({ label: 'Последняя проверка', at: earlier })
    expect(lastOkRow(conn({ kind: 'geo', checkedAt: null }))).toBeNull()
    expect(lastOkRow(conn({ kind: 'intake', lastSuccessAt: null }))).toBeNull()
    expect(lastOkRow(conn({ kind: 'intake', lastSuccessAt: earlier }))?.at).toBe(earlier)
    expect(lastOkRow(conn({ kind: 'analyzer', lastSuccessAt: earlier }))).toEqual({ label: 'Последний анализ', at: earlier })
    expect(lastOkRow(conn({ kind: 'analyzer', lastSuccessAt: null, checkedAt: earlier }))).toEqual({ label: 'Последняя проверка', at: earlier })
    expect(lastOkRow(conn({ kind: 'analyzer', lastSuccessAt: null, checkedAt: null }))).toBeNull()
  })

  it('успешная проверка: «можно включать сбор» — только у сборщика', () => {
    expect(testOkLine(conn({ enabled: false }))).toBe('Подключение проверено — можно включать сбор.')
    expect(testOkLine(conn({ enabled: true }))).toBe('Подключение проверено — сбор по расписанию включён.')
    expect(testOkLine(conn({ kind: 'geo', enabled: false }))).toBe('Ключ 2ГИС работает — сверка адреса доступна в карточке ЖК.')
    expect(testOkLine(conn({ kind: 'oauth', enabled: false }))).toBe('Аккаунт OLX доступен — только ресурсы своего аккаунта.')
    expect(testOkLine(conn({ kind: 'intake' }))).toBe('Подключение проверено.')
    expect(testOkLine(conn({ kind: 'analyzer' }))).toBe('Ключ Anthropic принят, модель доступна — новые публикации анализирует Claude (гипотезы для аналитика).')
    for (const kind of ['geo', 'oauth', 'intake', 'analyzer'] as const) expect(testOkLine(conn({ kind }))).not.toMatch(/сбор/)
  })

  it('OLX: истёк доступ, нет доступа, ошибка — «Подключить заново» рядом с «Отключить аккаунт»', () => {
    const olx = (x: Partial<Connector>) => conn({ code: 'olx', kind: 'oauth', group: 'limited', ...x })
    expect(accountState(olx({ configured: false, status: 'not_configured' }))).toEqual({ linked: false, relink: false })
    expect(accountState(olx({ status: 'unchecked' }))).toEqual({ linked: false, relink: false })
    for (const status of ['ready', 'working', 'checking'] as const) expect(accountState(olx({ status }))).toEqual({ linked: true, relink: false })
    for (const status of ['expired', 'denied', 'error'] as const) expect(accountState(olx({ status }))).toEqual({ linked: true, relink: true })
  })

  it('Telegram без сессии — не «нет ключа в .env», а вход на сервере', () => {
    expect(splitMissing(['TG_SESSION_PATH'])).toEqual({ env: [], session: true })
    expect(splitMissing(['TG_API_ID', 'TG_SESSION_PATH'])).toEqual({ env: ['TG_API_ID'], session: true })
    expect(splitMissing(undefined)).toEqual({ env: [], session: false })
    const tg = (missing: string[]) => conn({ code: 'telegram', configured: false, missing })
    expect(blockReason(tg(['TG_SESSION_PATH']), 'test', true)).toBe('Сначала выполните вход в Telegram на сервере — авторизованной сессии нет')
    expect(blockReason(tg(['TG_API_HASH', 'TG_SESSION_PATH']), 'collect', true))
      .toBe('Сначала настройте подключение на сервере: TG_API_HASH и выполните вход в Telegram')
    expect(blockReason(tg([]), 'test', true)).toBe('Сначала настройте подключение на сервере')
  })
})

describe('завершённые сборы: перечитать ленту один раз', () => {
  const ig = (lastRun: CollectionRun | null) => conn({ code: 'instagram', name: 'Instagram', lastRun })
  const tg = (lastRun: CollectionRun | null) => conn({ code: 'telegram', name: 'Telegram', lastRun })

  it('первая загрузка прежние запуски не считает', () => {
    expect(finishedWithData(null, [ig(run())])).toEqual([])
  })

  it('запуск завершился с новыми данными после снимка — один раз', () => {
    const before = runsSeen([ig(run({ id: 12, status: 'running' })), tg(null)])
    const list = [ig(run({ id: 12 })), tg(run({ id: 13, source: 'telegram' }))]
    expect(finishedWithData(before, list).map((x) => [x.name, x.run.id])).toEqual([['Instagram', 12], ['Telegram', 13]])
    // следующий опрос — те же завершённые запуски: уже учтены
    expect(finishedWithData(runsSeen(list), list)).toEqual([])
  })

  it('новый запуск между опросами (прошёл целиком) — тоже; без новых данных и незавершённый — нет', () => {
    const before = runsSeen([ig(run({ id: 10 }))])
    expect(finishedWithData(before, [ig(run({ id: 11 }))]).map((x) => x.run.id)).toEqual([11])
    expect(finishedWithData(before, [ig(run({ id: 11, counts: { ...run().counts, new: 0, updated: 0 } }))])).toEqual([])
    expect(finishedWithData(before, [ig(run({ id: 11, status: 'running' }))])).toEqual([])
    expect(finishedWithData(before, [ig(run({ id: 11, trigger: 'test', counts: { ...run().counts, new: 0, updated: 0 } }))])).toEqual([])
  })

  it('тост: один сбор — как у панели запуска; несколько — имена и сумма', () => {
    expect(runsToast([{ name: 'Instagram', run: run() }])).toBe('Сбор «Instagram»: новых 3, изменено 1 — событие в журнале')
    expect(runsToast([{ name: 'Instagram', run: run() }, { name: 'Telegram', run: run({ id: 13, counts: { ...run().counts, new: 2, updated: 0 } }) }]))
      .toBe('Сбор «Instagram», «Telegram»: новых 5, изменено 1 — события в журнале')
    expect(runsToast([{ name: 'Instagram', run: run({ counts: { ...run().counts, new: 0, updated: 0 } }) }])).toBeNull()
    expect(runsToast([])).toBeNull()
  })
})

describe('настройки подключения', () => {
  it('по одному в строке: без пустых, пробелов и повторов', () => {
    expect(parseLines(' @a \n\n@b\r\n@a\n  ')).toEqual(['@a', '@b'])
  })

  it('«Что собираем» и период', () => {
    expect(configSummary(conn({ config: { accounts: ['@a', '@b', '@c', '@d', '@e'] } }))).toBe('аккаунты: @a, @b, @c и ещё 2')
    expect(configSummary(conn())).toBe('список пуст — задайте его в «Настроить»')
    expect(configSummary(conn({ configFields: [] }))).toBeNull()
    expect([30, 60, 90, 360, 1440, 10080, null].map(intervalText)).toEqual(['раз в 30 мин', 'раз в час', 'раз в 1 ч 30 мин', 'раз в 6 ч', 'раз в сутки', 'раз в 7 сут', '—'])
    expect(intervalProblem(10, [30, 1440])).toBe('Период: от 30 до 1440 мин')
    expect(intervalProblem(60, [30, 1440])).toBeNull()
    expect(intervalProblem(1.5, [1, 10])).toBe('Период: целое число минут')
  })

  it('ошибки 422 по полям настроек — по-русски, с границами', () => {
    expect(messageOf({ detail: [{ type: 'greater_than_equal', loc: ['body', 'intervalMin'], msg: 'Input should be greater than or equal to 5', ctx: { ge: 5 } }] }, 'x'))
      .toBe('период: не меньше 5')
    expect(messageOf({ detail: [{ type: 'value_error', loc: ['body', 'config', 'accounts'], msg: 'Value error, Неверный аккаунт: ab cd' }] }, 'x'))
      .toBe('аккаунты: Неверный аккаунт: ab cd')
  })
})

describe('анализ собранной публикации', () => {
  it('очередь, этап, ошибка; готово — без пояснения', () => {
    expect(processingNote({ status: 'queued', version: 1 })).toBe('Анализ публикации в очереди')
    expect(processingNote({ status: 'running', version: 1, stage: 'ocr' })).toBe('Идёт анализ: распознавание текста')
    expect(processingNote({ status: 'running', version: 1, stage: 'llm' })).toBe('Идёт анализ: анализ ИИ (Claude)')
    expect(processingNote({ status: 'running', version: 1 })).toBe('Идёт анализ публикации')
    expect(processingNote({ status: 'failed', version: 2, stage: 'media', error: 'Файл недоступен (404)' })).toBe('Анализ не выполнен: Файл недоступен (404) — проверьте вручную')
    expect(processingNote({ status: 'failed', version: 2 })).toBe('Анализ не выполнен — проверьте вручную')
    expect(processingNote({ status: 'done', version: 2, analyzedVersion: 2 })).toBeNull()
    expect(processingNote(undefined)).toBeNull()
  })
})

describe('mediaSrc: адреса файлов', () => {
  it('относительный API (/api, прокси vite)', () => {
    expect(mediaSrc('/api/media/0123456789abcdef0123456789abcdef')).toBe('/api/media/0123456789abcdef0123456789abcdef')
    expect(mediaSrc('/img/site-a.jpg')).toBe('/img/site-a.jpg')
    expect(mediaSrc(null)).toBeUndefined()
    expect(mediaSrc('')).toBeUndefined()
  })

  it('абсолютный VITE_API_URL — серверные файлы идут на API, остальное не трогаем', () => {
    const base = 'http://localhost:8000/api/'
    expect(mediaUrl('/api/media/0123456789abcdef0123456789abcdef', base)).toBe('http://localhost:8000/api/media/0123456789abcdef0123456789abcdef')
    expect(mediaUrl('/img/site-a.jpg', base)).toBe('/img/site-a.jpg')
    expect(mediaUrl('https://cdn.example/x.jpg', base)).toBe('https://cdn.example/x.jpg')
  })
})

describe('режим администратора: токен только в sessionStorage этой вкладки', () => {
  it('хранит под saq.admin, отдаёт заголовок X-Admin-Token, выход удаляет', () => {
    expect(adminHeaders()).toEqual({})
    expect(setAdminToken(`  ${TOKEN}\n`)).toBe(true)
    expect(box.raw.get(ADMIN_KEY)).toBe(TOKEN)
    expect(adminHeaders()).toEqual({ [ADMIN_HEADER]: TOKEN })
    expect(isAdmin()).toBe(true)
    clearAdminToken()
    expect([box.raw.has(ADMIN_KEY), getAdminToken(), isAdmin()]).toEqual([false, null, false])
  })

  it('sessionStorage бросает — токен только в памяти страницы, без исключения', () => {
    const b = broken()
    expect(setAdminToken(TOKEN, b)).toBe(false)
    expect(getAdminToken(b)).toBe(TOKEN)
    clearAdminToken(b)
    expect(getAdminToken(b)).toBeNull()
  })

  it('не пишет в localStorage', () => {
    const local = memory()
    vi.stubGlobal('localStorage', local)
    setAdminToken(TOKEN)
    expect(local.raw.size).toBe(0)
  })

  it('слушатели получают только флаг', () => {
    const got: unknown[] = []
    const off = onAdminChange((x) => got.push(x))
    setAdminToken(TOKEN)
    clearAdminToken()
    clearAdminToken()
    off()
    setAdminToken(TOKEN)
    expect(got).toEqual([true, false])
  })

  it('пустой токен и не-ASCII — отказ до запроса', async () => {
    expect(tokenProblem('  ')).toBe('Введите токен администратора')
    expect(tokenProblem('токен администратора 123456')).toMatch(/недопустимые символы/)
    expect(tokenProblem(TOKEN)).toBeNull()
    const f = reply(200, { ok: true })
    vi.stubGlobal('fetch', f)
    await failure(checkAdmin('ключ'))
    expect(f).not.toHaveBeenCalled()
  })

  it('checkAdmin: заголовок только в /api/admin/check, токен не сохраняется; signInAdmin — сохраняет', async () => {
    const f = reply(200, { ok: true })
    vi.stubGlobal('fetch', f)
    await checkAdmin(TOKEN)
    expect(sent(f)).toMatchObject({ url: '/api/admin/check', method: 'GET', headers: { [ADMIN_HEADER]: TOKEN } })
    expect(getAdminToken()).toBeNull()
    await signInAdmin(TOKEN)
    expect(getAdminToken()).toBe(TOKEN)
  })

  it('checkAdmin: 403 и 503 сервера — его текст (503 API — не «нет связи»); старый сервер — «обновите бэкенд»', async () => {
    vi.stubGlobal('fetch', reply(403, { detail: 'Неверный токен администратора' }))
    expect((await failure(checkAdmin(TOKEN))).message).toBe('Неверный токен администратора')
    vi.stubGlobal('fetch', reply(503, { detail: 'Администрирование подключений не настроено: задайте SAQ_ADMIN_TOKEN на сервере' }))
    const e = await failure(checkAdmin(TOKEN))
    expect([e.status, e.message]).toEqual([503, 'Администрирование подключений не настроено: задайте SAQ_ADMIN_TOKEN на сервере'])
    vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
    expect((await failure(checkAdmin(TOKEN))).message).toBe(NO_ADMIN_ROUTE)
    vi.stubGlobal('fetch', reply(503, '', 'text/plain'))
    expect((await failure(checkAdmin(TOKEN))).message).toBe(UNREACHABLE)
  })
})

describe('запросы подключений', () => {
  const list = { connectors: [conn()], worker: { alive: true }, adminConfigured: true }

  it('getConnectors: данные; заголовок администратора не отправляется', async () => {
    setAdminToken(TOKEN)
    const f = reply(200, list)
    vi.stubGlobal('fetch', f)
    expect(await getConnectors()).toEqual(list)
    expect(sent(f)).toMatchObject({ url: '/api/sources/connectors', method: 'GET' })
    expect(sent(f).headers[ADMIN_HEADER]).toBeUndefined()
  })

  it('getConnectors: нет связи, сервер старой версии, HTML вместо JSON — null; 500 — ошибка', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('NetworkError') }))
    expect(await getConnectors()).toBeNull()
    vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
    expect(await getConnectors()).toBeNull()
    vi.stubGlobal('fetch', reply(200, '<!doctype html><html></html>', 'text/html'))
    expect(await getConnectors()).toBeNull()
    vi.stubGlobal('fetch', reply(500, { detail: 'Внутренняя ошибка сервера. Подробности — в журнале API.' }))
    expect((await failure(getConnectors())).status).toBe(500)
  })

  it('getRuns и getVersions: null при недоступности; 404 «Публикация не найдена» — ошибка', async () => {
    const f = reply(200, [])
    vi.stubGlobal('fetch', f)
    expect(await getRuns('instagram', 500)).toEqual([])
    expect(sent(f).url).toBe('/api/collection-runs?limit=100&source=instagram')
    vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
    expect(await getVersions('a1')).toBeNull()
    vi.stubGlobal('fetch', reply(404, { detail: 'Публикация не найдена' }))
    expect((await failure(getVersions('a1'))).message).toBe('Публикация не найдена')
  })

  it('getRun: старый сервер — понятный текст; 404 запуска — текст сервера', async () => {
    vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
    expect((await failure(getRun(5))).message).toBe(NO_ROUTE)
    vi.stubGlobal('fetch', reply(404, { detail: 'Запуск не найден' }))
    expect((await failure(getRun(5))).message).toBe('Запуск не найден')
  })

  it('действия без режима администратора — 401 без запроса к серверу', async () => {
    const f = reply(202, run())
    vi.stubGlobal('fetch', f)
    const e = await failure(testConnector('instagram', 'Омарова Ж.'))
    expect([e.status, e.message]).toEqual([401, NEED_ADMIN])
    await failure(collectAll('Омарова Ж.'))
    await failure(patchConnector('instagram', { author: 'Омарова Ж.', enabled: true }))
    expect(f).not.toHaveBeenCalled()
  })

  it('действия администратора: заголовок X-Admin-Token, автор в теле', async () => {
    setAdminToken(TOKEN)
    const f = reply(202, run({ status: 'queued' }))
    vi.stubGlobal('fetch', f)
    await testConnector('instagram', 'Омарова Ж.')
    await collectConnector('tiktok', 'Омарова Ж.')
    await patchConnector('instagram', { author: 'Омарова Ж.', enabled: true, intervalMin: 60, config: { accounts: ['@a'] } })
    expect(sent(f, 0)).toMatchObject({ url: '/api/sources/instagram/test', method: 'POST', headers: { [ADMIN_HEADER]: TOKEN }, body: { author: 'Омарова Ж.' } })
    expect(sent(f, 1)).toMatchObject({ url: '/api/sources/tiktok/collect', method: 'POST' })
    expect(sent(f, 2)).toMatchObject({ url: '/api/sources/instagram', method: 'PATCH', headers: { [ADMIN_HEADER]: TOKEN, 'Content-Type': 'application/json' },
      body: { author: 'Омарова Ж.', enabled: true, intervalMin: 60, config: { accounts: ['@a'] } } })
  })

  it('403 сервера — токен забывается, режим администратора выключается; 409 — текст сервера, токен остаётся', async () => {
    setAdminToken(TOKEN)
    vi.stubGlobal('fetch', reply(409, { detail: 'Сначала настройте подключение на сервере: APIFY_TOKEN' }))
    expect((await failure(patchConnector('tiktok', { author: 'Омарова Ж.', enabled: true }))).message).toBe('Сначала настройте подключение на сервере: APIFY_TOKEN')
    expect(isAdmin()).toBe(true)
    const got: boolean[] = []
    const off = onAdminChange((x) => got.push(x))
    vi.stubGlobal('fetch', reply(403, { detail: 'Неверный токен администратора' }))
    expect((await failure(collectAll('Омарова Ж.'))).message).toBe('Неверный токен администратора')
    off()
    expect([isAdmin(), got]).toEqual([false, [false]])
  })

  it('OLX: переход только на https olx.kz', async () => {
    expect(safeAuthorizeUrl('https://www.olx.kz/oauth/authorize/?client_id=1&state=x')).toBe('https://www.olx.kz/oauth/authorize/?client_id=1&state=x')
    for (const bad of ['http://www.olx.kz/oauth/authorize/', 'https://olx.kz.evil.example/', 'javascript:alert(1)', 'https://u:p@www.olx.kz/', 42, null])
      expect(safeAuthorizeUrl(bad)).toBeNull()
    setAdminToken(TOKEN)
    vi.stubGlobal('fetch', reply(200, { authorizeUrl: 'https://www.olx.kz/oauth/authorize/?state=s', expiresAt: '2026-10-16T10:10:00Z' }))
    expect((await olxStart('Омарова Ж.')).authorizeUrl).toBe('https://www.olx.kz/oauth/authorize/?state=s')
    vi.stubGlobal('fetch', reply(200, { authorizeUrl: 'https://evil.example/' }))
    expect((await failure(olxStart('Омарова Ж.'))).message).toMatch(/неожиданный адрес авторизации OLX/)
  })
})
