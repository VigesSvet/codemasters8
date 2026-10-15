// Риск-скоринг ЖК и публикаций по ТЗ §5 — точный порт backend/app/risk.py.
// Основной расчёт делает бэкенд (/api/risk); этот модуль — офлайн-фоллбэк и оценка черновика публикации.
// Веса, пороги, словари и тексты — только из shared/risk_model.json (общий с бэкендом).
// Совпадение с Python проверяет risk.test.ts на shared/risk_golden.json.
import modelJson from '../../../shared/risk_model.json'
import type {
  Ad, AdScore, AdSignals, Appeal, Basis, Complex, Developer, EvidenceItem, FactorState, Observation, ObservationState,
  Readiness, ReadinessValue, RiskFactor, RiskResult, Zone,
} from '../data/types'

export interface ModelFactor {
  code: string; weight: number; short: string; title: string; when: string; source: string; example: string
  reasons: Record<string, string>
}
export interface ModelObservation { code: string; title: string; why: string; texts: Record<string, string> }
export interface ModelZone { zone: Zone; name: string; range: string; rule: string; action: string }
export interface ModelCase {
  id: string; title: string; hits: string[]; unknown: string[]; score: number; zone: Zone; expect: string; complexId: string
}
export interface RiskModel {
  version: string; title: string; disclaimer: string; formula: string; maxScore: number; builtStage: string
  documents: { key: string; label: string; refField: string }[]
  thresholds: {
    linkConfirmed: number; autoLinkConfidence: number; readinessGap: number; idleDays: number; idlePhotosMin: number
    idleActivities: string[]; overdueDays: number; idleShortDays: number; quoteMaxWords: number; quoteWordsBefore: number
    quoteWordsAfter: number
  }
  zones: { yellowFrom: number; redFrom: number; redRequires: string[]; items: ModelZone[] }
  factors: ModelFactor[]
  observations: ModelObservation[]
  summaries: Record<'red' | 'yellow_high' | 'yellow' | 'green' | 'needs_check', string>
  ad: { factors: string[]; reasons: Record<string, string> }
  markers: { money: string[]; sale: string[]; safe: string[]; search: string[] }
  cases: ModelCase[]
}

export const model = modelJson as unknown as RiskModel
export const MARKERS = model.markers
const T = model.thresholds
const BUILT = model.builtStage
const FACTORS: Record<string, ModelFactor> = Object.fromEntries(model.factors.map((f) => [f.code, f]))
const OBSERVATIONS: Record<string, ModelObservation> = Object.fromEntries(model.observations.map((o) => [o.code, o]))

export const factorDef = (code: string): ModelFactor | undefined => FACTORS[code]
export const zoneLabel = Object.fromEntries(model.zones.items.map((z) => [z.zone, z.name])) as Record<Zone, string>

// ---------- паритет с Python ----------

type Values = Record<string, unknown> | null
/** dict.get(k, default): default только при отсутствии ключа, null остаётся null */
const get = (o: object, k: string, d?: unknown): unknown => { const v = (o as Record<string, unknown>)[k]; return v === undefined ? d : v }
/** Истинность как в Python: пустые строка, список и словарь, 0 и null — ложь */
const truthy = (x: unknown) => (Array.isArray(x) ? x.length > 0 : x !== null && typeof x === 'object' ? Object.keys(x).length > 0 : Boolean(x))
const pyStr = (v: unknown) => (v === null || v === undefined ? 'None' : v === true ? 'True' : v === false ? 'False' : String(v))
const cmp = (a: unknown, b: unknown) => ((a as string) < (b as string) ? -1 : (a as string) > (b as string) ? 1 : 0)
const pct = (x: number) => Math.floor(x * 100 + 0.5)
const dayNum = (iso: string) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 86_400_000
/** «Сегодня» по Усть-Каменогорску (UTC+5, без перехода на летнее время) — как datetime.now(ALMATY) в risk.py:
 *  иначе у браузера в другом поясе около полуночи дата расчёта (и R6) разошлась бы с бэкендом */
const today = () => new Date(Date.now() + 5 * 3600e3).toISOString().slice(0, 10)

// Пробельные символы Python (str.isspace, \s в re): слова и предложения режутся ровно как в risk.py
const WS = '\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000'
const SENTENCE = new RegExp(`(?<=[.!?…])[${WS}]+|\\n+`)
const WORDS = new RegExp(`[${WS}]+`)
const words = (s: string) => s.split(WORDS).filter(Boolean)

/** Подставляет {ключ} из values; неизвестные ключи оставляет как есть (как fill() в risk_model.py) */
export function fill(template: string, values?: Values): string {
  const v = values ?? {}
  return template.replace(/\{(\w+)\}/g, (m, k: string) => (Object.hasOwn(v, k) ? pyStr(v[k]) : m))
}

