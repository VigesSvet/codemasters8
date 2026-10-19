// Фильтры панели карты (pages/Dashboard.tsx): «Проверка по рекламе» (чипы статусов, можно несколько), «Формальный риск»
// (диапазон балла, ЖК без оценки, «нужна проверка») и «Показывать» (сданные, без замечаний, прошедшие проверку аналитика).
// Чистые функции: группа ЖК, правило риска, видимость, счётчики и короткие сводки свёрнутых блоков.
import type { Complex, RiskResult, ScreeningStatus } from '../data/types'
import { isActive } from './decision'
import { isAssessed, model } from './risk'
import { SCREENING_FILTER_LABEL, SCREENING_ORDER, SCREENING_STATUSES, inScreening, statusOf, variantOf } from './screening'

// ---------- «Проверка по рекламе» ----------

/** Выбор чипа: пусто — «Все»; статус уже выбран — снимается, иначе добавляется (порядок — как у чипов) */
export function toggleStatus(sel: readonly ScreeningStatus[], s: ScreeningStatus): ScreeningStatus[] {
  const on = sel.includes(s) ? sel.filter((x) => x !== s) : [...sel, s]
  return SCREENING_STATUSES.filter((x) => on.includes(x))
}

/** «Подозрение · Требуют проверки» — в порядке «подозрение первыми»; ничего не выбрано — «все статусы» */
export const screeningSummary = (sel: readonly ScreeningStatus[]) =>
  sel.length ? SCREENING_ORDER.filter((s) => sel.includes(s)).map((s) => SCREENING_FILTER_LABEL[s]).join(' · ') : 'все статусы'

// ---------- «Формальный риск» ----------

/** Шкала балла и пороги зон — из модели риска (shared/risk_model.json) */
export const RISK_MAX = model.maxScore
export const YELLOW_FROM = model.zones.yellowFrom
export const RED_FROM = model.zones.redFrom

export interface RiskFilter {
  /** Балл от и до (включительно) — для ЖК с оценкой риска */
  min: number
  max: number
  /** Показывать ЖК без оценки риска (карточки реестра без проверенных сведений) — независимо от диапазона */
  withNA: boolean
  /** Только «нужна проверка» */
  needsCheck: boolean
}
export const RISK_DEFAULT: RiskFilter = { min: 0, max: RISK_MAX, withNA: true, needsCheck: false }

type RiskLike = Pick<RiskResult, 'score' | 'needsCheck' | 'assessed'>

/** Правило риска: «нужна проверка» (если выбрано) — у всех; без оценки — по переключателю; с оценкой — балл в диапазоне */
export function inRisk(r: RiskLike, f: RiskFilter): boolean {
  if (f.needsCheck && !r.needsCheck) return false
  if (!isAssessed(r)) return f.withNA
  return r.score >= f.min && r.score <= f.max
}

export const isRiskDefault = (f: RiskFilter) =>
  f.min <= 0 && f.max >= RISK_MAX && f.withNA === RISK_DEFAULT.withNA && f.needsCheck === RISK_DEFAULT.needsCheck

/** «баллы 25–100 · без оценки скрыты · нужна проверка»; без фильтра — «все баллы» */
export function riskSummary(f: RiskFilter): string {
  const parts = [f.min <= 0 && f.max >= RISK_MAX ? 'все баллы' : `баллы ${f.min}–${f.max}`]
  if (!f.withNA) parts.push('без оценки скрыты')
  if (f.needsCheck) parts.push('нужна проверка')
  return parts.join(' · ')
}

/** Счётчики блока: без оценки, с оценкой, из них в диапазоне, «нужна проверка» */
export function riskCounts(rs: readonly RiskLike[], f: Pick<RiskFilter, 'min' | 'max'>) {
  const out = { na: 0, assessed: 0, inRange: 0, needsCheck: 0 }
  for (const r of rs) {
    if (r.needsCheck) out.needsCheck += 1
    if (!isAssessed(r)) { out.na += 1; continue }
    out.assessed += 1
    if (r.score >= f.min && r.score <= f.max) out.inRange += 1
  }
  return out
}

// ---------- «Показывать» ----------

