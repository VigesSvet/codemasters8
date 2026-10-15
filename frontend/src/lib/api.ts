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
}
// Поля тел запросов — как их называет интерфейс
const FIELD: Record<string, string> = {
  author: 'автор', comment: 'комментарий', text: 'текст', status: 'статус', assignee: 'ответственный',
  topics: 'темы запроса', complexId: 'ЖК', account: 'аккаунт', platform: 'площадка', url: 'ссылка',
  decision: 'решение', file: 'файл', ocr: 'текст со скриншота',
}
const PLACE = new Set(['body', 'query', 'path', 'header'])

interface Invalid { type?: string; loc?: (string | number)[]; msg?: string }

/** Одна ошибка валидации: «<поле>: <текст>», поле — последний строковый элемент loc */
function invalid(d: Invalid): string {
  const text = d.type === 'value_error' || !d.type || !VALIDATION[d.type]
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
  // 502/503/504 отдаёт прокси (vite, nginx), когда сам API не отвечает — для интерфейса это «нет связи»
  if (r.status >= 502 && r.status <= 504) throw unreachable({ status: r.status })
  reach(true)
  let body: unknown = null
  if (text) {
    try { body = JSON.parse(text) } catch { body = text }
  }
  if (!r.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail
    throw new ApiError(r.status, messageOf(body, fallbackOf(r.status)), detail && typeof detail === 'object' ? detail : body)
  }
  return body as T
}

const json = (method: string, body: unknown): RequestInit =>
  ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const apiGet = <T>(path: string) => apiFetch<T>(path)
export const apiPost = <T>(path: string, body: unknown) => apiFetch<T>(path, json('POST', body))
export const apiPatch = <T>(path: string, body: unknown) => apiFetch<T>(path, json('PATCH', body))
/** multipart/form-data: заголовок с границей ставит сам браузер */
export const apiUpload = <T>(path: string, form: FormData) => apiFetch<T>(path, { method: 'POST', body: form })

/** Текст любой ошибки для сообщения пользователю */
export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))