/** '2026-10-09' или '2026-10-09T18:22' → '09.10.2026' без часовых поясов */
export const fmtDate = (iso?: string | null) => (!iso || iso.length < 10 ? '—' : `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`)

export function zoneFor(score: number, hitCodes: Iterable<string>): Zone {
  const z = model.zones, hit = new Set(hitCodes)
  if (score >= z.redFrom && z.redRequires.every((code) => hit.has(code))) return 'red'
  return score >= z.yellowFrom ? 'yellow' : 'green'
}

// ---------- текст публикации ----------

/** Маркеры из словаря, найденные в тексте; вложенные («взнос» внутри «первый взнос») убираются */
export function hits(text: string, list: string[]): string[] {
  const t = text.toLowerCase()
  const found = list.filter((w) => t.includes(w.toLowerCase()))
  return found.filter((w) => !found.some((o) => o !== w && o.toLowerCase().includes(w.toLowerCase())))
}

export function adSignals(ad: Pick<Ad, 'text' | 'ocr'>): AdSignals {
  const t = `${ad.text || ''} ${ad.ocr || ''}`
  return { sale: hits(t, MARKERS.sale), funds: hits(t, MARKERS.money), safe: hits(t, MARKERS.safe) }
}

/** Фрагмент-основание: предложение с маркером; длинное — обрезается по словам вокруг маркера */
export function quote(text: string | null | undefined, marker: string): string | undefined {
  if (!text) return undefined
  const m = marker.toLowerCase()
  for (const s of text.split(SENTENCE)) {
    const i = s.toLowerCase().indexOf(m)
    if (i < 0) continue
    const ws = words(s)
    if (ws.length <= T.quoteMaxWords) return ws.join(' ')
    const wi = words(s.slice(0, i)).length
    const a = Math.max(0, wi - T.quoteWordsBefore), b = Math.min(ws.length, wi + T.quoteWordsAfter)
    return (a > 0 ? '… ' : '') + ws.slice(a, b).join(' ') + (b < ws.length ? ' …' : '')
  }
  return undefined
}

/** Привязка к ЖК — факт, если уверенность не ниже порога или аналитик подтвердил публикацию */
export const linkConfirmed = (ad: Pick<Ad, 'status' | 'matchConfidence'>) =>
  ad.status === 'confirmed' || (ad.matchConfidence || 0) >= T.linkConfirmed

// ---------- сборка результата ----------

const item = (kw: Record<string, unknown>) =>
  Object.fromEntries(Object.entries(kw).filter(([, v]) => v !== undefined && v !== null && v !== '')) as unknown as EvidenceItem

function factor(code: string, state: FactorState, reason: string, values?: Values, items?: EvidenceItem[], text?: string): RiskFactor {
  const f = FACTORS[code]
  return { code, title: f.title, points: f.weight, state, reason, evidence: fill(text ?? f.reasons[reason], values), items: items ?? [] }
}

const adItem = (ad: Ad, marker: string) => item({
  kind: 'ad', ref: ad.id, label: ad.account, source: ad.platform, date: ad.date, url: ad.url, marker,
  quote: quote(ad.text, marker) || quote(ad.ocr, marker),
})

/** По дате убыв., при равной дате — по id возр. (как двойная стабильная сортировка в risk.py) */
const byDateDesc = <X extends { id: string; date?: string | null }>(xs: X[]) =>
  [...xs].sort((a, b) => cmp(b.date || '', a.date || '') || cmp(a.id, b.id))

// ---------- факторы ----------

export function factorR1(c: Complex): RiskFactor {
  if (c.stage === BUILT) return factor('R1', 'clear', 'built')
  const b = (c.basis || {}) as Partial<Basis>
  const g = get(b, 'guarantee', 'unknown'), m = get(b, 'mio', 'unknown'), k = get(b, 'bvu', 'unknown')
  const items = model.documents.map((d) => item({
    kind: 'document', ref: d.key, label: d.label, value: get(b, d.key, 'unknown'), quote: get(b, d.refField), source: b.source, date: b.checkedAt,
  }))
  // «отсутствие МИО не является R1» — только когда отсутствие МИО установлено; «нет данных» так не называем
  if (g === 'yes') return factor('R1', 'clear', m === 'no' ? 'ok_guarantee_no_mio' : 'ok_guarantee', null, items)
  if (m === 'yes' && k === 'yes') return factor('R1', 'clear', 'ok_mio', null, items)
  if (g === 'no' && m === 'no') {
    if (truthy(b.source) && truthy(b.checkedAt)) return factor('R1', 'hit', 'absent', { source: b.source, checkedAt: fmtDate(b.checkedAt) }, items)
    return factor('R1', 'unknown', 'absent_unsourced', null, items)
  }
  if (m === 'yes') return factor('R1', 'unknown', 'mio_without_bank', null, items)
  return factor('R1', 'unknown', 'unknown', null, items)
}

