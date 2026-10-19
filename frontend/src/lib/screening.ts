// «Проверка по рекламе» (сервер: app.screening): видно, где застройщики и ЖК без замечаний, а где подозрение и нужна особая
// проверка. Это предварительная автоматическая оценка по рекламе и открытым данным — не вывод о нарушении: рядом с ней
// всегда виден дисклеймер. Формальный риск (балл и зона, ТЗ §5) — отдельная модель; здесь только подписи, порядок и фильтры.
import type { Complex, DeveloperScreening, Permit, PermitStatus, Screening, ScreeningBasis, ScreeningCounts, ScreeningReason, ScreeningStatus, ScreeningVariant } from '../data/types'
import { SIGNAL_LABEL } from './llm'

export const SCREENING_STATUSES: readonly ScreeningStatus[] = ['ok', 'check', 'suspect', 'unknown']
/** Порядок «подозрение первыми»: подозрение, требует проверки, нет данных, без замечаний */
export const SCREENING_ORDER: readonly ScreeningStatus[] = ['suspect', 'check', 'unknown', 'ok']

/** Подписи ЖК — как у сервера (сервер присылает и свою label: показываем её, эти — запасные и для фильтров) */
export const SCREENING_LABEL: Record<ScreeningStatus, string> = {
  ok: 'В реестре — замечаний нет',
  check: 'Требует проверки',
  suspect: 'Подозрение: продажа без разрешения',
  unknown: 'Нет данных для оценки',
}
/** Подписи застройщика */
export const DEV_SCREENING_LABEL: Record<ScreeningStatus, string> = {
  ok: 'Все ЖК в реестре — замечаний нет',
  check: 'Требует проверки',
  suspect: 'Подозрение: есть ЖК с продажей без разрешения',
  unknown: 'Нет данных для оценки',
}
/** Чипы фильтра (множественное число): «Требуют проверки»; «без замечаний» — в реестре, сданные и проверенные аналитиком */
export const SCREENING_FILTER_LABEL: Record<ScreeningStatus, string> = {
  ok: 'Без замечаний',
  check: 'Требуют проверки',
  suspect: 'Подозрение',
  unknown: 'Нет данных',
}
/** Короткие подписи — счётчики, подсказки меток */
export const SCREENING_SHORT: Record<ScreeningStatus, string> = {
  ok: 'без замечаний',
  check: 'требует проверки',
  suspect: 'подозрение',
  unknown: 'нет данных',
}
/** Подсказка статуса в фильтрах и сводках: что в него входит */
export const SCREENING_HINT: Record<ScreeningStatus, string> = {
  ok: 'Без замечаний: в реестре, дом сдан или проверено аналитиком',
  check: SCREENING_LABEL.check,
  suspect: SCREENING_LABEL.suspect,
  unknown: SCREENING_LABEL.unknown,
}
/** Одна строка легенды на все зелёные метки: в реестре, сдан или проверено аналитиком */
export const OK_LEGEND = 'Зелёный ✓ — в реестре, сдан или проверено аналитиком'
/** Строки легенды карты рядом со знаком метки */
export const LEGEND_LABEL: Record<ScreeningStatus, string> = {
  ok: 'В реестре, сдан или проверено аналитиком — замечаний нет',
  check: SCREENING_LABEL.check,
  suspect: SCREENING_LABEL.suspect,
  unknown: SCREENING_LABEL.unknown,
}

// ---------- ok по решению аналитика: «Дом сдан», «Проверено» ----------

export const SCREENING_VARIANTS: readonly ScreeningVariant[] = ['commissioned', 'analyst']
/** Подписи — как у сервера (screening.DECISION_LABELS) */
export const VARIANT_LABEL: Record<ScreeningVariant, string> = { commissioned: 'Дом сдан — замечаний нет', analyst: 'Проверено — замечаний нет' }
/** Короткие подписи — метки на фото, в списках и на карте */
export const VARIANT_SHORT: Record<ScreeningVariant, string> = { commissioned: 'сдан', analyst: 'проверено' }

