// Мобильный режим выезда (/field): ближайшие ЖК по геолокации и геометка снимка площадки.
// Снимок уходит тем же POST /api/complexes/{id}/photos, что окно «Добавить снимок площадки» карточки ЖК (R6, Фото-мониторинг);
// геометка, время и расстояние до точки ЖК — в примечании снимка (схема БД и ответы API не меняются)
import type { Complex } from '../data/types'
import { fmtPoint, haversineM } from './geo'
import { hasPoint } from './screening'

/** Дальше этого от точки ЖК снимок, скорее всего, сделан не на его площадке — предупреждение перед загрузкой */
export const FAR_M = 300

export interface Here { lat: number; lng: number; /** точность, м */ acc?: number | null }

/** ЖК с координатами по удалённости от точки, ближайшие первыми; без точки — по названию */
export function nearest<T extends Pick<Complex, 'lat' | 'lng' | 'name'>>(xs: readonly T[], here: Here | null): { c: T; m: number | null }[] {
  const rows = xs.filter((c) => hasPoint(c)).map((c) => ({ c, m: here ? haversineM(here, { lat: c.lat as number, lng: c.lng as number }) : null }))
  return rows.sort((a, b) => (a.m != null && b.m != null ? a.m - b.m : a.c.name.localeCompare(b.c.name, 'ru')))
}

/** Расстояние от точки ЖК до места снимка, м; null — у ЖК нет координат или геометки нет */
export function shotDistance(c: Pick<Complex, 'lat' | 'lng'>, at: Here | null): number | null {
  if (!at || !hasPoint(c)) return null
  return haversineM(at, { lat: c.lat as number, lng: c.lng as number })
}

export const isFar = (m: number | null) => m != null && m > FAR_M

/** «19.10.2026 14:32 (UTC+5)» — время Усть-Каменогорска, как даты снимков в журнале */
export function localStamp(iso: string): string {
  const d = new Date(Date.parse(iso) + 5 * 3600e3)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getUTCDate())}.${p(d.getUTCMonth() + 1)}.${d.getUTCFullYear()} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} (UTC+5)`
}

/** Примечание снимка: геометка, точность, расстояние до точки ЖК, время съёмки и комментарий — по-русски, как данные журнала.
 *  Не длиннее 2000 символов (предел примечания на сервере): комментарий обрезается */
export function geotagNote({ at, takenAt, m, comment }: { at: Here | null; takenAt: string; m: number | null; comment?: string }): string {
  const parts = ['Выезд (мобильный режим)']
  if (at) {
    parts.push(`геометка ${fmtPoint(at)}${at.acc != null && Number.isFinite(at.acc) ? ` (±${Math.round(at.acc)} м)` : ''}`)
    if (m != null) parts.push(`${Math.round(m)} м от точки ЖК${m > FAR_M ? ` — дальше ${FAR_M} м` : ''}`)
  } else parts.push('геометки нет (геолокация недоступна)')
  parts.push(`снято ${localStamp(takenAt)}`)
  const head = parts.join('; ')
  const tail = comment?.trim()
  if (!tail) return head
  const room = 2000 - head.length - '. Комментарий: '.length
  return `${head}. Комментарий: ${tail.length > room ? `${tail.slice(0, Math.max(0, room - 1))}…` : tail}`
}