const moneyMarker = (s: AdSignals): string | undefined => s.funds[0]
const advMarker = (s: AdSignals): string | undefined => s.sale[0] ?? s.funds[0]

/** R2/R3: подтверждённая привязка → hit, только гипотеза привязки → unknown */
function adsFactor(code: 'R2' | 'R3', built: boolean, linked: Ad[], pick: (s: AdSignals) => string | undefined): RiskFactor {
  if (built) return factor(code, 'clear', 'built')
  const [hitReason, unlinkedReason] = code === 'R2' ? ['offer', 'offer_unlinked'] : ['ads', 'ads_unlinked']
  const conf: EvidenceItem[] = [], unconf: EvidenceItem[] = []
  for (const a of linked) {
    const marker = pick(adSignals(a))
    if (marker) (linkConfirmed(a) ? conf : unconf).push(adItem(a, marker))
  }
  if (conf.length) return factor(code, 'hit', hitReason, { count: conf.length }, conf)
  if (unconf.length) return factor(code, 'unknown', unlinkedReason, { threshold: pct(T.linkConfirmed) }, unconf)
  return factor(code, 'clear', 'none')
}

export function factorR4(c: Complex, appeals: Appeal[]): RiskFactor {
  const own = appeals.filter((a) => a.complexId === c.id && truthy(a.collective) && a.status !== 'rejected')
  const it = (a: Appeal) => item({ kind: 'appeal', ref: a.id, label: a.subject, source: a.source, date: a.date, value: a.status })
  const verified = byDateDesc(own.filter((a) => a.status === 'verified'))
  if (verified.length) return factor('R4', 'hit', 'verified', { count: verified.length }, verified.map(it))
  const pending = byDateDesc(own.filter((a) => a.status === 'pending'))
  if (pending.length) return factor('R4', 'unknown', 'pending', null, pending.map(it))
  return factor('R4', 'clear', 'none')
}

export function factorR5(c: Complex): RiskFactor {
  if (c.stage === BUILT) return factor('R5', 'clear', 'built')
  const r = (c.readiness || {}) as Partial<Readiness>
  const d = r.declared, k = r.confirmed
  const pairs = [['declared', 'Заявленная готовность', d], ['confirmed', 'Подтверждённая готовность', k]] as const
  const items = pairs.filter(([, , x]) => truthy(x)).map(([key, label, x]) =>
    item({ kind: 'readiness', ref: key, label, value: x?.value, source: x?.source, date: x?.date }))
  const ok = (x?: ReadinessValue | null): x is ReadinessValue =>
    !!x && truthy(x) && typeof x.value === 'number' && truthy(x.source) && truthy(x.date)
  if (!(ok(d) && ok(k))) return factor('R5', 'unknown', 'incomplete', null, items)
  // округление до сотых до сравнения и вывода — одинаково с risk.py
  const diff = Math.floor(Math.abs(d.value - k.value) * 100 + 0.5) / 100, hit = diff >= T.readinessGap
  return factor('R5', hit ? 'hit' : 'clear', hit ? 'mismatch' : 'match',
    { declared: d.value, confirmed: k.value, diff, threshold: T.readinessGap }, items)
}

export function factorR6(c: Complex, asOf: string): RiskFactor {
  if (c.stage === BUILT) return factor('R6', 'clear', 'built')
  const planned = c.plannedEnd
  if (planned) {
    const days = dayNum(asOf) - dayNum(planned)
    if (days >= T.overdueDays) return factor('R6', 'hit', 'overdue', { plannedEnd: fmtDate(planned), days },
      [item({ kind: 'deadline', ref: 'plannedEnd', label: 'Заявленный срок сдачи', date: planned })])
  }
  const photos = c.photos || [], idle = c.idleDays || 0
  const items = photos.map((p) => item({ kind: 'photo', ref: p.src, label: p.note || p.activity, source: p.source, date: p.date, value: p.activity }))
  if (idle >= T.idleDays) {
    // простой подтверждают только датированные снимки без активности или с низкой; в items — вся серия
    const dates = photos.filter((p) => T.idleActivities.includes(p.activity)).map((p) => p.date).filter(Boolean).sort(cmp)
    if (dates.length >= T.idlePhotosMin) {
      return factor('R6', 'hit', 'idle', { idleDays: idle, from: fmtDate(dates[0]), to: fmtDate(dates[dates.length - 1]) }, items)
    }
    return factor('R6', 'unknown', 'idle_unconfirmed', { idleDays: idle, photos: T.idlePhotosMin }, items)
  }
  if (!photos.length) return factor('R6', 'unknown', 'no_photos')
  return factor('R6', 'clear', 'active')
}

