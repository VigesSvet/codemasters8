// Подключения источников публикаций (integration-spec §10.4–§10.5, §11, §13.2): запросы к серверу и подписи по-русски.
// Состояние подключений — только фактическое, с сервера: в офлайн-копии его нет, поддельных статусов не показываем.
// «Заявка отправлена» и «ключ создан» — не «работает»: «работает» только у сборщика, у которого прошёл сбор.
import { fmtDT, plural } from '../components/ui'
import type {
  AdVersion, CollectAllResult, CollectionRun, ConfigField, Connector, ConnectorCode, ConnectorGroup, ConnectorKind, ConnectorPatch,
  ConnectorStatus, ConnectorsResponse, OlxStart, Platform, Processing, RunCounts, RunStatus, RunTrigger,
} from '../data/types'
import { adminCall } from './admin'
import { ApiError, apiGet, apiPatch, apiPost, orNoRoute, orNull } from './api'

export { checkAdmin, signInAdmin } from './admin'

const enc = encodeURIComponent
const isObj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x)

// ---------- Запросы ----------

const isConnectors = (x: unknown) => isObj(x) && Array.isArray(x.connectors) && isObj(x.worker)
const isConnector = (x: unknown) => isObj(x) && typeof x.code === 'string' && typeof x.status === 'string'

/** Все подключения, воркер и adminConfigured. null — недоступно (нет связи, сервер без сбора публикаций, не JSON);
 *  при опросе null лучше не затирать прежний список. Ошибки сервера (500…) — ApiError */
export const getConnectors = () => orNull(apiGet<ConnectorsResponse>('/sources/connectors'), isConnectors)

/** Одно подключение; null — недоступно; 404 «Подключение не найдено» — ApiError */
export const getConnector = (code: ConnectorCode | string) => orNull(apiGet<Connector>(`/sources/connectors/${enc(code)}`), isConnector)

/** Настройки подключения (администратор): включение расписания, период, списки. 409/422 — текст сервера */
export const patchConnector = (code: ConnectorCode | string, body: ConnectorPatch) =>
  adminCall((h) => apiPatch<Connector>(`/sources/${enc(code)}`, body, h))

/** «Проверить подключение» (администратор) → запуск проверки (202) */
export const testConnector = (code: ConnectorCode | string, author: string) =>
  adminCall((h) => apiPost<CollectionRun>(`/sources/${enc(code)}/test`, { author }, h))

/** «Собрать сейчас» (администратор) → новый запуск (202) или уже идущий (200) */
export const collectConnector = (code: ConnectorCode | string, author: string) =>
  adminCall((h) => apiPost<CollectionRun>(`/sources/${enc(code)}/collect`, { author }, h))

/** «Обновить всё» (администратор): сбор всех включённых и проверенных сборщиков; пропущенные — с причиной */
export const collectAll = (author: string) => adminCall((h) => apiPost<CollectAllResult>('/sources/collect-all', { author }, h))

/** Запуск по номеру — для опроса хода; ошибки (в т. ч. нет связи) — ApiError, их показывает панель запуска */
export const getRun = (id: number) => orNoRoute(apiGet<CollectionRun>(`/collection-runs/${id}`))

/** Последние запуски (новые первыми), по подключению или все; null — недоступно */
export const getRuns = (source?: ConnectorCode | string, limit = 20) => {
  const q = new URLSearchParams({ limit: String(Math.min(100, Math.max(1, Math.round(limit)))) })
  if (source) q.set('source', source)
  return orNull(apiGet<CollectionRun[]>(`/collection-runs?${q}`), Array.isArray)
}

/** Версии публикации в источнике (новые первыми; [] — публикация не из приёма). null — недоступно;
 *  404 «Публикация не найдена» — ApiError */
export const getVersions = (adId: string) => orNull(apiGet<AdVersion[]>(`/ads/${enc(adId)}/versions`), Array.isArray)

