import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { adKey } from '../components/ui'
import * as demo from '../data/demo'
import type { Ad, AdScore, AdStatus, Analyst, Appeal, AuditEvent, Complex, Developer, RiskResult, Signal, SignalSummary } from '../data/types'
import { API, ApiError, apiGet, apiPatch, apiPost, onReach } from './api'
import { auditPath, linkLine, statusLine, type AuditScope, type RequestTopic } from './audit'
import { scoreAd, scoreComplex } from './risk'
import { onSessionChange, readSession, writeSession, type User } from './session'

export type Mode = 'loading' | 'api' | 'demo'
export type { AuditScope, User }

/** Последняя неуспешная операция: что не получилось (message), почему (detail), status 0 — нет связи */
export interface ApiErrorInfo { message: string; at: string; detail?: string; status?: number }

interface Store {
  /** api — данные с бэкенда; demo — встроенный набор из shared/seed_data.json */
  mode: Mode
  /** Ошибка последней операции — баннер под шапкой; «Скрыть» убирает только его */
  apiError: ApiErrorInfo | null
  clearError: () => void
  /** Последний запрос не дошёл до сервера (сеть, 502–504 прокси); любой ответ сервера снимает флаг — красный индикатор */
  offline: boolean
  /** Проверить /api/health и перечитать данные; при успехе — mode 'api' и без ошибки */
  reconnect: () => Promise<void>
  /** ISO времени последней успешной загрузки или обновления с сервера */
  lastSync: string | null
  /** Версия серверных данных: растёт после каждого сохранённого изменения (оно пишет журнал) и после восстановления
   *  связи. Страницы добавляют её в зависимости своих загрузок (журнал, сигнал) — и перечитывают их */
  revision: number
  ads: Ad[]
  complexes: Complex[]
  developers: Developer[]
  appeals: Appeal[]
  /** Вошедший сотрудник (демо-вход); автор всех действий */
  user: User | null
  /** Сотрудник сам вышел (здесь или в соседней вкладке): вход — без возврата на его страницу */
  signedOut: boolean
  /** Кого можно выбрать при входе: /api/analysts, в demo — seed.analysts */
  analysts: Analyst[]
  login: (u: User) => void
  logout: () => void
  /** Риск ЖК: с бэкенда (/api/risk), а без него — локально той же моделью */
  riskOf: (c: Complex) => RiskResult
  /** Оценка публикации локально; связанный ЖК берётся из complexes */
  adScoreOf: (a: Ad) => AdScore
  /** Перечитать риск; при ошибке — локальный расчёт и баннер (не отклоняется) */
  refreshRisk: () => Promise<void>
  /** Смена статуса от имени user.name. false — не сохранено: изменение откатано, ошибка в apiError */
  setAdStatus: (id: string, s: AdStatus, opts?: { assignee?: string; comment?: string }) => Promise<boolean>
  /** Новая публикация. false — не сохранена: убрана из ленты, ошибка в apiError */
  addAd: (a: Ad) => Promise<boolean>
  /** Привязка публикации к ЖК аналитиком (null — снять): уверенность 1 / 0, событие в журнале.
   *  false — не сохранено: привязка откатана, ошибка в apiError */
  linkAd: (id: string, complexId: string | null) => Promise<boolean>
  /** Журнал с сервера (по возрастанию времени); в demo — ApiError(0, …) */
  getAudit: (scope: AuditScope) => Promise<AuditEvent[]>
  /** Комментарий к публикации; в demo — только в журнал этой сессии */
  addComment: (adId: string, text: string) => Promise<AuditEvent>
  /** Внутренний запрос сведений по ЖК — запись в журнале объекта, во внешние органы не уходит; только с бэкендом */
  requestInfo: (complexId: string, topics: RequestTopic[], comment?: string) => Promise<AuditEvent>
  /** demo: действия этой сессии — на сервере не сохраняются */
  sessionLog: AuditEvent[]
  // модуль 05 — только с бэкендом; в demo методы отклоняются с ApiError(0, …)
  signals: SignalSummary[]
  /** Перечитать реестр сигналов; ошибка — в apiError (не отклоняется) */
  refreshSignals: () => Promise<void>
  /** author = user.name; при 409 (уже есть новый сигнал) — ApiError.data.signalId */
  createSignal: (complexId: string) => Promise<Signal>
  getSignal: (id: number) => Promise<Signal>
  decideSignal: (id: number, action: 'confirm' | 'return', comment: string) => Promise<Signal>
  signalPdfUrl: (id: number) => string
  toast: string | null
  notify: (title: string) => void
}

