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
  /** Другие формулировки той же причины: ключ «<причина>_<вариант>» (R1: absent_same_source — один источник у обоих документов) */
  variants?: Record<string, string>
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
    readinessGap: number; idleDays: number; idlePhotosMin: number; idleActivities: string[]; overdueDays: number
    idleShortDays: number; quoteMaxWords: number; quoteWordsBefore: number; quoteWordsAfter: number
    /** ИИ-анализ: ниже lowConfidence — пометка «низкая уверенность», ниже hypothesisMin гипотезу не показываем, топ hypothesesTop */
    lowConfidence: number; hypothesisMin: number; hypothesesTop: number
  }
  zones: { yellowFrom: number; redFrom: number; redRequires: string[]; items: ModelZone[] }
  factors: ModelFactor[]
  observations: ModelObservation[]
  summaries: Record<'red' | 'yellow_high' | 'yellow' | 'green' | 'needs_check', string>
  /** Карточки ЖК из открытого реестра (origins) без проверенных сведений: риск не оценивается — подпись, итог, зачем */
  notAssessed: { origins: string[]; label: string; summary: string; why: string }
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
/** «Риск не оценивается — нет проверенных данных»: подпись для карточек из открытого реестра без проверенных сведений */
export const NOT_ASSESSED = model.notAssessed.label
/** Риск оценён (балл и зона показываются); false — карточка из открытого реестра без проверенных сведений */
export const isAssessed = (r?: Pick<RiskResult, 'assessed'> | null) => !!r && r.assessed !== false
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
const BLANK = new RegExp(`^[${WS}]*$`)
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

/** Решения аналитика по публикации, от которых зависит риск */
export interface AdDecisions { status?: string; linkConfirmedBy?: string | null; termsReview?: string | null }

/** Привязка к ЖК — факт только после решения аналитика: он подтвердил привязку или саму публикацию.
 *  Уверенность сопоставления (matchConfidence) — оценка ИИ: сколь угодно высокая, она остаётся гипотезой */
export const linkConfirmed = (ad: AdDecisions) => ad.status === 'confirmed' || !BLANK.test(ad.linkConfirmedBy ?? '')

/** Аналитик отклонил условия оплаты: «это не предложение внести деньги» — публикация не даёт R2 */
export const termsRejected = (ad: AdDecisions) => ad.termsReview === 'rejected'

// ---------- сборка результата ----------

const item = (kw: Record<string, unknown>) =>
  Object.fromEntries(Object.entries(kw).filter(([, v]) => v !== undefined && v !== null && v !== '')) as unknown as EvidenceItem

function factor(code: string, state: FactorState, reason: string, values?: Values, items?: EvidenceItem[], text?: string): RiskFactor {
  const f = FACTORS[code]
  return { code, title: f.title, points: f.weight, state, reason, evidence: fill(text ?? f.reasons[reason], values), items: items ?? [] }
}

/** Цитата — из текста публикации, иначе из распознанного скриншота; quoteFrom говорит, откуда она взята */
function adItem(ad: Ad, marker: string): EvidenceItem {
  const inText = quote(ad.text, marker), q = inText || quote(ad.ocr, marker)
  return item({
    kind: 'ad', ref: ad.id, label: ad.account, source: ad.platform, date: ad.date, url: ad.url, marker, quote: q,
    quoteFrom: q ? (inText ? 'text' : 'ocr') : undefined,
  })
}

/** По дате убыв., при равной дате — по id возр. (как двойная стабильная сортировка в risk.py) */
const byDateDesc = <X extends { id: string; date?: string | null }>(xs: X[]) =>
  [...xs].sort((a, b) => cmp(b.date || '', a.date || '') || cmp(a.id, b.id))

// ---------- факторы ----------

/** Провенанс документа основания (source / checkedAt / url) */
export interface DocSource { source?: string | null; checkedAt?: string | null; url?: string | null }

/** Своя запись basis.sources[key] целиком (чужую дату не занимает), иначе — общая сверка basis.source / basis.checkedAt (без ссылки).
 *  Документ «нет данных» (не yes/no) не сверялся — у него нет ни источника, ни даты, даже при общей сверке */