/** Вариант ok по решению аналитика; у других статусов, без решения и у сервера старой версии — null */
export function variantOf(s?: Pick<Screening, 'status' | 'variant'> | null): ScreeningVariant | null {
  const v = s?.status === 'ok' ? s.variant : null
  return v && (SCREENING_VARIANTS as readonly string[]).includes(v) ? v : null
}
/** Короткая подпись статуса: «сдан» / «проверено» у решения аналитика, иначе — по статусу */
export const shortOf = (s: Pick<Screening, 'status' | 'variant'>) => {
  const v = variantOf(s)
  return v ? VARIANT_SHORT[v] : SCREENING_SHORT[s.status]
}

export interface DecidedCounts { commissioned: number; analyst: number; reopened: number }
/** Решения аналитика по загруженным ЖК — как stats.screening.decisions: ok по «сдан» и «проверено», снятые новой рекламой */
export function decidedCounts(xs: readonly { screening?: Pick<Screening, 'status' | 'variant'> | null; decision?: { status?: string } | null }[]): DecidedCounts {
  const out: DecidedCounts = { commissioned: 0, analyst: 0, reopened: 0 }
  for (const x of xs) {
    const v = variantOf(x.screening)
    if (v) out[v] += 1
    if (x.decision?.status === 'reopened') out.reopened += 1
  }
  return out
}
/** Из «без замечаний» — по решению аналитика: «сдан — 2 · проверено — 1»; таких нет — '' */
export function decidedLine(c?: DecidedCounts | null): string {
  if (!c) return ''
  return SCREENING_VARIANTS.filter((v) => c[v] > 0).map((v) => `${VARIANT_SHORT[v]} — ${c[v]}`).join(' · ')
}

export type ScreeningTone = 'green' | 'yellow' | 'red' | 'gray'
/** Цвет: без замечаний — зелёный, проверить — янтарный, подозрение — красный, нет данных — серый */
export const SCREENING_TONE: Record<ScreeningStatus, ScreeningTone> = { ok: 'green', check: 'yellow', suspect: 'red', unknown: 'gray' }
/** Знак на метке карты и в значке */
export const SCREENING_GLYPH: Record<ScreeningStatus, string> = { ok: '✓', check: '?', suspect: '!', unknown: '—' }

export const DISCLAIMER = 'Предварительная автоматическая оценка по рекламе и открытым данным — не вывод о нарушении'
export const DISCLAIMER_SHORT = 'Предварительная оценка — не вывод о нарушении'
/** Сервер старой версии: оценки нет в данных */
export const NO_SCREENING = 'Проверка по рекламе доступна с сервером'

export const BASIS_LABEL: Record<ScreeningBasis, string> = { hypothesis: 'по гипотезам ИИ', confirmed: 'по привязкам аналитика' }
export const BASIS_HINT: Record<ScreeningBasis, string> = {
  hypothesis: 'Публикации отнесены к ЖК гипотезами ИИ — аналитик их ещё не подтвердил',
  confirmed: 'Публикации привязаны к ЖК аналитиком; основание — по открытым данным или сверке',
}

export const isScreeningStatus = (x: unknown): x is ScreeningStatus => typeof x === 'string' && (SCREENING_STATUSES as readonly string[]).includes(x)

/** Статус оценки сущности; null — оценки нет (сервер старой версии) */
export const statusOf = (x?: { screening?: { status?: unknown } | null } | null): ScreeningStatus | null => {
  const s = x?.screening?.status
  return isScreeningStatus(s) ? s : null
}

/** Подпись: серверная label, а без неё — наша (у решения аналитика — его подпись) */
export function labelOf(s: (Pick<Screening, 'status' | 'label'> & { variant?: ScreeningVariant | null }) | null | undefined, dev = false): string {
  if (!s) return ''
  const v = dev ? null : variantOf(s)
  return s.label?.trim() || (v ? VARIANT_LABEL[v] : (dev ? DEV_SCREENING_LABEL : SCREENING_LABEL)[s.status])
}

/** Есть ли оценка хотя бы у одного ЖК — сервер её считает (офлайн-копия — как он посчитал) */
export const anyScreening = (xs: readonly { screening?: unknown }[]) => xs.some((x) => statusOf(x as { screening?: { status?: unknown } }) !== null)

/** Место в порядке «подозрение первыми»; без оценки — в конце */
export const screeningRank = (s?: ScreeningStatus | null) => (s ? SCREENING_ORDER.indexOf(s) : SCREENING_ORDER.length)