const Ctx = createContext<Store | null>(null)

const NO_SIGNALS = 'Сигналы сохраняются на сервере — запустите бэкенд (./dev.sh)'
const NO_AUDIT = 'Журнал хранится на сервере — в демо-режиме без бэкенда он недоступен (запустите ./dev.sh)'
const NO_USER = 'Войдите, чтобы действовать от своего имени'

type RiskRow = RiskResult & { complexId: string }
const riskMap = (rows: RiskRow[]) => new Map<string, RiskResult>(rows.map(({ complexId, ...r }) => [complexId, r]))
const summaryOf = ({ id, code, complexId, complexName, status, score, zone, needsCheck, hits, createdAt, createdBy, decidedAt, decidedBy }: Signal): SignalSummary =>
  ({ id, code, complexId, complexName, status, score, zone, needsCheck, hits, createdAt, createdBy, decidedAt, decidedBy })
const stamp = () => new Date().toISOString()
const asApiError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(-1, e instanceof Error ? e.message : String(e)))
const enc = encodeURIComponent

/** Все данные с бэкенда одним заходом; ЖК — обязательная часть, остальное может не загрузиться */
const fetchAll = () => Promise.allSettled([
  apiGet<Complex[]>('/complexes'), apiGet<Ad[]>('/ads'), apiGet<Developer[]>('/developers'),
  apiGet<Appeal[]>('/appeals'), apiGet<RiskRow[]>('/risk'), apiGet<SignalSummary[]>('/signals'), apiGet<Analyst[]>('/analysts'),
] as const)
type Fetched = Awaited<ReturnType<typeof fetchAll>>