/** Адрес авторизации OLX принимаем только https на olx.kz — иначе не переходим */
export function safeAuthorizeUrl(u: unknown): string | null {
  if (typeof u !== 'string') return null
  try {
    const url = new URL(u)
    const host = url.hostname.toLowerCase()
    return url.protocol === 'https:' && (host === 'olx.kz' || host.endsWith('.olx.kz')) && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

/** «Подключить аккаунт OLX» (администратор) → {authorizeUrl}; дальше window.location.assign(authorizeUrl).
 *  409 «OLX не настроен: заявка отправлена…» — текст сервера */
export async function olxStart(author: string): Promise<OlxStart> {
  const got = await adminCall((h) => apiPost<OlxStart>('/integrations/olx/start', { author }, h))
  const url = safeAuthorizeUrl(got?.authorizeUrl)
  if (!url) throw new ApiError(-1, 'Сервер вернул неожиданный адрес авторизации OLX — переход отменён')
  return { ...got, authorizeUrl: url }
}

/** Отключить аккаунт OLX (администратор): токены удаляются на сервере, статус «не подключён» */
export const olxDisconnect = (author: string) => adminCall((h) => apiPost<unknown>('/integrations/olx/disconnect', { author }, h))

// ---------- Статусы подключений (§10.4) ----------

export type Tone = 'gray' | 'blue' | 'green' | 'yellow' | 'red'

export const STATUS_LABEL: Record<ConnectorStatus, string> = {
  not_configured: 'не настроен',
  unchecked: 'не проверен',
  checking: 'проверяется',
  queued: 'в очереди',
  running: 'идёт сбор',
  ready: 'готов к сбору',
  working: 'работает',
  partial: 'работает частично',
  waiting: 'ожидает заявок',
  off: 'не используется',
  error: 'ошибка',
  expired: 'истёк доступ',
  denied: 'нет доступа',
  access_check: 'нужно ручное действие',
  rate_limited: 'пауза: лимит запросов',
}

/** Цвет метки статуса: класс `tag ${tone}` из go.css */
export const STATUS_TONE: Record<ConnectorStatus, Tone> = {
  not_configured: 'gray',
  unchecked: 'gray',
  checking: 'blue',
  queued: 'blue',
  running: 'blue',
  ready: 'green',
  working: 'green',
  partial: 'yellow',
  waiting: 'gray',
  off: 'gray',
  error: 'red',
  expired: 'yellow',
  denied: 'yellow',
  access_check: 'yellow',
  rate_limited: 'yellow',
}

/** Подписи отдельных подключений вместо общих (как labels коннекторов на сервере) */
export const LABEL_OVERRIDES: Partial<Record<string, Partial<Record<ConnectorStatus, string>>>> = {
  telegram_bot: { working: 'принимает заявки' },
  intake: { working: 'принимает публикации' },
  dgis: { ready: 'готов' },
  anthropic: { ready: 'готов', working: 'анализирует публикации', rate_limited: 'пауза: лимит' },
  olx: { not_configured: 'заявка отправлена', unchecked: 'не подключён', ready: 'подключено · ограничено', working: 'подключено · ограничено' },
}

const known = (s: string): s is ConnectorStatus => Object.hasOwn(STATUS_TONE, s)

/** Статус для показа: без ключей на сервере — всегда «не настроен», что бы ни пришло; неизвестный — «не проверен» */
export function effectiveStatus(c: Pick<Connector, 'status' | 'configured'>): ConnectorStatus {
  if (!c.configured) return 'not_configured'
  return known(c.status) ? c.status : 'unchecked'
}

/** Подпись статуса подключения по коду (без ответа сервера) */
export const labelFor = (code: string, status: ConnectorStatus) =>
  (Object.hasOwn(LABEL_OVERRIDES, code) ? LABEL_OVERRIDES[code]?.[status] : undefined) ?? STATUS_LABEL[status]

const WORKS = /^\s*работает\s*$/i

/** Подпись статуса для карточки: подпись сервера, но «работает» — только у сборщика со статусом working;
 *  ограниченное подключение (OLX) и «ключ есть/заявка отправлена» так не называются */
export function statusText(c: Pick<Connector, 'code' | 'status' | 'statusLabel' | 'configured' | 'group' | 'kind'>): string {
  const s = effectiveStatus(c)
  const own = c.statusLabel?.trim()
  // подпись сервера — к показываемому статусу (и к незнакомому статусу новой версии сервера)
  const label = own && (s === c.status || (c.configured && !known(c.status))) ? own : labelFor(c.code, s)
  if (!WORKS.test(label) || (s === 'working' && c.kind === 'collector')) return label
  const alt = labelFor(c.code, s)
  return !WORKS.test(alt) ? alt : groupOf(c) === 'limited' ? 'подключено · ограничено' : 'готов'
}

export const toneOf = (c: Pick<Connector, 'status' | 'configured'>): Tone => STATUS_TONE[effectiveStatus(c)]

/** Проверка, сбор в очереди или идёт — страница опрашивает сервер (каждые 5 с) */
export const BUSY: ReadonlySet<ConnectorStatus> = new Set(['checking', 'queued', 'running'])
export const isBusy = (c: Pick<Connector, 'status' | 'configured'>) => BUSY.has(effectiveStatus(c))
export const anyBusy = (list: readonly Connector[]) => list.some(isBusy)

// ---------- Группы (§13.2): сборщики → приём → геосервис → анализатор → ограниченно ----------

export const GROUP_ORDER: readonly ConnectorGroup[] = ['collectors', 'intake', 'geo', 'analyzer', 'limited']
export const GROUP_TITLE: Record<ConnectorGroup, string> = {
  collectors: 'Сборщики публикаций',
  intake: 'Приём заявок и публикаций',
  geo: 'Геосервис',
  analyzer: 'Анализатор',
  limited: 'Ограниченно',
}
const GROUP_BY_KIND: Record<ConnectorKind, ConnectorGroup> = {
  collector: 'collectors', intake: 'intake', geo: 'geo', analyzer: 'analyzer', oauth: 'limited',
}

/** Группа подключения: с сервера, а если её нет — по виду (oauth → «Ограниченно»); неизвестное — к ограниченным */
export function groupOf(c: Pick<Connector, 'group' | 'kind'>): ConnectorGroup {
  if (c.group && Object.hasOwn(GROUP_TITLE, c.group)) return c.group
  return Object.hasOwn(GROUP_BY_KIND, c.kind) ? GROUP_BY_KIND[c.kind] : 'limited'
}

export interface ConnectorSection { group: ConnectorGroup; title: string; items: Connector[] }

/** Подключения по группам в порядке GROUP_ORDER; внутри группы — порядок сервера; пустые группы не выводятся */
export function groupConnectors(list: readonly Connector[]): ConnectorSection[] {
  return GROUP_ORDER
    .map((group) => ({ group, title: GROUP_TITLE[group], items: list.filter((c) => groupOf(c) === group) }))
    .filter((s) => s.items.length > 0)
}

/** Значок карточки: площадка для PlatformIcon (telegram_bot — Telegram, dgis — 2ГИС); null — свой значок
 *  (intake — PlugConnection, анализатор anthropic — Sparkles) */
export function iconPlatform(c: Pick<Connector, 'code' | 'platform'>): Platform | null {
  if (c.code === 'dgis') return '2gis'
  if (c.code === 'telegram_bot') return 'telegram'
  if (c.code === 'intake' || c.code === 'anthropic') return null
  const p = c.platform ?? c.code
  return (['instagram', 'tiktok', 'krisha', 'olx', 'telegram', 'facebook', '2gis'] as const).find((x) => x === p) ?? null
}

/** Сборщики «в работе» (плитка героя): включены и готовы, работают или собирают */
const IN_WORK: ReadonlySet<ConnectorStatus> = new Set(['ready', 'working', 'partial', 'running', 'queued'])
export const workingCollectors = (list: readonly Connector[]) =>
  list.filter((c) => c.kind === 'collector' && c.enabled && IN_WORK.has(effectiveStatus(c)))

/** Статусы, при которых планировщик сервера ставит сбор (jobs.SCHEDULABLE) — их же берёт «Обновить всё» */
export const SCHEDULABLE: ReadonlySet<ConnectorStatus> = new Set(['ready', 'working', 'partial', 'error', 'rate_limited'])

/** Что «Обновить всё» поставит в очередь (как collect_all на сервере): включённые, настроенные, проверенные, не остановленные,
 *  без паузы по лимиту. Пусто — кнопка неактивна */
export function collectable(list: readonly Connector[], now: Date = new Date()): Connector[] {
  return list.filter((c) => {
    const s = effectiveStatus(c)
    if (c.kind !== 'collector' || !c.enabled || c.stopped || !SCHEDULABLE.has(s)) return false
    return !(s === 'rate_limited' && c.nextRunAt && new Date(c.nextRunAt).getTime() > now.getTime())
  })
}

/** «Следующий запуск»: at — время, если планировщик действительно поставит сбор (как schedule_due на сервере: включён,
 *  настроен, не остановлен, статус из SCHEDULABLE); иначе text — почему не запланирован. null — строки нет: не сборщик,
 *  выключен (рядом метка «выключен») или проверка/сбор уже идут */
export type NextRun = { text: string; at?: string }
export function nextRun(c: Connector, now: Date = new Date()): NextRun | null {
  if (c.kind !== 'collector' || !c.enabled) return null
  const s = effectiveStatus(c)
  if (BUSY.has(s)) return null
  if (s === 'not_configured') return { text: 'не запланирован — подключение не настроено на сервере' }
  if (c.stopped) return { text: 'не запланирован — сбор остановлен после ошибки' }
  if (s === 'unchecked') return { text: 'не запланирован — сначала «Проверить подключение»' }
  if (s === 'expired' || s === 'denied' || s === 'access_check') return { text: 'не запланирован — ждёт ручного действия' }
  if (!SCHEDULABLE.has(s)) return null
  const t = c.nextRunAt ? Date.parse(c.nextRunAt) : Number.NaN
  if (c.nextRunAt && Number.isFinite(t) && t > now.getTime()) return { text: when(c.nextRunAt), at: c.nextRunAt }
  // срок прошёл или его нет — планировщик поставит сбор при ближайшем проходе
  return { text: 'скоро — при ближайшем проходе планировщика' }
}

/** Строка о последнем успехе: у сборщика — последний успешный сбор (всегда, «—» — сбора ещё не было);
 *  у геосервиса и OLX — время последней проверки (сбора у них нет); у приёма — только если сервер его знает
 *  (время последней публикации — в пояснении сервера); у анализатора — нет */
export function lastOkRow(c: Pick<Connector, 'kind' | 'lastSuccessAt' | 'checkedAt'>): { label: string; at: string | null } | null {
  if (c.kind === 'collector') return { label: 'Последний успех', at: c.lastSuccessAt ?? null }
  if (c.kind === 'geo' || c.kind === 'oauth') return c.checkedAt ? { label: 'Последняя проверка', at: c.checkedAt } : null
  if (c.kind === 'intake') return c.lastSuccessAt ? { label: 'Последний успех', at: c.lastSuccessAt } : null
  if (c.kind === 'analyzer') {
    if (c.lastSuccessAt) return { label: 'Последний анализ', at: c.lastSuccessAt }
    return c.checkedAt ? { label: 'Последняя проверка', at: c.checkedAt } : null
  }
  return null
}

/** Итог успешной «Проверить подключение»: «можно включать сбор» — только у сборщика; 2ГИС и OLX сборщиками не называем */
export function testOkLine(c: Pick<Connector, 'kind' | 'enabled'>): string {
  if (c.kind === 'collector') return c.enabled ? 'Подключение проверено — сбор по расписанию включён.' : 'Подключение проверено — можно включать сбор.'
  if (c.kind === 'geo') return 'Ключ 2ГИС работает — сверка адреса доступна в карточке ЖК.'
  if (c.kind === 'oauth') return 'Аккаунт OLX доступен — только ресурсы своего аккаунта.'
  if (c.kind === 'analyzer') return 'Ключ Anthropic принят, модель доступна — новые публикации анализирует Claude (гипотезы для аналитика).'
  return 'Подключение проверено.'
}

/** Токен есть, но не действует — нужна повторная авторизация */
const RELINK: ReadonlySet<ConnectorStatus> = new Set(['expired', 'denied', 'error'])

/** Аккаунт OAuth (OLX): linked — токен сохранён на сервере (можно отключить); relink — токен не действует (истёк, нет доступа,
 *  ошибка): нужна кнопка «Подключить заново» рядом с «Отключить аккаунт» */
export function accountState(c: Pick<Connector, 'status' | 'configured'>): { linked: boolean; relink: boolean } {
  const s = effectiveStatus(c)
  const linked = s !== 'not_configured' && s !== 'unchecked'
  return { linked, relink: linked && RELINK.has(s) }
}

/** Новые и изменённые публикации завершённых запусков с прошлого снимка — пора перечитать ленту */
export type RunsSeen = ReadonlyMap<string, { id: number; terminal: boolean }>
/** Снимок последних запусков: код подключения → номер запуска и завершён ли он */
export const runsSeen = (list: readonly Connector[]): RunsSeen =>
  new Map(list.flatMap((c) => (c.lastRun ? [[c.code, { id: c.lastRun.id, terminal: isTerminal(c.lastRun) }] as const] : [])))
/** Запуски, которые завершились с новыми данными после снимка prev (prev null — первая загрузка: прежние запуски не считаем) */
export function finishedWithData(prev: RunsSeen | null, list: readonly Connector[]): { name: string; run: CollectionRun }[] {
  if (!prev) return []
  return list.flatMap((c) => {
    const run = c.lastRun
    if (!run || !isTerminal(run) || !hasNewData(run)) return []
    const was = prev.get(c.code)
    return was && was.id === run.id && was.terminal ? [] : [{ name: c.name, run }]
  })
}

// ---------- Кнопки: можно ли действовать ----------

export type Action = 'test' | 'collect' | 'toggle' | 'configure' | 'connect'

/** Почему кнопка неактивна (title) или null — можно. Без режима администратора — «Нужен режим администратора» */
export function blockReason(c: Connector, action: Action, admin: boolean): string | null {
  if (!admin) return 'Нужен режим администратора'
  const caps = c.capabilities
  if (action === 'test' && !caps?.test) return 'У этого подключения нет проверки'
  if (action === 'collect' && !caps?.collect) return 'У этого подключения нет сбора публикаций'
  if (action === 'toggle' && !caps?.toggle) return 'У этого подключения нет сбора по расписанию'
  if (action === 'configure' && !caps?.configure) return 'У этого подключения нет настроек'
  if (action === 'connect' && !caps?.connect) return 'Подключение аккаунта здесь не нужно'
  if (!c.configured && action !== 'configure') {
    return c.code === 'olx' ? 'OLX не настроен: заявка отправлена, ключей OLX_CLIENT_ID/OLX_CLIENT_SECRET нет на сервере' : missingShort(c.missing)
  }
  if (action === 'test' && effectiveStatus(c) === 'checking') return 'Проверка уже идёт'
  return null
}

/** Не переменная .env, а файл сессии: его создаёт вход в Telegram из консоли сервера */
const SESSION_NAMES: ReadonlySet<string> = new Set(['TG_SESSION_PATH'])

/** Чего нет на сервере: env — переменные .env; session — нет авторизованной сессии Telegram (нужен вход, а не ключ) */
export function splitMissing(missing: readonly string[] | undefined): { env: string[]; session: boolean } {
  const xs = missing ?? []
  return { env: xs.filter((n) => !SESSION_NAMES.has(n)), session: xs.some((n) => SESSION_NAMES.has(n)) }
}

function missingShort(missing: readonly string[] | undefined): string {
  const { env, session } = splitMissing(missing)
  if (env.length) return `Сначала настройте подключение на сервере: ${env.join(', ')}${session ? ' и выполните вход в Telegram' : ''}`
  return session ? 'Сначала выполните вход в Telegram на сервере — авторизованной сессии нет' : 'Сначала настройте подключение на сервере'
}

/** «Нет на сервере: …» — имена переменных, не значения */
export const missingLine = (missing: readonly string[]) =>
  `Нет на сервере: ${missing.join(', ')}. Ключи задаются в .env на сервере и в браузер не передаются.`

// ---------- Настройки (Modal «Настроить») ----------

export const CONFIG_META: Record<ConfigField, { label: string; placeholder: string; max: number }> = {
  accounts: { label: 'Аккаунты', placeholder: '@tumar.group.astana', max: 50 },
  channels: { label: 'Каналы', placeholder: 't.me/channel', max: 50 },
  profiles: { label: 'Профили', placeholder: '@tumargroup', max: 20 },
  searchUrls: { label: 'URL поиска', placeholder: 'https://krisha.kz/prodazha/kvartiry/ust-kamenogorsk/', max: 5 },
  cardUrls: { label: 'Карточки объявлений', placeholder: 'https://krisha.kz/a/show/1009924986', max: 10 },
}

/** Текст поля «по одному в строке» → список: без пробелов по краям, без пустых и повторов (порядок сохраняется) */
export function parseLines(text: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of text.split(/\r?\n/)) {
    const s = raw.trim()
    if (s && !seen.has(s)) { seen.add(s); out.push(s) }
  }
  return out
}

export const joinLines = (xs: readonly unknown[] | undefined) => (xs ?? []).filter((x) => typeof x === 'string').join('\n')

/** Список из настроек подключения (только строки) */
export const configList = (c: Pick<Connector, 'config'>, f: ConfigField): string[] => {
  const v = c.config?.[f]
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

/** Что не так со списком до отправки: больше допустимого — текст; иначе null */
export function listProblem(f: ConfigField, xs: readonly string[]): string | null {
  const { label, max } = CONFIG_META[f]
  if (xs.length > max) return `${label}: не больше ${max}`
  const long = xs.find((x) => x.length > (f === 'searchUrls' || f === 'cardUrls' ? 512 : 255))
  return long ? `${label}: слишком длинное значение — ${long.slice(0, 40)}…` : null
}

/** Период в минутах вне границ подключения — текст; иначе null */
export function intervalProblem(min: number, bounds?: readonly number[] | null): string | null {
  if (!Number.isFinite(min) || !Number.isInteger(min)) return 'Период: целое число минут'
  const [lo, hi] = bounds && bounds.length === 2 ? bounds : [5, 10080]
  return min < lo || min > hi ? `Период: от ${lo} до ${hi} мин` : null
}

/** «Что собираем»: значения списков через запятую (до трёх и «ещё N»); пусто — подсказка */
export function configSummary(c: Pick<Connector, 'config' | 'configFields'>): string | null {
  if (!c.configFields?.length) return null
  const parts = c.configFields.map((f) => {
    const xs = configList(c, f)
    if (!xs.length) return null
    const head = xs.slice(0, 3).join(', ')
    return `${Object.hasOwn(CONFIG_META, f) ? CONFIG_META[f].label.toLowerCase() : f}: ${head}${xs.length > 3 ? ` и ещё ${xs.length - 3}` : ''}`
  }).filter(Boolean)
  return parts.length ? parts.join(' · ') : 'список пуст — задайте его в «Настроить»'
}

/** «Период»: раз в 30 мин / раз в час / раз в 6 ч / раз в сутки / раз в 7 сут */
export function intervalText(min: number | null | undefined): string {
  if (!min || min <= 0) return '—'
  if (min % 1440 === 0) return min === 1440 ? 'раз в сутки' : `раз в ${min / 1440} сут`
  if (min % 60 === 0) return min === 60 ? 'раз в час' : `раз в ${min / 60} ч`
  return min < 60 ? `раз в ${min} мин` : `раз в ${Math.floor(min / 60)} ч ${min % 60} мин`
}

// ---------- Счётчики и запуски (§10.5) ----------

/** «Собрано»: 12 материалов · 10 публикаций · 7 файлов */
export const collectedLine = (n: { items: number; ads: number; media: number }) =>
  `${n.items} ${plural(n.items, ['материал', 'материала', 'материалов'])} · ${n.ads} ${plural(n.ads, ['публикация', 'публикации', 'публикаций'])} · ${n.media} ${plural(n.media, ['файл', 'файла', 'файлов'])}`

export const RUN_LABEL: Record<RunStatus, string> = {
  queued: 'в очереди',
  running: 'выполняется',
  succeeded: 'успешно',
  partial: 'частично',
  failed: 'ошибка',
  access_check: 'проверка доступа на сайте',
  rate_limited: 'пауза: лимит запросов',
  not_configured: 'не настроен',
  cancelled: 'отменён',
}

export const RUN_TONE: Record<RunStatus, Tone> = {
  queued: 'blue',
  running: 'blue',
  succeeded: 'green',
  partial: 'yellow',
  failed: 'red',
  access_check: 'yellow',
  rate_limited: 'yellow',
  not_configured: 'gray',
  cancelled: 'gray',
}

export const TRIGGER_LABEL: Record<RunTrigger, string> = { schedule: 'по расписанию', manual: 'вручную', test: 'проверка подключения' }

/** Конечные статусы запуска — все, кроме «в очереди» и «выполняется» */
export const RUN_TERMINAL: ReadonlySet<RunStatus> = new Set(['succeeded', 'partial', 'failed', 'access_check', 'rate_limited', 'not_configured', 'cancelled'])
export const isTerminal = (run: Pick<CollectionRun, 'status'>) => RUN_TERMINAL.has(run.status)

export const runLabel = (run: Pick<CollectionRun, 'status' | 'statusLabel'>) =>
  run.statusLabel?.trim() || (Object.hasOwn(RUN_LABEL, run.status) ? RUN_LABEL[run.status] : run.status)

const ZERO: RunCounts = { seen: 0, new: 0, updated: 0, unchanged: 0, failed: 0, mediaSaved: 0, mediaFailed: 0 }

/** «новых 3 · изменено 1 · без изменений 20 · ошибок 0» */
export function runSummary(run: Pick<CollectionRun, 'counts'>): string {
  const c = { ...ZERO, ...run.counts }
  return `новых ${c.new} · изменено ${c.updated} · без изменений ${c.unchanged} · ошибок ${c.failed}`
}

/** Стоимость платного запуска: $0.12; меньше цента — «<$0.01» */
export const fmtUsd = (x: number) => (x > 0 && x < 0.005 ? '<$0.01' : `$${x.toFixed(2)}`)

/** «Последний запуск»: №12 · успешно · новых 3 · …; у проверки — без счётчиков; у платного — стоимость */
export function runLine(run: CollectionRun): string {
  const head = `№${run.id} · ${runLabel(run)}`
  const body = run.trigger === 'test' ? TRIGGER_LABEL.test : runSummary(run)
  return `${head} · ${body}${typeof run.costUsd === 'number' ? ` · ${fmtUsd(run.costUsd)}` : ''}`
}

/** Последние n заметок запуска (на сервере до 100) */
export const lastNotes = (run: Pick<CollectionRun, 'notes'>, n = 5) => (run.notes ?? []).slice(-n)

/** «повтор после HH:MM» или null */
export function retryLine(run: Pick<CollectionRun, 'retryAt'>, fmt: (iso: string) => string = hhmm): string | null {
  return run.retryAt ? `повтор после ${fmt(run.retryAt)}` : null
}

export const hhmm = (iso: string) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
/** Дата и время для строк карточки («Последний успех», «Следующий запуск») */
export const when = (iso: string | null | undefined) => (iso ? fmtDT(iso) : '—')

/** Новые или изменённые публикации — перечитать ленту и сообщить */
export const hasNewData = (run: Pick<CollectionRun, 'counts'>) => (run.counts?.new ?? 0) + (run.counts?.updated ?? 0) > 0

/** Тост по завершении сбора с новыми данными: «Сбор «Instagram»: новых 3, изменено 1 — событие в журнале»; без них — null */
export function runToast(name: string, run: CollectionRun): string | null {
  if (!isTerminal(run) || !hasNewData(run)) return null
  return `Сбор «${name}»: новых ${run.counts.new}, изменено ${run.counts.updated} — событие в журнале`
}

/** Тост по нескольким завершённым сборам: один — как runToast; несколько — имена и общие счётчики; без новых данных — null */
export function runsToast(items: readonly { name: string; run: CollectionRun }[]): string | null {
  const done = items.filter((x) => isTerminal(x.run) && hasNewData(x.run))
  if (done.length <= 1) return done.length ? runToast(done[0].name, done[0].run) : null
  const sum = (k: 'new' | 'updated') => done.reduce((n, x) => n + (x.run.counts?.[k] ?? 0), 0)
  return `Сбор ${done.map((x) => `«${x.name}»`).join(', ')}: новых ${sum('new')}, изменено ${sum('updated')} — события в журнале`
}

/** Источник/источника/источников */
export const sourcesWord = (n: number) => plural(n, ['источник', 'источника', 'источников'])

/** Тост «Обновить всё»: «Сбор поставлен в очередь: 2 источника, пропущено: Krisha.kz — выключен, …» */
export function collectAllToast(res: CollectAllResult, nameOf: (code: string) => string = (c) => c): string {
  const n = res.queued?.length ?? 0
  const skipped = (res.skipped ?? []).map((s) => `${nameOf(s.code)} — ${s.reason || 'пропущен'}`)
  const tail = skipped.length ? `пропущено: ${skipped.join(', ')}` : ''
  if (!n) return `Сбор не поставлен в очередь${tail ? ` — ${tail}` : ''}`
  return `Сбор поставлен в очередь: ${n} ${sourcesWord(n)}${tail ? `, ${tail}` : ''}`
}

// ---------- Анализ собранной публикации ----------

export const STAGE_LABEL: Record<string, string> = {
  media: 'загрузка файлов',
  frames: 'кадры видео',
  ocr: 'распознавание текста',
  rules: 'правила ИИ',
  llm: 'анализ ИИ (Claude)',
  events: 'запись в журнал',
}
export const stageLabel = (s?: string | null) => (s ? (Object.hasOwn(STAGE_LABEL, s) ? STAGE_LABEL[s] : s) : null)

/** Пояснение к состоянию анализа: queued «Анализ публикации в очереди», running «Идёт анализ: {этап}»,
 *  failed «Анализ не выполнен: {error} — проверьте вручную»; done — null */
export function processingNote(p: Processing | null | undefined): string | null {
  if (!p) return null
  if (p.status === 'queued') return 'Анализ публикации в очереди'
  if (p.status === 'running') return p.stage ? `Идёт анализ: ${stageLabel(p.stage)}` : 'Идёт анализ публикации'
  if (p.status === 'failed') return p.error?.trim() ? `Анализ не выполнен: ${p.error.trim()} — проверьте вручную` : 'Анализ не выполнен — проверьте вручную'
  return null
}

// ---------- Закреплённые тексты страницы «Источники» (§13.2) ----------

export const COPY = {
  offline: 'Состояние подключений хранится на сервере — в офлайн-копии недоступно (запустите ./dev.sh).',
  unavailable: 'Состояние подключений не загрузилось: нет связи с сервером или сервер без сбора публикаций.',
  workerDown: 'Воркер сбора не запущен — проверки и сбор стоят в очереди. Запуск: ./dev.sh или docker compose up -d worker.',
  adminNotConfigured: 'Администрирование подключений не настроено на сервере (SAQ_ADMIN_TOKEN)',
  adminButton: 'Режим администратора',
  adminField: 'Токен администратора',
  adminHint: 'Значение SAQ_ADMIN_TOKEN с сервера. Хранится только в этой вкладке до её закрытия.',
  adminLine: 'Настройка подключений и ручной запуск — только для администратора: токен проверяет сервер. Вход аналитика без пароля этого не даёт.',
  adminTag: 'администратор',
  needAdmin: 'Нужен режим администратора',
  stopped: 'Сбор остановлен после ошибки — запустите «Проверить подключение»',
  accessCheck: 'Сайт закрыл страницы проверкой доступа. Автоматический сбор остановлен — обход проверок не выполняется; нужен ручной шаг.',
  polling: 'Выполняется… обновляем каждые 2 с',
  contractUnverified: 'контракт не сверен',
  paid: 'платно',
  disabled: 'выключен',
  stoppedTag: 'остановлен',
  toggle: 'Сбор по расписанию',
  test: 'Проверить подключение',
  collect: 'Собрать сейчас',
  configure: 'Настроить',
  olxConnect: 'Подключить аккаунт OLX',
  olxRelink: 'Подключить заново',
  noSession: 'Нет авторизованной сессии Telegram — выполните вход на сервере (ниже). Сессия в браузер не передаётся.',
  collectAll: 'Обновить всё',
} as const

/** «Запуск №{id} · {статус}» — заголовок панели запуска */
export const runTitle = (run: Pick<CollectionRun, 'id' | 'status' | 'statusLabel'>) => `Запуск №${run.id} · ${runLabel(run)}`
