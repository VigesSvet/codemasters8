// Сверка адреса в 2ГИС (integration-spec §12.5, §13.3; план §11): кандидаты — подсказка аналитику, ничего не принимается само.
// Порядок координат: у 2ГИС {lat, lon}, у ЖК {lat, lng}, у карты (MapView, MapLibre) — [lng, lat].
// 2ГИС запрашивается только по кнопке «Сверить адрес»: при открытии карточки читаем сохранённые сверки и квоту.
import { fmtDT } from '../components/ui'
import type { GeoAcceptIn, GeoCandidate, GeoCheck, GeoCheckIn, GeoRejectIn, GeoStatus, GeoTargetKind } from '../data/types'
import { adminCall } from './admin'
import { apiGet, apiPost, orNoRoute, orNull } from './api'

const isObj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x)

// ---------- Запросы ----------

/** Квота и кэш 2ГИС; null — недоступно (нет связи, сервер без 2ГИС) */
export const getGeoStatus = () => orNull(apiGet<GeoStatus>('/geo/status'), (x) => isObj(x) && typeof x.configured === 'boolean')

/** Сохранённые сверки объекта (новые первыми) — без запроса к 2ГИС; null — недоступно */
export const getGeoChecks = (kind: GeoTargetKind, id: string) =>
  orNull(apiGet<GeoCheck[]>(`/geo/checks?${new URLSearchParams({ targetKind: kind, targetId: id })}`), Array.isArray)

/** «Сверить адрес»: 201 — новый запрос к 2ГИС, 200 — из кэша (cached). force — «Обновить запрос» мимо кэша: только
 *  администратор, токен уходит лишь в этом случае. 429/503/502 — текст сервера (лимит, не настроен, 2ГИС не ответил) */
export function runGeoCheck(body: GeoCheckIn): Promise<GeoCheck> {
  // пустой запрос — сервер подставит адрес объекта
  const payload: GeoCheckIn = { ...body, query: body.query?.trim() || undefined }
  return body.force
    ? adminCall((h) => apiPost<GeoCheck>('/geo/checks', payload, h))
    : orNoRoute(apiPost<GeoCheck>('/geo/checks', payload))
}

/** Принять кандидата; apply — перенести координаты в карточку ЖК (у объекта открытых данных нельзя) */
export const acceptGeo = (id: number, body: GeoAcceptIn) =>
  orNoRoute(apiPost<GeoCheck>(`/geo/checks/${id}/accept`, { ...body, comment: body.comment?.trim() || undefined }))

/** «Отклонить всех» */
export const rejectGeo = (id: number, body: GeoRejectIn) =>
  orNoRoute(apiPost<GeoCheck>(`/geo/checks/${id}/reject`, { ...body, comment: body.comment?.trim() || undefined }))

// ---------- Координаты ----------

/** Точка ЖК или кандидата: {lat, lng} или {lat, lon} */
export type GeoPoint = { lat: number; lng: number } | { lat: number; lon: number }

const lngOf = (p: GeoPoint) => ('lng' in p ? p.lng : p.lon)

/** Координаты похожи на настоящие: числа в границах; (0, 0) — «нет координат» */
export function validPoint(p: Partial<{ lat: unknown; lng: unknown; lon: unknown }> | null | undefined): p is GeoPoint {
  if (!p) return false
  const lat = p.lat, lng = p.lng ?? p.lon
  return typeof lat === 'number' && typeof lng === 'number' && Number.isFinite(lat) && Number.isFinite(lng)
    && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 && !(lat === 0 && lng === 0)
}

/** Кандидат 2ГИС → точка как у ЖК {lat, lng} (lon → lng) */
export const candidatePoint = (c: Pick<GeoCandidate, 'lat' | 'lon'>) => ({ lat: c.lat, lng: c.lon })

/** Для карты (MapLibre): [lng, lat] */
export const toLngLat = (p: GeoPoint): [number, number] => [lngOf(p), p.lat]

/** Расстояние по большому кругу, м */
export function haversineM(a: GeoPoint, b: GeoPoint): number {
  const R = 6371008.8
  const rad = (d: number) => (d * Math.PI) / 180
  const dLat = rad(b.lat - a.lat)
  const dLng = rad(lngOf(b) - lngOf(a))
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)))
}

/** «110 м», «1,2 км», «12 км» */
export function fmtDist(m: number): string {
  if (!Number.isFinite(m) || m < 0) return '—'
  if (Math.round(m) < 1000) return `${Math.round(m)} м`
  const km = Math.round(m / 100) / 10
  return km < 10 ? `${km.toFixed(1).replace('.', ',')} км` : `${Math.round(m / 1000)} км`
}

/** «49.887791, 82.610139» — шесть знаков, как в журнале */
export const fmtPoint = (p: GeoPoint) => `${p.lat.toFixed(6)}, ${lngOf(p).toFixed(6)}`

/** Расстояние кандидата до точки в карточке: с сервера, а без него — по координатам; null — точки нет */
export function candidateDistance(c: GeoCandidate, point?: GeoPoint | null): number | null {
  if (typeof c.distanceM === 'number' && Number.isFinite(c.distanceM)) return c.distanceM
  return validPoint(point) && validPoint(c) ? haversineM(point, c) : null
}