/** Наблюдения вне балла: перечень ГАСК, профиль застройщика, короткий простой */
export function observations(c: Complex, dev?: Developer | null): Observation[] {
  const obs = (code: string, state: ObservationState, values?: Values): Observation => {
    const o = OBSERVATIONS[code]
    return { code, title: o.title, state, text: fill(o.texts[state], values) }
  }
  const lst = get(c, 'inPublishedList', 'unknown')
  const out = [obs('O1', lst === 'yes' ? 'ok' : lst === 'no' ? 'flag' : 'unknown')]
  if (dev == null) out.push(obs('O2', 'unknown'))
  else {
    const flag = (dev.unfinished || 0) > 0 && (dev.objects || 0) <= 3
    out.push(obs('O2', flag ? 'flag' : 'ok', { unfinished: dev.unfinished, objects: dev.objects }))
  }
  const idle = c.idleDays || 0
  const short = c.stage !== BUILT && T.idleShortDays <= idle && idle < T.idleDays
  out.push(obs('O3', short ? 'flag' : 'ok', { idleDays: idle, threshold: T.idleDays }))
  return out
}

// ---------- итог ----------

function total(factors: RiskFactor[]) {
  const hit = factors.filter((f) => f.state === 'hit')
  const score = Math.min(model.maxScore, hit.reduce((s, f) => s + f.points, 0))
  const unknown = factors.filter((f) => f.state === 'unknown').map((f) => f.code)
  return { score, zone: zoneFor(score, hit.map((f) => f.code)), needsCheck: unknown.length > 0, unknown }
}

/** Риск ЖК: шесть факторов §5, балл, зона, «Нужна проверка», наблюдения вне балла. asOf — 'YYYY-MM-DD', по умолчанию сегодня */
export function scoreComplex(c: Complex, dev: Developer | null | undefined, ads: Ad[], appeals: Appeal[], asOf?: string): RiskResult {
  const d = (asOf ?? today()).slice(0, 10)
  const built = c.stage === BUILT
  const linked = byDateDesc(ads.filter((a) => a.complexId === c.id && a.status !== 'dismissed'))
  const factors = [
    factorR1(c),
    adsFactor('R2', built, linked, moneyMarker),
    adsFactor('R3', built, linked, advMarker),
    factorR4(c, appeals),
    factorR5(c),
    factorR6(c, d),
  ]
  const t = total(factors), s = model.summaries
  const key = t.zone !== 'yellow' ? t.zone : t.score >= model.zones.redFrom ? 'yellow_high' : 'yellow'
  let summary = fill(s[key], { score: t.score })
  if (t.needsCheck) summary += ' ' + fill(s.needs_check, { codes: t.unknown.join(', ') })
  return { score: t.score, zone: t.zone, needsCheck: t.needsCheck, factors, observations: observations(c, dev), summary, modelVersion: model.version }
}

/** Оценка одной публикации: основание объекта (R1) + её собственные R2 и R3, те же веса и зоны */
export function scoreAd(ad: Ad, c?: Complex | null): AdScore {
  const s = adSignals(ad)
  // «введён» учитываем, только если привязка к ЖК — факт, а не гипотеза
  const built = !!c && truthy(c) && c.stage === BUILT && linkConfirmed(ad)
  const money = moneyMarker(s), adv = advMarker(s), r = model.ad.reasons
  let r1: RiskFactor, r2: RiskFactor, r3: RiskFactor
  if (built) {
    r2 = factor('R2', 'clear', 'built')
    r3 = factor('R3', 'clear', 'built')
  } else {
    r2 = money ? factor('R2', 'hit', 'R2_offer', null, [adItem(ad, money)], r.R2_offer) : factor('R2', 'clear', 'none', null, [], r.none)
    r3 = adv ? factor('R3', 'hit', 'R3_ads', null, [adItem(ad, adv)], r.R3_ads) : factor('R3', 'clear', 'none', null, [], r.none)
  }
  if (built) r1 = factor('R1', 'clear', 'built')
  else if (r2.state !== 'hit' && r3.state !== 'hit') r1 = factor('R1', 'clear', 'R1_no_sale', null, [], r.R1_no_sale)
  else if (c == null) r1 = factor('R1', 'unknown', 'R1_no_complex', null, [], r.R1_no_complex)
  else if (!linkConfirmed(ad)) {
    r1 = factor('R1', 'unknown', 'R1_link_unconfirmed', { confidence: pct(ad.matchConfidence || 0) }, [], r.R1_link_unconfirmed)
  } else r1 = factorR1(c)
  const factors = [r1, r2, r3], t = total(factors)
  return { score: t.score, zone: t.zone, needsCheck: t.needsCheck, factors, signals: s }
}
