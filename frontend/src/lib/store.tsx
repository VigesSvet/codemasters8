import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { adKey } from '../components/ui'
import { offlineObject, offlineRegistry, offlineSources } from '../data/openData'
import { READ_ONLY, loadSnapshot, snapshotSource, type RiskRow, type SnapshotMeta } from '../data/snapshot'
import type {
  Ad, AdAnalysis, AdScore, AdStatus, AiEval, Analysis, AnalyzeInput, Analyst, Appeal, AuditEvent, Complex, Developer, ImportReport, OcrResult,
  OcrStatus, RegistryCard, RegistryList, RiskResult, Signal, SignalSummary, SourceInfo, Stats, TermsReview,
} from '../data/types'
import { decisionPatch } from './ai'
import { API, ApiError, apiDelete, apiGet, apiPatch, apiPost, apiUpload, onReach } from './api'
import { plain } from './plain'
import { auditPath, type AuditScope, type RequestTopic } from './audit'
import { keepLocalPreviews } from './media'
import { scoreAd, scoreComplex } from './risk'
import { onSessionChange, readSession, writeSession, type User } from './session'

export type Mode = 'loading' | 'api' | 'snapshot'
export type { AuditScope, User }

/** Последняя неуспешная операция: что не получилось (message), почему (detail), status 0 — нет связи */
export interface ApiErrorInfo { message: string; at: string; detail?: string; status?: number }