// ---------- Кандидаты ----------

export const PRECISION_LABEL: Record<string, string> = {
  exact: 'точный адрес',
  number: 'отличается литера/корпус',
  near: 'ближайший дом',
  street: 'только улица',
}

/** Точность адреса по-русски; other и неизвестное — «неточно» */
export const precisionLabel = (p?: string | null) => (p && Object.hasOwn(PRECISION_LABEL, p) ? PRECISION_LABEL[p] : 'неточно')

/** Точное совпадение дома (не «8/3а» вместо «8/3») */
export const isExact = (c: Pick<GeoCandidate, 'precision'>) => c.precision === 'exact'

/** Заголовок кандидата: название, полный адрес отдельно */
export const candidateTitle = (c: Pick<GeoCandidate, 'name' | 'fullName' | 'addressName'>) => c.name || c.fullName || c.addressName || 'Без названия'

/** Строка кандидата: «точный адрес · 49.888730, 82.610583 · в 110 м от точки в карточке» */
export function candidateLine(c: GeoCandidate, point?: GeoPoint | null): string {
  const d = candidateDistance(c, point)
  return [c.purpose, precisionLabel(c.precision), fmtPoint(candidatePoint(c)), d === null ? null : `в ${fmtDist(d)} от точки в карточке`]
    .filter(Boolean).join(' · ')
}

/** Кандидат, которого выбрал аналитик */
export const chosenOf = (check: GeoCheck) => check.candidates.find((c) => c.id === check.chosenId) ?? null

// ---------- Подписи панели (§13.3) ----------

/** Запрос по умолчанию — как на сервере: «Казахстан, Усть-Каменогорск, {адрес}» */
export const defaultGeoQuery = (address: string) => `Казахстан, Усть-Каменогорск, ${address.trim()}`

const count = (n: number, limit?: number | null) => (typeof limit === 'number' ? `${n} из ${limit}` : `${n}`)

/** «Запросов 2ГИС по ключу: 3 из 1000 · сегодня 1 из 50» */
export const quotaLine = (s: GeoStatus) =>
  `Запросов 2ГИС по ключу: ${count(s.used, s.limit)} · сегодня ${count(s.usedToday, s.dailyLimit)}`

/** «Кандидаты от {когда} (кэш до {когда})»; удалены по сроку — просьба сверить заново; без времени — null */
export function cacheLine(check: GeoCheck, fmt: (iso: string) => string = fmtDT): string | null {
  if (check.purged) return GEO_COPY.purged
  if (!check.fetchedAt) return null
  return `Кандидаты от ${fmt(check.fetchedAt)}${check.expiresAt ? ` (кэш до ${fmt(check.expiresAt)})` : ''}`
}

/** Решение по сверке: «Принято: {адрес} — {кто}, {когда}, координаты перенесены|не менялись»; ждёт решения — null */
export function decisionLine(check: GeoCheck, fmt: (iso: string) => string = fmtDT): string | null {
  const by = [check.decidedBy, check.decidedAt ? fmt(check.decidedAt) : null].filter(Boolean).join(', ')
  if (check.status === 'accepted') {
    const c = chosenOf(check)
    const what = c ? c.fullName || c.name : `объект 2ГИС ${check.chosenId ?? '—'}`
    return `Принято: ${what}${by ? ` — ${by}` : ''}${check.applied ? ', координаты перенесены' : ', координаты не менялись'}`
  }
  if (check.status === 'rejected') return `Кандидаты отклонены${by ? ` — ${by}` : ''}`
  if (check.status === 'not_found') return '2ГИС не нашёл адрес — уточните запрос'
  return null
}

/** Ждёт решения аналитика: есть кандидаты и они не удалены по сроку */
export const awaitingDecision = (check: GeoCheck) => check.status === 'candidates' && !check.purged && check.candidates.length > 0

export const GEO_COPY = {
  title: 'Сверка адреса в 2ГИС',
  run: 'Сверить адрес',
  force: 'Обновить запрос',
  offline: 'Сверка выполняется на сервере — в офлайн-копии недоступна (запустите ./dev.sh)',
  unavailable: 'Сверка адреса недоступна: нет связи с сервером или сервер без 2ГИС',
  notConfigured: '2ГИС не настроен на сервере (DGIS_API_KEY)',
  purged: 'Кандидаты удалены по сроку хранения — выполните сверку заново',
  recommended: 'рекомендуется',
  accept: 'Принять',
  rejectAll: 'Отклонить всех',
  acceptTitle: 'Принять кандидата 2ГИС?',
  apply: 'Перенести координаты в карточку ЖК',
} as const

/** Строки окна «Принять кандидата 2ГИС?»: «Сейчас в карточке: …» → «2ГИС: … (110 м)» */
export function acceptLines(c: GeoCandidate, point?: GeoPoint | null): { now: string; next: string } {
  const d = candidateDistance(c, point)
  return {
    now: `Сейчас в карточке: ${validPoint(point) ? fmtPoint(point) : 'координат нет'}`,
    next: `2ГИС: ${fmtPoint(candidatePoint(c))}${d === null ? '' : ` (${fmtDist(d)})`}`,
  }
}