/** Сравнение для сортировки «подозрение первыми»: статус, затем свежие публикации с признаком приёма денег, затем вся реклама */
export function compareScreening(a?: Pick<Screening, 'status' | 'counts'> | null, b?: Pick<Screening, 'status' | 'counts'> | null): number {
  return screeningRank(a?.status) - screeningRank(b?.status)
    || (b?.counts?.concerning ?? 0) - (a?.counts?.concerning ?? 0)
    || (b?.counts?.ads ?? 0) - (a?.counts?.ads ?? 0)
}

/** Сравнение застройщиков: худший статус, затем больше подозрительных и требующих проверки ЖК */
export function compareDevScreening(a?: Pick<DeveloperScreening, 'status' | 'counts'> | null, b?: Pick<DeveloperScreening, 'status' | 'counts'> | null): number {
  return screeningRank(a?.status) - screeningRank(b?.status)
    || (b?.counts?.suspect ?? 0) - (a?.counts?.suspect ?? 0)
    || (b?.counts?.check ?? 0) - (a?.counts?.check ?? 0)
}

/** Место строки основания (permit.text) в причинах: первая причина не от решения аналитика (их сервер ставит первыми);
 *  -1 — причин нет */
export const permitIndex = (s?: Pick<Screening, 'reasons'> | null): number => (s?.reasons ?? []).findIndex((r) => r.kind !== 'decision')

/** Строка основания из причин — когда permit отдельно не пришёл */
export const permitReason = (s?: Pick<Screening, 'reasons'> | null): ScreeningReason | null => {
  const i = permitIndex(s)
  return i < 0 ? null : s!.reasons[i]
}

/** Причины, кроме строки основания: сначала решение аналитика (сведения решения или «новая реклама после решения»), затем —
 *  по рекламе и автоматической оценке, в порядке сервера */
export const adReasons = (s?: Pick<Screening, 'reasons'> | null): ScreeningReason[] => {
  const i = permitIndex(s)
  return (s?.reasons ?? []).filter((_, j) => j !== i)
}

/** Причины от решения аналитика */
export const decisionReasons = (s?: Pick<Screening, 'reasons'> | null): ScreeningReason[] => (s?.reasons ?? []).filter((r) => r.kind === 'decision')

/** Главная причина, кроме основания: сервер ставит причины по важности — первая после основания; нет причин — null */
export const topReason = (s?: Pick<Screening, 'reasons'> | null): ScreeningReason | null => adReasons(s)[0] ?? null

/** До n причин по рекламе в порядке сервера */
export const topReasons = (s: Pick<Screening, 'reasons'> | null | undefined, n = 3): ScreeningReason[] => adReasons(s).slice(0, n)

/** Текст причины вокруг цитаты: сервер часто вписывает цитату в текст («… бронь — «цитата»; …») — тогда она выделяется на месте,
 *  а не повторяется второй раз. inline false — цитаты в тексте нет, её показывают отдельной строкой */
export function reasonParts(r: Pick<ScreeningReason, 'text' | 'quote'>): { before: string; quote: string | null; after: string; inline: boolean } {
  const q = r.quote?.trim()
  if (!q) return { before: r.text, quote: null, after: '', inline: false }
  const mark = `«${q}»`
  const i = r.text.indexOf(mark)
  if (i < 0) return { before: r.text, quote: q, after: '', inline: false }
  return { before: r.text.slice(0, i), quote: q, after: r.text.slice(i + mark.length), inline: true }
}

/** Коротко для строки списка: признак и цитата («бронь: «…»»), без цитаты — текст причины */
export function reasonShort(r: Pick<ScreeningReason, 'text' | 'quote' | 'signal'>): string {
  const q = r.quote?.trim()
  if (q && r.signal) return `${signalName(r.signal)}: «${q}»`
  return r.text
}

const dayMonth = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
}

/** «5 публ. · 2 с признаком приёма денег за 120 дн. · последняя 17 окт.»; рекламы нет — «Рекламы не найдено» */
export function countsLine(c?: ScreeningCounts | null, fmt: (iso: string) => string = dayMonth): string {
  if (!c || !c.ads) return 'Рекламы не найдено'
  const parts = [`${c.ads} публ.`]
  parts.push(c.concerning ? `${c.concerning} с признаком приёма денег за ${c.windowDays} дн.` : `без признаков приёма денег за ${c.windowDays} дн.`)
  if (c.lastAdAt) parts.push(`последняя ${fmt(c.lastAdAt)}`)
  return parts.join(' · ')
}

