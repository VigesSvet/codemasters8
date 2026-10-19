// Горячие точки рекламы (переключатель на главной карте): где больше публикаций с предложением оплаты у ЖК без подтверждённого
// основания по открытым данным. Это контекст для планирования проверок, а не вывод о нарушении: публикации к ЖК отнесены сервером
// «Проверки по рекламе» (в том числе по гипотезам ИИ), основание — сведения источника (permit), не юридический вывод.
import type { Ad, Complex, LlmSignalType } from '../data/types'
import { adSignals } from './risk'
import { hasPoint, statusOf } from './screening'

/** Признаки приёма денег — как у «Проверки по рекламе»: старт продаж, бронь, ДДУ, рассрочка, первоначальный взнос */
export const MONEY_SIGNALS: readonly LlmSignalType[] = ['presale', 'booking', 'ddu', 'installment', 'down_payment']

/** Публикация с предложением оплаты: проверенный ответ Claude, без него — словарь модели риска (markers.money).
 *  Не считаются отклонённые публикации и решение аналитика «это не предложение внести деньги» */
export function isPaymentOffer(a: Pick<Ad, 'text' | 'ocr' | 'status' | 'termsReview' | 'llm'>): boolean {
  if (a.status === 'dismissed' || a.termsReview === 'rejected') return false
  if (a.llm?.status === 'done') return a.llm.saleSignals.some((s) => MONEY_SIGNALS.includes(s.type))
  return adSignals(a).funds.length > 0
}

/** Район: поле district, а без него — ячейка сетки ~1,5 × 1,5 км по координатам (в данных районов нет) */
export const CELL = { lat: 0.0135, lng: 0.021 }

export interface HotPoint {
  id: string
  name: string
  lat: number
  lng: number
  /** Публикаций с признаком приёма денег (свежих, окно «Проверки по рекламе») */
  offers: number
  /** Основание по открытым данным или сверке есть (permit = permitted) */
  basis: boolean
  /** «Проверка по рекламе»: требует проверки или подозрение */
  flagged: boolean
}

export interface HotArea {
  key: string
  /** Район (поле district) — как есть */
  district: string | null
  /** Самая частая улица адресов района или ячейки («пр. Есенберлина»); null — улицы в адресах нет */
  street: string | null
  /** У нескольких ячеек одна улица — какая это часть по широте: n — северная, c — средняя, s — южная; иначе null */
  part: 'n' | 'c' | 's' | null
  lat: number
  lng: number
  complexes: number
  offers: number
  /** Из них — у ЖК без подтверждённого основания */
  offersNoBasis: number
  flagged: number
  names: string[]
}

/** Публикаций с предложением оплаты у ЖК: счётчик сервера (screening.counts.concerning), без него — локальный (fallback) */
export const offersOf = (c: Complex, fallback?: (c: Complex) => number) => c.screening?.counts?.concerning ?? fallback?.(c) ?? 0

/** Точки для карты — только ЖК с координатами и хотя бы одной публикацией с предложением оплаты или отметкой проверки */
export function hotPoints(complexes: readonly Complex[], fallback?: (c: Complex) => number): HotPoint[] {
  return complexes.flatMap((c) => {
    if (!hasPoint(c)) return []
    const s = statusOf(c)
    const p: HotPoint = { id: c.id, name: c.name, lat: c.lat, lng: c.lng, offers: offersOf(c, fallback), basis: c.permit?.status === 'permitted', flagged: s === 'check' || s === 'suspect' }
    return p.offers > 0 || p.flagged ? [p] : []
  })
}

const STREET = /(?:^|[\s,])(проспект|пр-т|пр\.|улица|ул\.|бульвар|б-р|переулок|пер\.)\s*([^,]+)/u
/** Имена в названиях улиц, которые в адресах то пишут, то нет */
const GIVEN = /^(?:Илияса|Каныша)\s+/u
/** Улица адреса в коротком виде: «проспект Илияса Есенберлина, 38/3» и «пр. И. Есенберлина, д. 92» → «пр. Есенберлина»;
 *  нет улицы — null */