interface Store {
  /** api — данные с бэкенда; snapshot — сервер недоступен: офлайн-копия стартового набора (shared/snapshot), только просмотр */
  mode: Mode
  /** Офлайн-копия: дата данных и число записей; null — копия не открыта */
  snapshot: SnapshotMeta | null
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
  /** Вошедший сотрудник (вход пилота без пароля); автор всех действий */
  user: User | null
  /** Сотрудник сам вышел (здесь или в соседней вкладке): вход — без возврата на его страницу */
  signedOut: boolean
  /** Кого можно выбрать при входе: /api/analysts, в офлайн-копии — её список */
  analysts: Analyst[]
  login: (u: User) => void
  logout: () => void
  /** Сводка сервера (/api/stats): «Проверка по рекламе» по ЖК и застройщикам, объекты из рекламы; в офлайн-копии — из неё.
   *  null — нет ответа */
  stats: Stats | null
  /** Перечитать ЖК, застройщиков и сводку — «Проверку по рекламе» после решений по объектам из рекламы и новой рекламы.
   *  Офлайн-копия — ничего не делает; ошибка — без баннера, остаются прежние данные */
  refreshScreening: () => Promise<void>
  /** Риск ЖК: с бэкенда (/api/risk) или из офлайн-копии (тот же ответ сервера), а без них — локально той же моделью */
  riskOf: (c: Complex) => RiskResult
  /** Оценка публикации локально; связанный ЖК берётся из complexes */
  adScoreOf: (a: Ad) => AdScore
  /** Перечитать риск; при ошибке — локальный расчёт и баннер (не отклоняется) */
  refreshRisk: () => Promise<void>
  /** Перечитать ленту публикаций с сервера (сборщик или бот добавили, изменили публикации, закончился их анализ), затем риск;
   *  страницы перечитают свои загрузки (revision). Офлайн-копия — ничего не делает. Не отклоняется: ошибка — в баннер.
   *  quiet — для опроса: ошибка без баннера, лента не изменилась — ни риск, ни revision не трогаются.
   *  true — лента прочитана с сервера; false — офлайн-копия или сервер не ответил */
  refreshAds: (opts?: { quiet?: boolean }) => Promise<boolean>
  /** ЖК изменился на сервере (координаты перенесены из 2ГИС) или создан (по объекту из рекламы): patch — сразу в список ЖК
   *  (карта, карточка, список), затем ЖК перечитывается с сервера; нового в списке — добавляет. Не отклоняется: без ответа остаётся patch */
  refreshComplex: (id: string, patch?: Partial<Pick<Complex, 'lat' | 'lng'>>) => Promise<void>
  /** Смена статуса от имени user.name. false — не сохранено: изменение откатано, ошибка в apiError (офлайн-копия — тост) */
  setAdStatus: (id: string, s: AdStatus, opts?: { assignee?: string; comment?: string }) => Promise<boolean>
  /** Новая публикация. false — не сохранена: убрана из ленты, ошибка в apiError (офлайн-копия — тост) */
  addAd: (a: Ad) => Promise<boolean>
  /** Привязка публикации к ЖК аналитиком (null — снять): привязка становится фактом от имени user.name, событие в журнале.
   *  false — не сохранено: привязка откатана, ошибка в apiError (офлайн-копия — тост) */
  linkAd: (id: string, complexId: string | null) => Promise<boolean>
  // ИИ-анализ (ТЗ §6) — выполняется на сервере; в офлайн-копии методы отклоняются с ApiError(0, …) без баннера
  /** Анализ черновика без записи: условия оплаты, гипотезы ЖК */
  analyze: (input: AnalyzeInput) => Promise<Analysis>
  /** Анализ сохранённой публикации: без отклонённых гипотез, с решениями аналитика */
  getAnalysis: (adId: string) => Promise<AdAnalysis>
  /** Принять гипотезу ИИ: привязать публикацию к ЖК и подтвердить привязку от имени user.name */
  acceptHypothesis: (adId: string, complexId: string) => Promise<Ad>
  /** Отклонить гипотезу: ИИ больше не предлагает этот ЖК; текущая неподтверждённая привязка к нему снимается */
  rejectHypothesis: (adId: string, complexId: string, comment?: string) => Promise<Ad>
  /** Решение по условиям оплаты: rejected — «это не предложение внести деньги», R2 публикации не засчитывается */
  reviewTerms: (adId: string, decision: TermsReview, comment?: string) => Promise<Ad>
  /** Распознать скриншот (413/415/422 — ошибка файла, её показывает страница) */
  ocr: (file: File) => Promise<OcrResult>
  ocrStatus: () => Promise<OcrStatus>
  /** Прогон ИИ на новых текстах (shared/ai_cases.json) */
  aiEval: () => Promise<AiEval>
  /** Журнал с сервера (по возрастанию времени); в офлайн-копии — ApiError(0, …) */
  getAudit: (scope: AuditScope) => Promise<AuditEvent[]>
  /** Комментарий к публикации; в офлайн-копии — ApiError(0, READ_ONLY) */
  addComment: (adId: string, text: string) => Promise<AuditEvent>
  /** Внутренний запрос сведений по ЖК — запись в журнале объекта, во внешние органы не уходит; только с бэкендом */
  requestInfo: (complexId: string, topics: RequestTopic[], comment?: string) => Promise<AuditEvent>
  // Сверка сведений аналитиком по ЖК (сервер: app.verification) — только с бэкендом; в офлайн-копии — ApiError(0, «доступно с
  // сервером»). author — user.name. Ответ ЖК сразу попадает в список, затем риск и «Проверка по рекламе» перечитываются
  // (revision растёт — журнал объекта тоже). Ошибки формы (422, файл 413/415/429/507, 409) показывает окно, не баннер
  /** Документ основания: «есть» / «нет» с источником или «нет сведений» (снять сверку) */
  verifyBasis: (complexId: string, body: Record<string, unknown>) => Promise<Complex>
  /** Опубликованный перечень ГАСК (O1) */
  checkPublishedList: (complexId: string, body: Record<string, unknown>) => Promise<Complex>
  /** Заявленная или подтверждённая готовность (R5) */
  setReadiness: (complexId: string, body: Record<string, unknown>) => Promise<Complex>
  /** «Обращений нет — сверено» (R4) */
  checkAppeals: (complexId: string, body: Record<string, unknown>) => Promise<Complex>
  /** Обращение граждан — ждёт проверки */
  addAppeal: (complexId: string, body: Record<string, unknown>) => Promise<Appeal>
  /** Решение по обращению: подтвердить или отклонить */
  decideAppeal: (appealId: string, action: 'verify' | 'reject', comment?: string) => Promise<Appeal>
  /** Снимок площадки: файл и поля формы (дата, источник, активность, примечание) */
  addSitePhoto: (complexId: string, file: File, fields: Record<string, string>) => Promise<Complex>
  // Фото объекта (сервер: app.gallery) — на риск не влияют; ЖК из ответа — сразу в список
  /** Фото дома от оператора; без закреплённой обложки становится обложкой */
  addGalleryPhoto: (complexId: string, file: File, note?: string) => Promise<Complex>
  /** Закрепить обложку (src одного из фото) или вернуть автоматическую ('') */
  setCover: (complexId: string, src: string) => Promise<Complex>
  /** Удалить фото оператора */
  removeGalleryPhoto: (complexId: string, mediaId: string) => Promise<Complex>
  /** Подгрузить фото карточки ЖК с Homeportal (по записям открытого реестра) */
  fetchGallery: (complexId: string) => Promise<Complex>
  // Решение аналитика по «Проверке по рекламе» (сервер: app.decisions) — как сверка: только с бэкендом, ЖК из ответа — сразу
  // в список, затем застройщики, сводка и журнал
  /** «Объект сдан»: акт №, дата акта, источник, ссылка, комментарий */
  markCommissioned: (complexId: string, body: Record<string, unknown>) => Promise<Complex>
  /** «Проверено — замечаний нет»: что проверено, источник, ссылка, комментарий */
  decideScreening: (complexId: string, body: Record<string, unknown>) => Promise<Complex>
  /** Отменить действующее или снятое рекламой решение: оценка снова автоматическая */
  cancelDecision: (complexId: string, body: Record<string, unknown>) => Promise<Complex>
  // модуль 05: реестр сигналов в офлайн-копии — из неё (только просмотр); сигнал, решение и PDF — только с бэкендом,
  // в офлайн-копии методы отклоняются с ApiError(0, …)
  signals: SignalSummary[]
  /** Перечитать реестр сигналов; ошибка — в apiError (не отклоняется) */
  refreshSignals: () => Promise<void>
  /** author = user.name; при 409 (уже есть новый сигнал) — ApiError.data.signalId */
  createSignal: (complexId: string) => Promise<Signal>
  getSignal: (id: number) => Promise<Signal>
  decideSignal: (id: number, action: 'confirm' | 'return', comment: string) => Promise<Signal>
  signalPdfUrl: (id: number) => string
  // открытые данные (ТЗ §4) — записи источников на сервере; риск, зоны и сигналы для них не считаются.
  // В офлайн-копии — встроенная копия shared/registry_ust_kamenogorsk.json (те же объекты, что API после загрузки); импорт — только
  // с бэкендом
  /** Источники: что, сколько записей, когда собрано; в офлайн-копии последней строкой — сама копия */
  sources: () => Promise<SourceInfo[]>
  /** Объекты открытых данных: с координатами и без; q — поиск по названию, адресу, застройщику, БИН */
  registry: (q?: string) => Promise<RegistryList>
  /** Объект открытых данных и все его записи источников (404 — нет такого ключа, страница покажет сама) */
  registryObject: (key: string) => Promise<RegistryCard>
  /** Импорт xlsx/csv: новые записи — вставка, известные (источник, ID) — обновление; событие в журнале.
   *  413/415/422 — ошибка файла, её показывает форма */
  importSource: (file: File) => Promise<ImportReport>
  toast: string | null
  notify: (title: string) => void
}