/** Счётчики по статусам: none — без оценки */
export function countByStatus<T>(xs: readonly T[], get: (x: T) => ScreeningStatus | null | undefined): Record<ScreeningStatus, number> & { none: number } {
  const out = { ok: 0, check: 0, suspect: 0, unknown: 0, none: 0 }
  for (const x of xs) {
    const s = get(x)
    if (s) out[s] += 1
    else out.none += 1
  }
  return out
}

/** Фильтр по статусу: 'all' — все; без оценки подходит только 'all' */
export type ScreeningFilter = 'all' | ScreeningStatus
export const matchesScreening = (s: ScreeningStatus | null | undefined, f: ScreeningFilter) => f === 'all' || s === f
/** Параметр адреса (?screening=suspect,check) → статусы; неизвестное отбрасывается, повторы — тоже */
export const parseScreeningParam = (v?: string | null): ScreeningStatus[] =>
  [...new Set((v ?? '').split(',').map((x) => x.trim()).filter(isScreeningStatus))]
/** Подходит ли статус под выбранные в адресе (пусто — все) */
export const inScreening = (s: ScreeningStatus | null | undefined, sel: readonly ScreeningStatus[]) => !sel.length || (!!s && sel.includes(s))

/** «3 ЖК: подозрение — 1, требует проверки — 1, без замечаний — 1» */
export function devCountsLine(c?: DeveloperScreening['counts'] | null): string {
  if (!c || !c.complexes) return 'ЖК на контроле нет'
  const parts = SCREENING_ORDER.filter((s) => c[s] > 0).map((s) => `${SCREENING_SHORT[s]} — ${c[s]}`)
  return `${c.complexes} ЖК${parts.length ? `: ${parts.join(', ')}` : ''}`
}

// ---------- Основание (permit) ----------

export const PERMIT_LABEL: Record<PermitStatus, string> = {
  permitted: 'Основание есть в открытых данных',
  not_permitted: 'Без разрешения на дольщиков',
  unknown: 'Сведений об основании нет',
}
export const PERMIT_TONE: Record<PermitStatus, ScreeningTone> = { permitted: 'green', not_permitted: 'red', unknown: 'gray' }
const PERMIT_SOURCE: Record<string, string> = {
  homeportal: 'Homeportal',
  homeportal_section: 'Homeportal, раздел «Без разрешения на дольщиков»',
  ashyq: 'Казреестр (Ashyq Data)',
  analyst: 'сверка аналитика',
}
/** Источник основания по-русски; неизвестный код — как есть */
export const permitSource = (code?: string | null) => (code ? PERMIT_SOURCE[code] ?? code : '')

/** Куда вести «источник»: карточка объекта открытых данных (внутри приложения) и/или внешняя страница источника */
export function permitLinks(p?: Permit | null): { registry?: string; url?: string } {
  if (!p) return {}
  const out: { registry?: string; url?: string } = {}
  if (p.registryKey) out.registry = `/registry/${encodeURIComponent(p.registryKey)}`
  const u = p.url?.trim()
  if (u && /^https?:\/\//i.test(u)) out.url = u
  return out
}

// ---------- Признаки в причинах ----------

const EXTRA_SIGNAL: Record<string, string> = {
  money: 'предложение внести деньги',
  deposit: 'задаток',
  direct_payment: 'оплата напрямую',
  investment: 'инвестиции',
}
/** Вид признака продажи в причине по-русски */
export const signalName = (s?: string | null) => (!s ? '' : Object.hasOwn(SIGNAL_LABEL, s) ? SIGNAL_LABEL[s as keyof typeof SIGNAL_LABEL] : EXTRA_SIGNAL[s] ?? s)

// ---------- ЖК без координат ----------

/** Координаты ЖК есть и похожи на настоящие: числа в границах, не (0, 0) */
export const hasPoint = (c: Pick<Complex, 'lat' | 'lng'>): c is Pick<Complex, 'lat' | 'lng'> & { lat: number; lng: number } =>
  typeof c.lat === 'number' && typeof c.lng === 'number' && Number.isFinite(c.lat) && Number.isFinite(c.lng)
  && Math.abs(c.lat) <= 90 && Math.abs(c.lng) <= 180 && !(c.lat === 0 && c.lng === 0)
