import { plain } from './plain'
// HTTP-хелперы для бэкенда FastAPI (JSON, camelCase).
export const API: string = import.meta.env.VITE_API_URL ?? '/api'

/** Ошибка запроса: status 0 — бэкенд недоступен; data — detail-объект ответа (при 409 сигнала — {message, signalId}) или всё тело */
export class ApiError extends Error {
  status: number
  data?: unknown
  constructor(status: number, message: string, data?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

export const UNREACHABLE = 'Сервер недоступен'

// Типовые ошибки валидации pydantic → по-русски; value_error — текст сервера как есть
const VALIDATION: Record<string, string> = {
  missing: 'обязательное поле',
  string_too_short: 'не может быть пустым',
  too_short: 'не может быть пустым',
  string_too_long: 'слишком длинное значение',
  too_long: 'слишком много значений',
  literal_error: 'недопустимое значение',
  enum: 'недопустимое значение',
  extra_forbidden: 'неизвестное поле',
  int_parsing: 'нужно целое число',
  int_from_float: 'нужно целое число',
  float_parsing: 'нужно число',
  bool_parsing: 'нужно «да» или «нет»',
  finite_number: 'нужно число',
}
// Поля тел запросов — как их называет интерфейс
const FIELD: Record<string, string> = {
  author: 'автор', comment: 'комментарий', text: 'текст', status: 'статус', assignee: 'ответственный',
  topics: 'темы запроса', complexId: 'ЖК', account: 'аккаунт', platform: 'площадка', url: 'ссылка',
  decision: 'решение', file: 'файл', ocr: 'текст со скриншота',
  // подключения источников и сверка 2ГИС
  enabled: 'сбор по расписанию', intervalMin: 'период', config: 'настройки', accounts: 'аккаунты', channels: 'каналы',
  profiles: 'профили', searchUrls: 'URL поиска', cardUrls: 'карточки', resultsPerPage: 'публикаций за запуск',
  downloadVideos: 'загрузка видео', query: 'запрос', candidateId: 'кандидат', targetId: 'объект', targetKind: 'вид объекта',
  apply: 'перенос координат',
  // сверка сведений аналитиком по ЖК
  doc: 'документ', value: 'значение', ref: 'номер и дата документа', source: 'источник', sourceUrl: 'ссылка',
  checkedAt: 'дата сверки', kind: 'вид', date: 'дата', subject: 'суть обращения', applicants: 'заявителей',
  collective: 'коллективное', activity: 'активность', note: 'примечание',
}
const PLACE = new Set(['body', 'query', 'path', 'header'])

interface Invalid { type?: string; loc?: (string | number)[]; msg?: string; ctx?: Record<string, unknown> }

/** Границы числа (период сбора, число публикаций): «не меньше 15» / «не больше 1440» */
const BOUND: Record<string, [string, string]> = {
  greater_than_equal: ['ge', 'не меньше'], less_than_equal: ['le', 'не больше'], greater_than: ['gt', 'больше'], less_than: ['lt', 'меньше'],
}

/** Одна ошибка валидации: «<поле>: <текст>», поле — последний строковый элемент loc */
function invalid(d: Invalid): string {
  const bound = d.type ? BOUND[d.type] : undefined
  const limit = bound && d.ctx ? d.ctx[bound[0]] : undefined
  const text = bound && (typeof limit === 'number' || typeof limit === 'string')
    ? `${bound[1]} ${limit}`
    : d.type === 'value_error' || !d.type || !VALIDATION[d.type]
    ? (d.msg ?? '').replace(/^(Value error|Assertion failed), /, '')
    : VALIDATION[d.type]
  const field = [...(d.loc ?? [])].reverse().find((x): x is string => typeof x === 'string')
  return field && !PLACE.has(field) ? `${FIELD[field] ?? field}: ${text}` : text
}

/** Текст ошибки FastAPI: detail строкой, detail.message или список ошибок валидации (422) */
export function messageOf(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) return detail.map((d: Invalid) => (d && typeof d === 'object' ? invalid(d) : '')).filter(Boolean).join('; ') || fallback
  if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') return (detail as { message: string }).message
  return fallback
}

const fallbackOf = (status: number) => (status >= 500 ? 'Внутренняя ошибка сервера' : status === 404 ? 'Не найдено' : `Ошибка ${status}`)

type ReachListener = (online: boolean) => void
const reachListeners = new Set<ReachListener>()
/** Связь с сервером по итогу каждого запроса: false — ответа нет (сеть, 502–504 прокси), true — сервер ответил,
 *  с любым статусом. Индикатор в шапке следит за ней, а не за баннером ошибки. Возвращает отписку */
export function onReach(cb: ReachListener): () => void {
  reachListeners.add(cb)
  return () => { reachListeners.delete(cb) }
}
const reach = (online: boolean) => reachListeners.forEach((cb) => cb(online))
/** Тело ответа FastAPI: {detail: "..."} или {detail: {...}} — прокси так не отвечает */
const fromApi = (body: unknown) => {
  const detail = (body as { detail?: unknown } | null)?.detail
  return !!body && typeof body === 'object' && ((typeof detail === 'string' && detail.trim() !== '') || (!!detail && typeof detail === 'object'))
}
const unreachable = (data: unknown) => { reach(false); return new ApiError(0, UNREACHABLE, data) }

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response
  let text: string
  try {
    r = await fetch(`${API}${path}`, init)
    text = await r.text()
  } catch (e) {
    throw unreachable({ cause: e instanceof Error ? e.message : String(e) })
  }
  let body: unknown = null
  if (text) {
    try { body = JSON.parse(text) } catch { body = text }
  }
  // 502/503/504 отдаёт прокси (vite, nginx), когда сам API не отвечает — для интерфейса это «нет связи».
  // Ответ самого API с русским detail (503 «не настроено на сервере», 502 «2ГИС не ответил») — ответ сервера, его текст показываем
  if (r.status >= 502 && r.status <= 504 && !fromApi(body)) throw unreachable({ status: r.status })
  reach(true)
  if (!r.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail
    throw new ApiError(r.status, messageOf(body, fallbackOf(r.status)), detail && typeof detail === 'object' ? detail : body)
  }
  return body as T
}