const Ctx = createContext<Store | null>(null)

const NO_SIGNALS = 'Сигналы формируются и открываются на сервере — в офлайн-копии доступен только их список'
const NO_AUDIT = 'Журнал хранится на сервере — в офлайн-копии его нет'
const NO_USER = 'Войдите, чтобы действовать от своего имени'
const NO_AI = 'Анализ ИИ и OCR выполняются на сервере — в офлайн-копии доступны только сохранённые результаты'
/** Ошибки файла скриншота: формат, размер, битое изображение — их показывает форма */
const FILE_ERRORS = new Set([413, 415, 422])
const NO_VERIFY = 'Сверка сведений сохраняется на сервере — в офлайн-копии только просмотр'
/** Ошибки окна сверки: проверка полей, файл снимка, лимит загрузок, решение другого аналитика — их показывает окно */
const FORM_ERRORS = new Set([409, 413, 415, 422, 429, 507])

const riskMap = (rows: RiskRow[]) => new Map<string, RiskResult>(rows.map(({ complexId, ...r }) => [complexId, r]))
const summaryOf = ({ id, code, complexId, complexName, status, score, zone, needsCheck, hits, createdAt, createdBy, decidedAt, decidedBy }: Signal): SignalSummary =>
  ({ id, code, complexId, complexName, status, score, zone, needsCheck, hits, createdAt, createdBy, decidedAt, decidedBy })
const stamp = () => new Date().toISOString()
const asApiError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(-1, e instanceof Error ? e.message : String(e)))
const enc = encodeURIComponent

/** Все данные с бэкенда одним заходом; ЖК — обязательная часть, остальное может не загрузиться.
 *  /stats — необязательная сводка: сервер старой версии её не знает, баннера об этом нет */