export function docSource(b: Partial<Basis>, key: string): DocSource {
  const state = get(b, key, 'unknown')
  if (state !== 'yes' && state !== 'no') return { source: null, checkedAt: null, url: null }
  const all = get(b, 'sources') as Record<string, DocSource | null | undefined> | null | undefined
  const own = truthy(all) ? all![key] : undefined
  if (truthy(own)) return { source: own!.source, checkedAt: own!.checkedAt, url: own!.url }
  return { source: b.source, checkedAt: b.checkedAt, url: null }
}

const sourced = (p: DocSource) => truthy(p.source) && truthy(p.checkedAt)

export function factorR1(c: Complex): RiskFactor {
  if (c.stage === BUILT) return factor('R1', 'clear', 'built')
  const b = (c.basis || {}) as Partial<Basis>
  const g = get(b, 'guarantee', 'unknown'), m = get(b, 'mio', 'unknown'), k = get(b, 'bvu', 'unknown')
  const src = Object.fromEntries(model.documents.map((d) => [d.key, docSource(b, d.key)]))
  const items = model.documents.map((d) => item({
    kind: 'document', ref: d.key, label: d.label, value: get(b, d.key, 'unknown'), quote: get(b, d.refField),
    source: src[d.key].source, date: src[d.key].checkedAt, url: src[d.key].url,
  }))
  // «отсутствие МИО не является R1» — только когда отсутствие МИО установлено; «нет данных» так не называем
  if (g === 'yes') return factor('R1', 'clear', m === 'no' ? 'ok_guarantee_no_mio' : 'ok_guarantee', null, items)
  if (m === 'yes' && k === 'yes') return factor('R1', 'clear', 'ok_mio', null, items)
  if (g === 'no' && m === 'no') {
    // отсутствие установлено, только если у каждого из двух документов есть источник и дата сверки (свои или общие)
    const pg = src.guarantee, pm = src.mio
    if (sourced(pg) && sourced(pm)) {
      const v = { guaranteeSource: pg.source, guaranteeCheckedAt: fmtDate(pg.checkedAt), mioSource: pm.source, mioCheckedAt: fmtDate(pm.checkedAt) }
      if (v.guaranteeSource === v.mioSource && v.guaranteeCheckedAt === v.mioCheckedAt) {
        return factor('R1', 'hit', 'absent', { source: pg.source, checkedAt: v.mioCheckedAt }, items, FACTORS.R1.variants!.absent_same_source)
      }
      return factor('R1', 'hit', 'absent', v, items)
    }
    const missing = model.documents.filter((d) => (d.key === 'guarantee' || d.key === 'mio') && !sourced(src[d.key])).map((d) => d.label)
    return factor('R1', 'unknown', 'absent_unsourced', { documents: missing.join(', ') }, items)
  }
  if (m === 'yes') return factor('R1', 'unknown', 'mio_without_bank', null, items)
  return factor('R1', 'unknown', 'unknown', null, items)
}

const moneyMarker = (s: AdSignals): string | undefined => s.funds[0]
const advMarker = (s: AdSignals): string | undefined => s.sale[0] ?? s.funds[0]

/** R2/R3: подтверждённая привязка → hit, только гипотеза привязки → unknown.
 *  R2 не дают публикации, по которым аналитик отклонил условия оплаты («это не предложение внести деньги»).
 *  В доказательства «отклонено аналитиком» идут только публикации, привязанные к ЖК фактом: отклонённая публикация
 *  с привязкой-гипотезой к этому объекту не относится (она и так R2 не даёт) — её не показываем и не считаем */
function adsFactor(code: 'R2' | 'R3', built: boolean, linked: Ad[], pick: (s: AdSignals) => string | undefined): RiskFactor {
  if (built) return factor(code, 'clear', 'built')
  const [hitReason, unlinkedReason] = code === 'R2' ? ['offer', 'offer_unlinked'] : ['ads', 'ads_unlinked']
  const conf: EvidenceItem[] = [], unconf: EvidenceItem[] = [], rejected: EvidenceItem[] = []
  for (const a of linked) {
    const marker = pick(adSignals(a))
    if (!marker) continue
    if (code === 'R2' && termsRejected(a)) {
      if (linkConfirmed(a)) rejected.push(adItem(a, marker))
    } else (linkConfirmed(a) ? conf : unconf).push(adItem(a, marker))
  }
  if (conf.length) return factor(code, 'hit', hitReason, { count: conf.length }, conf)
  if (unconf.length) return factor(code, 'unknown', unlinkedReason, { count: unconf.length }, unconf)
  if (rejected.length) return factor(code, 'clear', 'offer_rejected', { count: rejected.length }, rejected)
  return factor(code, 'clear', 'none')
}