/** Дополнительные заголовки запроса — например, токен администратора (lib/admin.ts) только для маршрутов администратора */
export type ExtraHeaders = Record<string, string>

const json = (method: string, body: unknown, headers?: ExtraHeaders): RequestInit =>
  ({ method, headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const apiGet = <T>(path: string, headers?: ExtraHeaders) => apiFetch<T>(path, headers ? { headers } : undefined)
export const apiPost = <T>(path: string, body: unknown, headers?: ExtraHeaders) => apiFetch<T>(path, json('POST', body, headers))
export const apiPatch = <T>(path: string, body: unknown, headers?: ExtraHeaders) => apiFetch<T>(path, json('PATCH', body, headers))
/** DELETE с JSON-телом (автор и причина — например, отмена решения аналитика) */
export const apiDelete = <T>(path: string, body: unknown, headers?: ExtraHeaders) => apiFetch<T>(path, json('DELETE', body, headers))
/** multipart/form-data: заголовок с границей ставит сам браузер */
export const apiUpload = <T>(path: string, form: FormData, headers?: ExtraHeaders) =>
  apiFetch<T>(path, { method: 'POST', body: form, ...(headers ? { headers } : {}) })

/** Текст любой ошибки для сообщения пользователю — без имён переменных, команд, путей и адресов API сервера (lib/plain.ts) */
export const errorText = (e: unknown) => plain(e instanceof Error ? e.message : String(e))

// ---------- Новые функции сервера (сбор публикаций, 2ГИС, файлы): сервер старой версии их не знает ----------

/** Маршрута нет на этом сервере (бэкенд старой версии): 404 FastAPI без русского detail («Not Found»), 404 без тела
 *  или с HTML (статический хостинг без API), 405 — метод не поддерживается */
export const isRouteMissing = (e: unknown) =>
  e instanceof ApiError
  && ((e.status === 404 && (e.message === 'Not Found' || e.data == null || typeof e.data === 'string')) || e.status === 405)

/** Функция сейчас недоступна: нет связи с сервером (status 0) или маршрута нет на сервере */
export const isUnavailable = (e: unknown) => e instanceof ApiError && (e.status === 0 || isRouteMissing(e))

export const NO_ROUTE = 'На сервере нет этой функции — обновите бэкенд до версии со сбором публикаций'

/** Чтение необязательных данных: null — недоступно (нет связи, маршрута нет, ответ не того вида — например, HTML статики);
 *  ошибки сервера с русским detail (404 «Публикация не найдена», 500) пробрасываются */
export async function orNull<T>(p: Promise<T>, valid: (x: unknown) => boolean = () => true): Promise<T | null> {
  try {
    const got = await p
    return valid(got) ? got : null
  } catch (e) {
    if (isUnavailable(e)) return null
    throw e
  }
}

/** Действие: «Not Found» старого сервера → понятный текст NO_ROUTE (status тот же); остальные ошибки как есть */
export async function orNoRoute<T>(p: Promise<T>): Promise<T> {
  try {
    return await p
  } catch (e) {
    if (isRouteMissing(e)) throw new ApiError((e as ApiError).status, NO_ROUTE, (e as ApiError).data)
    throw e
  }
}

const SAFE_PROTOCOL = new Set(['http:', 'https:', 'blob:'])
/** Управляющие символы: браузер выбрасывает их при разборе адреса («java\tscript:» → javascript:) — такой адрес не принимаем */
// oxlint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/
/** База для разбора относительных путей: схему пути сайта даёт она (https:), а сам путь возвращается как есть */
const PARSE_BASE = 'https://media.invalid/'

/** Адрес файла для <img>/<video>/<a href> при заданной базе API: /api/media/… → base + /media/…; http(s), blob: (локальный
 *  предпросмотр этой вкладки) и data:image|video — как есть; пути сайта (/img/…, /snapshot-media/… офлайн-копии, /brands/…) — без изменений;
 *  другие схемы (javascript:, file:…) и адреса с управляющими символами — undefined. Схему определяет разбор URL, как в браузере */
export function mediaUrl(u: string | null | undefined, base: string): string | undefined {
  const s = u?.trim()
  if (!s || CONTROL.test(s)) return undefined
  if (s.startsWith('/api/')) return base.replace(/\/+$/, '') + s.slice(4)
  let protocol: string
  try {
    protocol = new URL(s, PARSE_BASE).protocol
  } catch {
    return undefined
  }
  if (SAFE_PROTOCOL.has(protocol)) return s
  return protocol === 'data:' && /^data:(image|video)\//i.test(s) ? s : undefined
}

/** Адрес файла публикации для <img src>/<video src>: серверные /api/media/… учитывают VITE_API_URL */
export const mediaSrc = (u?: string | null): string | undefined => mediaUrl(u, API)