const fetchAll = () => Promise.allSettled([
  apiGet<Complex[]>('/complexes'), apiGet<Ad[]>('/ads'), apiGet<Developer[]>('/developers'),
  apiGet<Appeal[]>('/appeals'), apiGet<RiskRow[]>('/risk'), apiGet<SignalSummary[]>('/signals'), apiGet<Analyst[]>('/analysts'),
  apiGet<Stats>('/stats'),
] as const)
const isStats = (x: unknown): x is Stats => !!x && typeof x === 'object' && !Array.isArray(x)
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
  // до ответа сервера — пусто: данные приходят с сервера или из офлайн-копии
  const [ads, setAds] = useState<Ad[]>([])
  const [complexes, setComplexes] = useState<Complex[]>([])
  const [developers, setDevelopers] = useState<Developer[]>([])
  const [appeals, setAppeals] = useState<Appeal[]>([])
  const [analysts, setAnalysts] = useState<Analyst[]>([])
  const [snapshot, setSnapshot] = useState<SnapshotMeta | null>(null)
  const [user, setUser] = useState<User | null>(() => readSession())
  // учётную запись могли переименовать на сервере (роль вместо имени) — сессия берёт актуальные имя, значок и роль по id,
  // иначе автором новых действий в журнале осталось бы прежнее имя из localStorage
  useEffect(() => {
    if (!user) return
    const a = analysts.find((x) => x.id === user.id)
    if (!a || (a.name === user.name && a.short === user.short && a.role === user.role)) return
    const next: User = { id: a.id, name: a.name, short: a.short, role: a.role }
    writeSession(next)
    setUser(next)
  }, [analysts]) // eslint-disable-line react-hooks/exhaustive-deps
  const [signedOut, setSignedOut] = useState(false)
  const [apiRisk, setApiRisk] = useState<Map<string, RiskResult>>(() => new Map())
  const [signals, setSignals] = useState<SignalSummary[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [apiError, setApiError] = useState<ApiErrorInfo | null>(null)
  const [lastSync, setLastSync] = useState<string | null>(null)
  const [offline, setOffline] = useState(false)
  const [revision, setRevision] = useState(0)
  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  // Мутации, начатые до конца начальной загрузки: её ответ затрёт их результат — после неё перечитываем /ads и /risk
  const booted = useRef(false)
  const early = useRef<Promise<unknown>[]>([])
  // поколение данных: связь восстановлена — копия, догружающаяся после этого, сервер не затирает
  const gen = useRef(0)

  /** Ошибка операции — в баннер; status 0 — нет связи с сервером */
  const fail = (message: string, e: unknown) => {
    const err = asApiError(e)
    setApiError({ message, detail: plain(err.message), status: err.status, at: stamp() })
  }

  /** Применить загруженное. true — API отвечает (частичные потери — в apiError); иначе ошибка загрузки ЖК */
  const apply = ([c, a, d, ap, r, s, an, st]: Fetched): true | ApiError => {
    if (c.status === 'rejected') return asApiError(c.reason)
    setComplexes(c.value)
    if (a.status === 'fulfilled') setAds(a.value)
    if (d.status === 'fulfilled') setDevelopers(d.value)
    if (ap.status === 'fulfilled') setAppeals(ap.value)
    // без ответа /api/risk — локальный расчёт по свежим данным, и это видно в баннере
    setApiRisk(r.status === 'fulfilled' ? riskMap(r.value) : new Map())
    if (s.status === 'fulfilled') setSignals(s.value)
    if (an.status === 'fulfilled' && an.value.length) setAnalysts(an.value)
    setStats(st.status === 'fulfilled' && isStats(st.value) ? st.value : null)
    const lost = ([['публикации', a], ['застройщики', d], ['обращения', ap], ['риск', r], ['сигналы', s], ['аналитики', an]] as const)
      .filter(([, x]) => x.status === 'rejected')
    if (lost.length) {
      const first = lost[0][1] as PromiseRejectedResult
      fail(`С сервера не загрузилось: ${lost.map(([n]) => n).join(', ')}${r.status === 'rejected' ? ' — риск посчитан локально той же моделью' : ''}`, first.reason)
    }
    setLastSync(stamp())
    return true
  }

  /** Открыть офлайн-копию стартового набора: данные, риск и «Проверка по рекламе» — как их вернул сервер при выгрузке.
   *  Режим 'snapshot' — когда копия загружена (или не загрузилась): до этого страницы показывают загрузку, а не «не найдено» */
  const openSnapshot = (alive: () => boolean) => {
    const g = gen.current
    const fresh = () => alive() && gen.current === g
    loadSnapshot().then((s) => {
      if (!fresh()) return
      setMode('snapshot')
      setComplexes(s.complexes)
      setAds(s.ads)
      setDevelopers(s.developers)
      setAppeals(s.appeals)
      setAnalysts(s.analysts)
      setSignals(s.signals)
      setStats(s.stats)
      setApiRisk(riskMap(s.risk))
      setSnapshot(s.meta)
    }, (e: unknown) => {
      if (!fresh()) return
      setMode('snapshot')
      fail('Сервер недоступен, а офлайн-копия стартового набора не загрузилась', e)
    })
  }

  // Если бэкенд FastAPI запущен — берём данные и риск оттуда, иначе открываем офлайн-копию стартового набора (только просмотр).
  useEffect(() => {
    let alive = true
    fetchAll().then(async (got) => {
      if (!alive) return
      const res = apply(got)
      booted.current = true
      const pending = early.current
      early.current = []
      if (res !== true) {
        // сервер есть, но отвечает ошибкой — это не «сервер недоступен», говорим прямо
        if (res.status !== 0) fail('Сервер ответил ошибкой — открыта офлайн-копия стартового набора, только просмотр', res)
        openSnapshot(() => alive)
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
    // до ответа сервера изменения уходят на сервер (track); в офлайн-копии — только просмотр
    const live = mode !== 'snapshot'
    const notify = (title: string) => {
      setToast(title)
      clearTimeout(toastTimer.current)
      toastTimer.current = setTimeout(() => setToast(null), 2800)
    }
    const author = () => {
      if (!user) throw new ApiError(0, NO_USER)
      return user.name
    }
    /** «Проверка по рекламе» меняется вместе с рекламой и привязками: ЖК, застройщики и сводка — заново; ошибка — без баннера */
    const refreshScreening = async () => {
      if (!live) return
      const [c, d, st] = await Promise.allSettled([apiGet<Complex[]>('/complexes'), apiGet<Developer[]>('/developers'), apiGet<Stats>('/stats')])
      if (c.status === 'fulfilled' && Array.isArray(c.value)) setComplexes(c.value)
      if (d.status === 'fulfilled' && Array.isArray(d.value)) setDevelopers(d.value)
      if (st.status === 'fulfilled' && isStats(st.value)) setStats(st.value)
    }
    const refreshRisk = async () => {
      if (!live) return
      const screening = refreshScreening()
      try {
        setApiRisk(riskMap(await apiGet<RiskRow[]>('/risk')))
        setLastSync(stamp())
      } catch (e) {
        // устаревший серверный риск не показываем: локальный расчёт по текущим данным — и баннер об этом
        setApiRisk(new Map())
        fail('Не удалось обновить риск с сервера — показан локальный расчёт той же моделью', e)
      }
      await screening
    }
    const needApi = () => { if (!live) throw new ApiError(0, NO_SIGNALS) }
    const needAi = () => { if (!live) throw new ApiError(0, NO_AI) }
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
    /** Изменение в офлайн-копии: не выполняется — тост «только для просмотра» */
    const readOnly = () => { notify(READ_ONLY); return false }
    /** Запрос с отчётом об ошибке в баннер; expected — ошибки, которые страница разбирает сама */
    const guarded = async <T,>(what: string, p: () => Promise<T>, expected?: (e: ApiError) => boolean): Promise<T> => {
      try {
        return await p()
      } catch (e) {
        if (!(e instanceof ApiError && expected?.(e))) fail(what, e)
        throw e
      }
    }
    const needVerify = () => { if (!live) throw new ApiError(0, NO_VERIFY) }
    /** Сверка по ЖК сохранена: ЖК из ответа — сразу в список, затем журнал (revision), риск и «Проверка по рекламе» */
    const verified = async (what: string, p: () => Promise<Complex>) => {
      const got = await track(guarded(what, p, (e) => FORM_ERRORS.has(e.status)))
      if (got?.id) setComplexes((xs) => xs.map((x) => (x.id === got.id ? got : x)))
      changed()
      await refreshRisk()
      return got
    }
    const appealSaved = async (what: string, p: () => Promise<Appeal>) => {
      const got = await track(guarded(what, p, (e) => FORM_ERRORS.has(e.status)))
      if (got?.id) setAppeals((xs) => (xs.some((x) => x.id === got.id) ? xs.map((x) => (x.id === got.id ? got : x)) : [got, ...xs]))
      changed()
      await refreshRisk()
      return got
    }
    /** Решение аналитика по выводу ИИ: из ответа сервера берём только поля привязки и решения по условиям (как linkAd) —
     *  параллельная смена статуса и скриншот этой вкладки остаются; затем журнал (revision) и риск ЖК */
    const decided = async (what: string, p: () => Promise<Ad>) => {
      const got = await track(guarded(what, p))
      patchAd(got.id, decisionPatch(got))
      changed()
      await refreshRisk()
      return got
    }

    return {
      mode, snapshot, apiError, offline, lastSync, revision, ads, complexes, developers, appeals, analysts, user, signedOut, signals, toast, notify,
      stats, refreshScreening,
      clearError: () => setApiError(null),
      reconnect: async () => {
        try {
          await apiGet('/health')
        } catch (e) {
          if (mode === 'api') fail('Нет связи с сервером', e)
          // офлайн-копия не загрузилась — пробуем её ещё раз
          else if (mode === 'snapshot' && !snapshot) { setApiError(null); openSnapshot(() => true) }
          else notify('Сервер по-прежнему недоступен — открыта офлайн-копия, только просмотр')
          return
        }
        setApiError(null)
        const res = apply(await fetchAll())
        if (res !== true) { fail('Сервер отвечает, но данные не загрузились', res); return }
        // копия, которая ещё догружается, данные сервера не затрёт
        gen.current += 1
        booted.current = true
        setSnapshot(null)
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
      refreshAds: async ({ quiet } = {}) => {
        if (mode !== 'api') return false
        let got: Ad[]
        try {
          got = await apiGet<Ad[]>('/ads')
        } catch (e) {
          if (!quiet) fail('Не удалось обновить ленту публикаций', e)
          return false
        }
        if (!Array.isArray(got)) return false
        setLastSync(stamp())
        // опрос без изменений: не перечитываем риск, журналы и анализ на страницах. Сравниваем вместе с предпросмотрами этой
        // вкладки (blob:) — иначе публикация со скриншотом, не сохранённым на сервере, «меняла» бы ленту при каждом опросе
        if (quiet && JSON.stringify(keepLocalPreviews(got, ads)) === JSON.stringify(ads)) return true
        // скриншот этой вкладки (blob:), не сохранённый на сервере, остаётся предпросмотром до перезагрузки
        setAds((xs) => keepLocalPreviews(got, xs))
        await refreshRisk()
        changed()
        return true
      },
      refreshComplex: async (id, patch) => {
        // офлайн-копия не меняется: изменения ЖК делает только сервер
        if (!live) return
        if (patch) setComplexes((xs) => xs.map((x) => (x.id === id ? { ...x, ...patch } : x)))
        if (mode !== 'api') return
        try {
          const got = await apiGet<Complex>(`/complexes/${enc(id)}`)
          // координаты — парой: обе есть или обеих нет (ЖК по рекламе до сверки в 2ГИС)
          const pair = (Number.isFinite(got?.lat) && Number.isFinite(got?.lng)) || (got?.lat == null && got?.lng == null)
          if (got?.id === id && pair) {
            // новая карточка (создана по объекту из рекламы) — в конец списка
            setComplexes((xs) => (xs.some((x) => x.id === id) ? xs.map((x) => (x.id === id ? got : x)) : [...xs, got]))
            setLastSync(stamp())
          }
        } catch {
          // без ответа остаётся patch — та же точка, что записал сервер
        }
      },
      setAdStatus: async (id, status, opts = {}) => {
        if (!live) return readOnly()
        const prev = ads.find((a) => a.id === id)
        if (!user || !prev) { notify(user ? 'Публикация не найдена' : NO_USER); return false }
        const assignee = opts.assignee?.trim() || undefined
        const comment = opts.comment?.trim() || undefined
        patchAd(id, { status, assignee: assignee ?? prev.assignee })
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
        if (!live) return readOnly()
        if (!user) { notify(NO_USER); return false }
        setAds((xs) => [a, ...xs.filter((x) => x.id !== a.id)])
        // linkConfirmed — ЖК выбран аналитиком вручную: привязка сразу факт; иначе гипотеза ИИ.
        // Скриншот сначала загружает форма (POST /api/media) — в image приходит /api/media/{id}, его и сохраняем.
        // Локальный скриншот (blob:) живёт только в этой вкладке — на сервер его адрес не отправляем, в сессии показываем
        const local = a.image?.startsWith('blob:') ? a.image : undefined
        const body = {
          id: a.id, platform: a.platform, account: a.account, url: a.url, date: a.date, text: a.text, ocr: a.ocr, image: local ? undefined : a.image,
          price: a.price, complexId: a.complexId, matchConfidence: a.matchConfidence, source: a.source, status: a.status, author: user.name,
          linkConfirmed: !!a.linkConfirmedBy,
        }
        try {
          const got = await track(apiPost<Ad>('/ads', body))
          setAds((xs) => xs.map((x) => (x.id === a.id ? (local && !got.image ? { ...got, image: local } : got) : x)))
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
        if (!live) return readOnly()
        const prev = ads.find((a) => a.id === id)
        if (!user || !prev) { notify(user ? 'Публикация не найдена' : NO_USER); return false }
        const confirmed = complexId ? { linkConfirmedBy: user.name, linkConfirmedAt: stamp() } : { linkConfirmedBy: null, linkConfirmedAt: null }
        patchAd(id, { complexId: complexId ?? undefined, matchConfidence: complexId ? 1 : 0, ...confirmed })
        let got: Ad
        try {
          got = await track(apiPatch<Ad>(`/ads/${enc(id)}/complex`, { complexId, author: user.name }))
          changed()
        } catch (e) {
          // откатываем только поля привязки — параллельная смена статуса остаётся
          patchAd(id, { complexId: prev.complexId, matchConfidence: prev.matchConfidence, linkConfirmedBy: prev.linkConfirmedBy, linkConfirmedAt: prev.linkConfirmedAt })
          fail(`Привязка ${adKey(id)} не сохранена — изменение отменено`, e)
          return false
        }
        patchAd(id, { complexId: got.complexId ?? undefined, matchConfidence: got.matchConfidence, linkConfirmedBy: got.linkConfirmedBy ?? null, linkConfirmedAt: got.linkConfirmedAt ?? null })
        await refreshRisk()
        return true
      },
      analyze: async (input) => {
        needAi()
        return guarded('Анализ ИИ не выполнен', () => apiPost<Analysis>('/analyze', input))
      },
      getAnalysis: async (adId) => {
        needAi()
        return guarded(`Анализ ИИ ${adKey(adId)} не загрузился`, () => apiGet<AdAnalysis>(`/ads/${enc(adId)}/analysis`))
      },
      acceptHypothesis: async (adId, complexId) => {
        needAi()
        const who = author()
        return decided('Гипотеза ИИ не принята', () => apiPost<Ad>(`/ads/${enc(adId)}/hypotheses/accept`, { author: who, complexId }))
      },
      rejectHypothesis: async (adId, complexId, comment) => {
        needAi()
        const who = author()
        return decided('Гипотеза ИИ не отклонена', () =>
          apiPost<Ad>(`/ads/${enc(adId)}/hypotheses/reject`, { author: who, complexId, comment: comment?.trim() || undefined }))
      },
      reviewTerms: async (adId, decision, comment) => {
        needAi()
        const who = author()
        return decided('Решение по условиям оплаты не сохранено', () =>
          apiPost<Ad>(`/ads/${enc(adId)}/terms-review`, { author: who, decision, comment: comment?.trim() || undefined }))
      },
      ocr: async (file) => {
        needAi()
        const form = new FormData()
        form.append('file', file)
        return guarded('Скриншот не распознан', () => apiUpload<OcrResult>('/ocr', form), (e) => FILE_ERRORS.has(e.status))
      },
      ocrStatus: async () => {
        needAi()
        return guarded('Не удалось узнать, работает ли OCR на сервере', () => apiGet<OcrStatus>('/ocr/status'))
      },
      aiEval: async () => {
        needAi()
        return guarded('Проверка ИИ на новых данных не загрузилась', () => apiGet<AiEval>('/ai/eval'))
      },
      getAudit: async (scope) => {
        if (!live) throw new ApiError(0, NO_AUDIT)
        return guarded('Журнал не загрузился', () => apiGet<AuditEvent[]>(auditPath(scope)))
      },
      addComment: async (adId, text) => {
        if (!live) throw new ApiError(0, READ_ONLY)
        const who = author()
        const body = text.trim()
        return saved(guarded('Комментарий не сохранён', () => apiPost<AuditEvent>(`/ads/${enc(adId)}/comments`, { author: who, text: body })))
      },
      requestInfo: async (complexId, topics, comment) => {
        if (!live) throw new ApiError(0, 'Запрос сведений записывается в журнал на сервере — в офлайн-копии недоступен')
        const who = author()
        return saved(guarded('Запрос сведений не записан', () =>
          apiPost<AuditEvent>(`/complexes/${enc(complexId)}/requests`, { author: who, topics, comment: comment?.trim() || undefined })))
      },
      verifyBasis: async (complexId, body) => {
        needVerify()
        const who = author()
        return verified('Сверка основания не сохранена', () => apiPost<Complex>(`/complexes/${enc(complexId)}/basis`, { ...body, author: who }))
      },
      checkPublishedList: async (complexId, body) => {
        needVerify()
        const who = author()
        return verified('Сверка с перечнем ГАСК не сохранена', () =>
          apiPost<Complex>(`/complexes/${enc(complexId)}/published-list`, { ...body, author: who }))
      },
      setReadiness: async (complexId, body) => {
        needVerify()
        const who = author()
        return verified('Готовность не сохранена', () => apiPost<Complex>(`/complexes/${enc(complexId)}/readiness`, { ...body, author: who }))
      },
      checkAppeals: async (complexId, body) => {
        needVerify()
        const who = author()
        return verified('Сверка обращений не сохранена', () => apiPost<Complex>(`/complexes/${enc(complexId)}/appeals-check`, { ...body, author: who }))
      },
      addAppeal: async (complexId, body) => {
        needVerify()
        const who = author()
        return appealSaved('Обращение не сохранено', () => apiPost<Appeal>(`/complexes/${enc(complexId)}/appeals`, { ...body, author: who }))
      },
      decideAppeal: async (appealId, action, comment) => {
        needVerify()
        const who = author()
        return appealSaved('Решение по обращению не сохранено', () =>
          apiPost<Appeal>(`/appeals/${enc(appealId)}/${action}`, { author: who, comment: comment?.trim() || undefined }))
      },
      addSitePhoto: async (complexId, file, fields) => {
        needVerify()
        const who = author()
        const form = new FormData()
        form.append('file', file, file.name || 'site.jpg')
        for (const [k, v] of Object.entries({ ...fields, author: who })) form.append(k, v)
        return verified('Снимок площадки не сохранён', () => apiUpload<Complex>(`/complexes/${enc(complexId)}/photos`, form))
      },
      addGalleryPhoto: async (complexId, file, note) => {
        needVerify()
        const who = author()
        const form = new FormData()
        form.append('file', file, file.name || 'photo.jpg')
        form.append('author', who)
        if (note?.trim()) form.append('note', note.trim())
        return verified('Фото не сохранено', () => apiUpload<Complex>(`/complexes/${enc(complexId)}/gallery`, form))
      },
      setCover: async (complexId, src) => {
        needVerify()
        const who = author()
        return verified('Обложка не сохранена', () => apiPost<Complex>(`/complexes/${enc(complexId)}/cover`, { author: who, src }))
      },
      removeGalleryPhoto: async (complexId, mediaId) => {
        needVerify()
        const who = author()
        return verified('Фото не удалено', () => apiDelete<Complex>(`/complexes/${enc(complexId)}/gallery/${enc(mediaId)}`, { author: who }))
      },
      fetchGallery: async (complexId) => {
        needVerify()
        const who = author()
        return verified('Фото с Homeportal не подгрузились', () => apiPost<Complex>(`/complexes/${enc(complexId)}/gallery/fetch`, { author: who }))
      },
      markCommissioned: async (complexId, body) => {
        needVerify()
        const who = author()
        return verified('Отметка «Объект сдан» не сохранена', () => apiPost<Complex>(`/complexes/${enc(complexId)}/commissioned`, { ...body, author: who }))
      },
      decideScreening: async (complexId, body) => {
        needVerify()
        const who = author()
        return verified('Решение «Проверено — замечаний нет» не сохранено', () =>
          apiPost<Complex>(`/complexes/${enc(complexId)}/screening-decision`, { ...body, author: who }))
      },
      cancelDecision: async (complexId, body) => {
        needVerify()
        const who = author()
        return verified('Решение не отменено', () => apiDelete<Complex>(`/complexes/${enc(complexId)}/screening-decision`, { ...body, author: who }))
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
      sources: async () => guarded('Список источников не загрузился', async () =>
        (live ? apiGet<SourceInfo[]>('/sources') : offlineSources().then((xs) => (snapshot ? [...xs, snapshotSource(snapshot)] : xs)))),
      registry: async (q) => {
        const query = q?.trim()
        return guarded('Открытые данные не загрузились', () =>
          (live ? apiGet<RegistryList>(`/registry${query ? `?q=${enc(query)}` : ''}`) : offlineRegistry(query)))
      },
      registryObject: async (key) => guarded('Объект открытых данных не загрузился', async () => {
        if (live) return apiGet<RegistryCard>(`/registry/${enc(key)}`)
        const got = await offlineObject(key)
        if (!got) throw new ApiError(404, 'Объект открытых данных не найден')
        return got
      }, (e) => e.status === 404),
      importSource: async (file) => {
        if (!live) throw new ApiError(0, 'Импорт выполняется на сервере — в офлайн-копии недоступен')
        const form = new FormData()
        form.append('file', file)
        form.append('author', author())
        // импорт пишет событие в журнал и меняет реестр — страницы перечитают свои загрузки
        return saved(guarded('Файл не импортирован', () => apiUpload<ImportReport>('/sources/import', form), (e) => FILE_ERRORS.has(e.status)))
      },
    }
  }, [mode, snapshot, apiError, offline, lastSync, revision, ads, complexes, developers, appeals, analysts, user, signedOut, signals, toast, riskOf, adScoreOf, stats]) // eslint-disable-line react-hooks/exhaustive-deps

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useStore() {
  const s = useContext(Ctx)
  if (!s) throw new Error('StoreProvider missing')
  return s
}