/** built — сданные, clean — без замечаний (реестр / автоматическая оценка), checked — прошедшие проверку аналитика */
export type ShowGroup = 'built' | 'clean' | 'checked'
export const SHOW_GROUPS: readonly ShowGroup[] = ['built', 'clean', 'checked']
export type ShowFlags = Record<ShowGroup, boolean>
export const SHOW_DEFAULT: ShowFlags = { built: true, clean: true, checked: true }
export const SHOW_LABEL: Record<ShowGroup, string> = { built: 'Сданные', clean: 'Без замечаний', checked: 'Прошедшие проверку аналитика' }
/** Для сводки свёрнутого блока */
export const SHOW_SHORT: Record<ShowGroup, string> = { built: 'сданные', clean: 'без замечаний', checked: 'проверенные' }
export const SHOW_HINT: Record<ShowGroup, string> = {
  built: 'Решение «Объект сдан» или стадия «введён», без замечаний',
  clean: 'В реестре, замечаний нет — без решения аналитика',
  checked: 'Решение «Проверено — замечаний нет»',
}

type GroupLike = Pick<Complex, 'stage' | 'screening' | 'decision'>

/** Группа ЖК — ровно одна или никакой, по старшинству:
 *  1) действующее решение аналитика: «Объект сдан» → built, «Проверено — замечаний нет» → checked (без объекта решения —
 *     по варианту оценки: commissioned / analyst);
 *  2) стадия карточки «введён» → built;
 *  3) «Проверка по рекламе» ok без решения (в реестре, замечаний нет) → clean;
 *  иначе null — переключатели «Показывать» ЖК не скрывают (требуют проверки, подозрение, нет данных, решение снято рекламой) */
export function showGroupOf(c: GroupLike): ShowGroup | null {
  // требует проверки, подозрение, нет данных (в том числе сданный дом, по которому решение снято новой рекламой) —
  // ни в одну группу: такие ЖК переключатели «Показывать» не прячут никогда
  if (c.screening && statusOf(c) !== 'ok') return null
  const kind = isActive(c.decision) ? c.decision.kind : null
  const v = variantOf(c.screening)
  if (kind === 'commissioned' || (!kind && v === 'commissioned')) return 'built'
  if (kind === 'no_issues' || (!kind && v === 'analyst')) return 'checked'
  if (c.stage === model.builtStage) return 'built'
  if (statusOf(c) === 'ok') return 'clean'
  return null
}

/** Виден ли ЖК при переключателях «Показывать»: вне групп — всегда */
export const isShown = (c: GroupLike, flags: ShowFlags) => {
  const g = showGroupOf(c)
  return !g || flags[g]
}

export const isShowDefault = (f: ShowFlags) => SHOW_GROUPS.every((g) => f[g] === SHOW_DEFAULT[g])

/** Счётчики групп по всем ЖК; other — вне групп. Сумма всегда равна числу ЖК */
export function countShowGroups(cs: readonly GroupLike[]): Record<ShowGroup | 'other', number> {
  const out = { built: 0, clean: 0, checked: 0, other: 0 }
  for (const c of cs) out[showGroupOf(c) ?? 'other'] += 1
  return out
}

/** «скрыты: сданные, проверенные»; всё показано — «все группы» */
export function showSummary(f: ShowFlags): string {
  const off = SHOW_GROUPS.filter((g) => !f[g])
  if (!off.length) return 'все группы'
  return `скрыты: ${off.map((g) => SHOW_SHORT[g]).join(', ')}`
}

// ---------- вместе ----------

export interface MapFilters { screening: readonly ScreeningStatus[]; risk: RiskFilter; show: ShowFlags }
export const MAP_DEFAULT: MapFilters = { screening: [], risk: RISK_DEFAULT, show: SHOW_DEFAULT }

/** Проходит ли ЖК три блока фильтров (поиск и переключатели карты проверяет экран) */
export const passesMapFilters = (x: { c: GroupLike; r: RiskLike }, f: MapFilters) =>
  inScreening(statusOf(x.c), f.screening) && inRisk(x.r, f.risk) && isShown(x.c, f.show)