export function factorR4(c: Complex, appeals: Appeal[]): RiskFactor {
  const own = appeals.filter((a) => a.complexId === c.id && truthy(a.collective) && a.status !== 'rejected')
  const it = (a: Appeal) => item({ kind: 'appeal', ref: a.id, label: a.subject, source: a.source, date: a.date, value: a.status })
  const verified = byDateDesc(own.filter((a) => a.status === 'verified'))
  if (verified.length) return factor('R4', 'hit', 'verified', { count: verified.length }, verified.map(it))
  const pending = byDateDesc(own.filter((a) => a.status === 'pending'))
  if (pending.length) return factor('R4', 'unknown', 'pending', null, pending.map(it))
  if (model.notAssessed.origins.includes(c.origin ?? '')) {
    // карточка реестра или рекламы: обращения сюда никто не заносил — пустой список не значит «обращений нет»;
    // «нет» — только после сверки аналитиком с источником и датой (appealsCheck), как в risk.py
    const chk = (get(c, 'appealsCheck') ?? {}) as { source?: string | null; checkedAt?: string | null }
    if (truthy(chk.source) && truthy(chk.checkedAt)) {
      return factor('R4', 'clear', 'none_checked', { source: chk.source, checkedAt: fmtDate(chk.checkedAt) })
    }
    return factor('R4', 'unknown', 'unchecked')
  }
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
  // застройщик из открытого реестра: портфель (объектов, незавершённых) не получен — профиль неизвестен, а не «в порядке»
  if (dev == null || model.notAssessed.origins.includes(dev.origin ?? '')) out.push(obs('O2', 'unknown'))
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

/** Оценивается ли риск ЖК. ЖК не из открытого реестра — всегда. Карточка из реестра (сведения источника, аналитиком не
 *  проверены) — только когда по ней есть проверенные сведения: подтверждённая аналитиком привязка публикации, документ
 *  основания «есть/нет» с источником и датой сверки, проверенное обращение, подтверждённая готовность с источником и датой
 *  или датированный снимок площадки с источником (как assessed в risk.py) */
export function assessed(c: Complex, ads: Ad[], appeals: Appeal[]): boolean {
  if (!model.notAssessed.origins.includes(c.origin ?? '')) return true
  if (ads.some((a) => a.complexId === c.id && a.status !== 'dismissed' && linkConfirmed(a))) return true
  const b = (c.basis || {}) as Partial<Basis>
  if (model.documents.some((d) => sourced(docSource(b, d.key)))) return true
  if (appeals.some((a) => a.complexId === c.id && a.status === 'verified')) return true
  const k = ((c.readiness || {}) as Partial<Readiness>).confirmed
  if (k && typeof k.value === 'number' && truthy(k.source) && truthy(k.date)) return true
  return (c.photos || []).some((p) => truthy(p.date) && truthy(p.source))
}

/** Риск ЖК: шесть факторов §5, балл, зона, «Нужна проверка», наблюдения вне балла. asOf — 'YYYY-MM-DD', по умолчанию сегодня.
 *  Карточка из открытого реестра без проверенных сведений — assessed false, балл 0 и «риск не оценивается» */
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
  if (!assessed(c, ads, appeals)) {
    return { score: 0, zone: zoneFor(0, []), needsCheck: t.needsCheck, factors, observations: observations(c, dev),
      summary: model.notAssessed.summary, modelVersion: model.version, assessed: false }
  }
  const key = t.zone !== 'yellow' ? t.zone : t.score >= model.zones.redFrom ? 'yellow_high' : 'yellow'
  let summary = fill(s[key], { score: t.score })
  if (t.needsCheck) summary += ' ' + fill(s.needs_check, { codes: t.unknown.join(', ') })
  return { score: t.score, zone: t.zone, needsCheck: t.needsCheck, factors, observations: observations(c, dev), summary, modelVersion: model.version, assessed: true }
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
    r2 = !money ? factor('R2', 'clear', 'none', null, [], r.none)
      : termsRejected(ad) ? factor('R2', 'clear', 'R2_rejected', { marker: money }, [adItem(ad, money)], r.R2_rejected)
      : factor('R2', 'hit', 'R2_offer', null, [adItem(ad, money)], r.R2_offer)
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