/** Оценка публикаций с кэшем по объекту (объявления в сторе неизменяемы) — на один набор ЖК */
function adScorer(complexById: Map<string, Complex>) {
  const cache = new WeakMap<Ad, AdScore>()
  return (a: Ad) => {
    let s = cache.get(a)
    if (!s) cache.set(a, (s = scoreAd(a, a.complexId ? complexById.get(a.complexId) : undefined)))
    return s
  }
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>('loading')
  const [ads, setAds] = useState<Ad[]>(demo.ads)
  const [complexes, setComplexes] = useState<Complex[]>(demo.complexes)
  const [developers, setDevelopers] = useState<Developer[]>(demo.developers)
  const [appeals, setAppeals] = useState<Appeal[]>(demo.appeals)
  const [analysts, setAnalysts] = useState<Analyst[]>(demo.analysts)
  const [user, setUser] = useState<User | null>(() => readSession())
  const [signedOut, setSignedOut] = useState(false)
  const [apiRisk, setApiRisk] = useState<Map<string, RiskResult>>(() => new Map())
  const [signals, setSignals] = useState<SignalSummary[]>([])
  const [apiError, setApiError] = useState<ApiErrorInfo | null>(null)
  const [lastSync, setLastSync] = useState<string | null>(null)
  const [offline, setOffline] = useState(false)
  const [revision, setRevision] = useState(0)
  const [sessionLog, setSessionLog] = useState<AuditEvent[]>([])
  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  // Мутации, начатые до конца начальной загрузки: её ответ затрёт их результат — после неё перечитываем /ads и /risk
  const booted = useRef(false)
  const early = useRef<Promise<unknown>[]>([])
  const localId = useRef(0)

  /** Ошибка операции — в баннер; status 0 — нет связи с сервером */
  const fail = (message: string, e: unknown) => {
    const err = asApiError(e)
    setApiError({ message, detail: err.message, status: err.status, at: stamp() })
  }

  /** Применить загруженное. true — API отвечает (частичные потери — в apiError); иначе ошибка загрузки ЖК */
  const apply = ([c, a, d, ap, r, s, an]: Fetched): true | ApiError => {
    if (c.status === 'rejected') return asApiError(c.reason)
    setComplexes(c.value)
    if (a.status === 'fulfilled') setAds(a.value)
    if (d.status === 'fulfilled') setDevelopers(d.value)
    if (ap.status === 'fulfilled') setAppeals(ap.value)
    // без ответа /api/risk — локальный расчёт по свежим данным, и это видно в баннере
    setApiRisk(r.status === 'fulfilled' ? riskMap(r.value) : new Map())
    if (s.status === 'fulfilled') setSignals(s.value)
    if (an.status === 'fulfilled' && an.value.length) setAnalysts(an.value)
    const lost = ([['публикации', a], ['застройщики', d], ['обращения', ap], ['риск', r], ['сигналы', s], ['аналитики', an]] as const)
      .filter(([, x]) => x.status === 'rejected')
    if (lost.length) {
      const first = lost[0][1] as PromiseRejectedResult
      fail(`С сервера не загрузилось: ${lost.map(([n]) => n).join(', ')}${r.status === 'rejected' ? ' — риск посчитан локально той же моделью' : ''}`, first.reason)
    }
    setLastSync(stamp())
    return true
  }

  // Если бэкенд FastAPI запущен — берём данные и риск оттуда, иначе работаем на демо-наборе.
  useEffect(() => {
    let alive = true
    fetchAll().then(async (got) => {
      if (!alive) return
      const res = apply(got)
      booted.current = true
      const pending = early.current
      early.current = []
      if (res !== true) {
        setMode('demo')
        // сервер есть, но отвечает ошибкой — это не «демо без бэкенда», говорим прямо
        if (res.status !== 0) fail('Сервер ответил ошибкой — открыт демо-набор без бэкенда', res)
        return
      }
      setMode('api')
      if (!pending.length) return
      await Promise.allSettled(pending)
      const [a2, r2] = await Promise.allSettled([apiGet<Ad[]>('/ads'), apiGet<RiskRow[]>('/risk')])
      if (!alive) return
      if (a2.status === 'fulfilled') setAds(a2.value)
      else fail('Не удалось перечитать публикации', a2.reason)
      if (r2.status === 'fulfilled') setApiRisk(riskMap(r2.value))
      else { setApiRisk(new Map()); fail('Не удалось обновить риск — показан локальный расчёт', r2.reason) }
    })
    return () => { alive = false }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Вход и выход в соседней вкладке
  useEffect(() => onSessionChange((u) => { setUser(u); setSignedOut(!u) }), [])
  // Связь — по итогу каждого запроса, независимо от баннера: успешный запрос после сбоя снимает «Нет связи»
  useEffect(() => onReach((online) => setOffline(!online)), [])

  const devById = useMemo(() => new Map(developers.map((d) => [d.id, d])), [developers])
  const complexById = useMemo(() => new Map(complexes.map((c) => [c.id, c])), [complexes])

  // Локальный риск — пачкой и только для ЖК, по которым нет ответа /api/risk
  const localRisk = useMemo(() => new Map(complexes.filter((c) => !apiRisk.has(c.id))
    .map((c) => [c.id, scoreComplex(c, devById.get(c.developerId), ads, appeals)])), [complexes, ads, appeals, devById, apiRisk])

  const riskOf = useMemo(() => (c: Complex) =>
    apiRisk.get(c.id) ?? localRisk.get(c.id) ?? scoreComplex(c, devById.get(c.developerId), ads, appeals), [apiRisk, localRisk, devById, ads, appeals])
  const adScoreOf = useMemo(() => adScorer(complexById), [complexById])

  const value = useMemo<Store>(() => {
    const live = mode !== 'demo'
    const notify = (title: string) => {
      setToast(title)
      clearTimeout(toastTimer.current)
      toastTimer.current = setTimeout(() => setToast(null), 2800)
    }
    const author = () => {
      if (!user) throw new ApiError(0, NO_USER)
      return user.name
    }
    const refreshRisk = async () => {
      if (!live) return
      try {
        setApiRisk(riskMap(await apiGet<RiskRow[]>('/risk')))
        setLastSync(stamp())
      } catch (e) {
        // устаревший серверный риск не показываем: локальный расчёт по текущим данным — и баннер об этом
        setApiRisk(new Map())
        fail('Не удалось обновить риск с сервера — показан локальный расчёт той же моделью', e)
      }
    }
    const needApi = () => { if (!live) throw new ApiError(0, NO_SIGNALS) }
    /** Изменение сохранено на сервере (и записано в журнал) — страницы перечитают свои загрузки */
    const changed = () => setRevision((v) => v + 1)
    const saved = <T,>(p: Promise<T>) => p.then((v) => { changed(); return v })
    const track = <T,>(p: Promise<T>) => { if (!booted.current) early.current.push(p); return p }
    const patchAd = (id: string, patch: Partial<Ad>) => setAds((xs) => xs.map((a) => (a.id === id ? { ...a, ...patch } : a)))
    const upsert = (sig: Signal) => {
      const row = summaryOf(sig)
      setSignals((xs) => (xs.some((x) => x.id === row.id) ? xs.map((x) => (x.id === row.id ? row : x)) : [row, ...xs]))
      return sig
    }
    /** Событие журнала демо-сессии (на сервере не сохраняется) */
    const logLocal = (e: Pick<AuditEvent, 'kind' | 'what'> & Partial<AuditEvent>): AuditEvent => {
      const ev: AuditEvent = { id: -++localId.current, at: stamp(), who: user?.name ?? '—', adId: null, complexId: null, signalId: null, ...e }
      setSessionLog((xs) => [...xs, ev])
      return ev
    }
    /** Запрос с отчётом об ошибке в баннер; expected — ошибки, которые страница разбирает сама */
    const guarded = async <T,>(what: string, p: () => Promise<T>, expected?: (e: ApiError) => boolean): Promise<T> => {
      try {
        return await p()
      } catch (e) {
        if (!(e instanceof ApiError && expected?.(e))) fail(what, e)
        throw e
      }
    }

    return {
      mode, apiError, offline, lastSync, revision, ads, complexes, developers, appeals, analysts, user, signedOut, signals, sessionLog, toast, notify,
      clearError: () => setApiError(null),
      reconnect: async () => {
        try {
          await apiGet('/health')
        } catch (e) {
          if (mode === 'api') fail('Нет связи с сервером', e)
          else notify('Бэкенд по-прежнему недоступен — работаем на демо-наборе')
          return
        }
        setApiError(null)
        const res = apply(await fetchAll())
        if (res !== true) { fail('Сервер отвечает, но данные не загрузились', res); return }
        booted.current = true
        setMode('api')
        changed()
        notify('Связь с сервером есть, данные обновлены')
      },
      login: (u) => {
        const next: User = { id: u.id, name: u.name, short: u.short, role: u.role }
        writeSession(next)
        setUser(next)
        setSignedOut(false)
      },
      logout: () => {
        writeSession(null)
        setUser(null)
        setSignedOut(true)
      },
      riskOf,
      adScoreOf,
      refreshRisk,
      setAdStatus: async (id, status, opts = {}) => {
        const prev = ads.find((a) => a.id === id)
        if (!user || !prev) { notify(user ? 'Публикация не найдена' : NO_USER); return false }
        const assignee = opts.assignee?.trim() || undefined
        const comment = opts.comment?.trim() || undefined
        patchAd(id, { status, assignee: assignee ?? prev.assignee })
        if (!live) {
          if (status !== prev.status || (assignee && assignee !== prev.assignee) || comment)
            logLocal({ kind: 'status', adId: id, complexId: prev.complexId ?? null, what: statusLine(prev.status, status, assignee !== prev.assignee ? assignee : undefined, comment) })
          return true
        }
        try {
          const got = await track(apiPatch<Ad>(`/ads/${enc(id)}/status`, { status, author: user.name, assignee, comment }))
          patchAd(id, { status: got.status, assignee: got.assignee })
          changed()
        } catch (e) {
          // 409 — статус уже сменил другой аналитик: показываем серверное состояние, а не свой устаревший снимок
          // (не удалось перечитать — откатываем к прежнему; сама ошибка всё равно уходит в баннер ниже)
          const fresh = e instanceof ApiError && e.status === 409
            ? await apiGet<Ad[]>(`/ads`).then((xs) => xs.find((x) => x.id === id), () => undefined) : undefined
          // откатываем только статус и ответственного — параллельная привязка остаётся
          patchAd(id, fresh ? { status: fresh.status, assignee: fresh.assignee } : { status: prev.status, assignee: prev.assignee })
          if (fresh) changed()
          fail(`Статус ${adKey(id)} не сохранён — изменение отменено`, e)
          return false
        }
        await refreshRisk()
        return true
      },
      addAd: async (a) => {
        if (!user) { notify(NO_USER); return false }
        setAds((xs) => [a, ...xs.filter((x) => x.id !== a.id)])
        if (!live) {
          logLocal({ kind: 'system', adId: a.id, complexId: a.complexId ?? null, what: 'Публикация добавлена вручную' })
          return true
        }
        try {
          const got = await track(apiPost<Ad>('/ads', { ...a, author: user.name }))
          setAds((xs) => xs.map((x) => (x.id === a.id ? got : x)))
          changed()
        } catch (e) {
          setAds((xs) => xs.filter((x) => x.id !== a.id))
          fail('Публикация не сохранена', e)
          return false
        }
        await refreshRisk()
        return true
      },
      linkAd: async (id, complexId) => {
        const prev = ads.find((a) => a.id === id)
        if (!user || !prev) { notify(user ? 'Публикация не найдена' : NO_USER); return false }
        patchAd(id, { complexId: complexId ?? undefined, matchConfidence: complexId ? 1 : 0 })
        if (!live) {
          const from = prev.complexId ? complexById.get(prev.complexId) : undefined
          const to = complexId ? complexById.get(complexId) : undefined
          logLocal({ kind: 'link', adId: id, complexId: complexId ?? prev.complexId ?? null, what: linkLine(from, to) })
          return true
        }
        let got: Ad
        try {
          got = await track(apiPatch<Ad>(`/ads/${enc(id)}/complex`, { complexId, author: user.name }))
          changed()
        } catch (e) {
          // откатываем только поля привязки — параллельная смена статуса остаётся
          patchAd(id, { complexId: prev.complexId, matchConfidence: prev.matchConfidence })
          fail(`Привязка ${adKey(id)} не сохранена — изменение отменено`, e)
          return false
        }
        patchAd(id, { complexId: got.complexId ?? undefined, matchConfidence: got.matchConfidence })
        await refreshRisk()
        return true
      },
      getAudit: async (scope) => {
        if (!live) throw new ApiError(0, NO_AUDIT)
        return guarded('Журнал не загрузился', () => apiGet<AuditEvent[]>(auditPath(scope)))
      },
      addComment: async (adId, text) => {
        const who = author()
        const body = text.trim()
        if (!live) return logLocal({ kind: 'comment', adId, complexId: ads.find((a) => a.id === adId)?.complexId ?? null, what: body })
        return saved(guarded('Комментарий не сохранён', () => apiPost<AuditEvent>(`/ads/${enc(adId)}/comments`, { author: who, text: body })))
      },
      requestInfo: async (complexId, topics, comment) => {
        if (!live) throw new ApiError(0, 'Запрос сведений записывается в журнал на сервере — запустите бэкенд (./dev.sh)')
        const who = author()
        return saved(guarded('Запрос сведений не записан', () =>
          apiPost<AuditEvent>(`/complexes/${enc(complexId)}/requests`, { author: who, topics, comment: comment?.trim() || undefined })))
      },
      refreshSignals: async () => {
        if (!live) return
        try {
          setSignals(await apiGet<SignalSummary[]>('/signals'))
          setLastSync(stamp())
        } catch (e) {
          fail('Не удалось обновить реестр сигналов', e)
        }
      },
      createSignal: async (complexId) => {
        needApi()
        const who = author()
        // 409 — по ЖК уже есть нерассмотренный сигнал: страница откроет его сама
        return upsert(await saved(guarded('Сигнал не сформирован', () => apiPost<Signal>('/signals', { complexId, author: who }), (e) => e.status === 409)))
      },
      getSignal: async (id) => {
        needApi()
        return guarded(`Сигнал S-${String(id).padStart(4, '0')} не загрузился`, () => apiGet<Signal>(`/signals/${id}`), (e) => e.status === 404)
      },
      decideSignal: async (id, action, comment) => {
        needApi()
        const who = author()
        // 409 — уже рассмотрен другим аналитиком: карточка перечитает сигнал
        return upsert(await saved(guarded('Решение по сигналу не сохранено', () => apiPost<Signal>(`/signals/${id}/${action}`, { author: who, comment }), (e) => e.status === 409)))
      },
      // автор выгрузки попадает в журнал сигнала
      signalPdfUrl: (id) => `${API}/signals/${id}/pdf?author=${enc(user?.name ?? '')}`,
    }
  }, [mode, apiError, offline, lastSync, revision, ads, complexes, complexById, developers, appeals, analysts, user, signedOut, signals, sessionLog, toast, riskOf, adScoreOf]) // eslint-disable-line react-hooks/exhaustive-deps

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useStore() {
  const s = useContext(Ctx)
  if (!s) throw new Error('StoreProvider missing')
  return s
}