export function streetOf(address: string | null | undefined): string | null {
  const m = STREET.exec((address ?? '').replace(/г\.\s*Усть-Каменогорск,?\s*/u, ''))
  if (!m) return null
  const kind = /^пр/u.test(m[1]) ? 'пр.' : /^б/u.test(m[1]) ? 'б-р' : /^пер/u.test(m[1]) ? 'пер.' : 'ул.'
  const name = m[2].split(/\s+и\s+/u)[0].replace(/^(?:[А-ЯЁа-яё]\.?\s+)+/u, '').replace(GIVEN, '').trim()
  return name ? `${kind} ${name}` : null
}

const cellOf = (lat: number, lng: number) => `${Math.floor(lat / CELL.lat)}:${Math.floor(lng / CELL.lng)}`

/** Районы по убыванию: публикации у ЖК без подтверждённого основания, затем отметки проверки, затем все публикации */
export function hotAreas(complexes: readonly Complex[], fallback?: (c: Complex) => number): HotArea[] {
  const groups = new Map<string, Complex[]>()
  for (const c of complexes) {
    if (!hasPoint(c) && !c.district) continue
    const key = c.district ? `d:${c.district}` : `g:${cellOf(c.lat as number, c.lng as number)}`
    groups.set(key, [...(groups.get(key) ?? []), c])
  }
  const areas = [...groups].map(([key, cs]): HotArea => {
    const pts = cs.filter((c) => hasPoint(c))
    const streets = new Map<string, number>()
    for (const c of cs) { const s = streetOf(c.address); if (s) streets.set(s, (streets.get(s) ?? 0) + 1) }
    const street = [...streets].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'ru'))[0]?.[0]
    let offers = 0, offersNoBasis = 0, flagged = 0
    for (const c of cs) {
      const n = offersOf(c, fallback), s = statusOf(c)
      offers += n
      if (c.permit?.status !== 'permitted') offersNoBasis += n
      if (s === 'check' || s === 'suspect') flagged += 1
    }
    const avg = (f: (c: Complex) => number) => (pts.length ? pts.reduce((s, c) => s + f(c), 0) / pts.length : 0)
    return {
      key, district: cs[0].district || null, street: street ?? null, part: null,
      lat: avg((c) => c.lat as number), lng: avg((c) => c.lng as number),
      complexes: cs.length, offers, offersNoBasis, flagged, names: cs.map((c) => c.name).sort((a, b) => a.localeCompare(b, 'ru')),
    }
  })
  // одна улица у нескольких ячеек (длинный проспект) — подпись части: север, центр, юг
  const byStreet = new Map<string, HotArea[]>()
  for (const a of areas) if (!a.district && a.street) byStreet.set(a.street, [...(byStreet.get(a.street) ?? []), a])
  for (const xs of byStreet.values()) {
    if (xs.length < 2) continue
    xs.sort((a, b) => b.lat - a.lat).forEach((a, n) => { a.part = n === 0 ? 'n' : n === xs.length - 1 ? 's' : 'c' })
  }
  return areas
    .filter((a) => a.offers > 0 || a.flagged > 0)
    .sort((a, b) => b.offersNoBasis - a.offersNoBasis || b.flagged - a.flagged || b.offers - a.offers || a.key.localeCompare(b.key))
}

/** Круги для карты (MapView heat): w — публикации с предложением оплаты (у ЖК только с отметкой проверки — 1, чтобы точка была
 *  видна), red — основания по открытым данным нет, amber — есть */
export const heatPoints = (complexes: readonly Complex[], fallback?: (c: Complex) => number) =>
  hotPoints(complexes, fallback).map((p) => ({ id: p.id, lng: p.lng, lat: p.lat, w: Math.max(1, p.offers), tone: p.basis ? 'amber' as const : 'red' as const }))
