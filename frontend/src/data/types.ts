export type Zone = 'green' | 'yellow' | 'red'
export type Tri = 'yes' | 'no' | 'unknown'

export type Stage = 'котлован' | 'фундамент' | 'каркас' | 'кровля' | 'отделка' | 'введён'

export interface Developer {
  id: string
  name: string
  bin: string
  director: string
  registered: string
  objects: number
  unfinished: number
  site?: string
  instagram?: string
  /** Телефоны отдела продаж (демо) — признак сопоставления публикации с ЖК */
  phones?: string[]
}

export interface Basis {
  /** Гарантия ЕО (Казахстанская жилищная компания) */
  guarantee: Tri
  guaranteeRef?: string
  /** Разрешение МИО на привлечение денег дольщиков */
  mio: Tri
  mioRef?: string
  /** Банк второго уровня (проектное финансирование / эскроу) */
  bvu: Tri
  bank?: string
  /** Где сверено основание и когда (без источника «нет» не превращается в R1) */
  source?: string
  checkedAt?: string
}

/** Значение готовности с источником и датой — для R5 нужны оба */
export interface ReadinessValue {
  value: number
  source: string
  date: string
}

export interface Readiness {
  /** Заявлено застройщиком (сайт, реклама) */
  declared: ReadinessValue | null
  /** Подтверждено (акт осмотра, фото-мониторинг) */
  confirmed: ReadinessValue | null
}

export interface SitePhoto {
  date: string
  src: string
  source: 'дрон' | 'камера' | 'выезд'
  activity: 'есть' | 'нет' | 'низкая'
  note?: string
  fence?: Tri
  bins?: Tri
  wheelWash?: Tri
}

export interface Complex {
  id: string
  name: string
  developerId: string
  address: string
  district: string
  lng: number
  lat: number
  stage: Stage
  /** Подтверждённая готовность, % (совпадает с readiness.confirmed.value) */
  progress: number
  floors: number
  flats: number
  start: string
  plannedEnd: string
  basis: Basis
  readiness: Readiness
  inPublishedList: Tri
  cover: string
  photos: SitePhoto[]
  idleDays: number
}

export type AppealStatus = 'verified' | 'pending' | 'rejected'

/** Обращение граждан по объекту — данные для R4 */
export interface Appeal {
  id: string
  complexId: string
  date: string
  collective: boolean
  applicants: number
  status: AppealStatus
  verifiedAt?: string | null
  subject: string
  source: string
}

export type Platform = 'instagram' | 'tiktok' | 'krisha' | 'olx' | 'telegram' | 'facebook' | '2gis'

export type AdStatus = 'new' | 'needs_check' | 'in_review' | 'confirmed' | 'dismissed'

/** Решение аналитика по условиям оплаты, найденным ИИ: rejected — «это не предложение внести деньги» (R2 не засчитывается) */
export type TermsReview = 'accepted' | 'rejected'

export interface Ad {
  id: string
  platform: Platform
  account: string
  url: string
  date: string
  text: string
  ocr?: string
  image?: string
  complexId?: string
  /** Уверенность ИИ в привязке к ЖК (информативно): фактом привязку делает только подтверждение аналитика */
  matchConfidence: number
  /** Кто и когда подтвердил привязку к ЖК; null — привязка остаётся гипотезой ИИ */
  linkConfirmedBy?: string | null
  linkConfirmedAt?: string | null
  /** ЖК, гипотезы о которых аналитик отклонил — ИИ их больше не предлагает */
  rejectedLinks?: string[]
  termsReview?: TermsReview | null
  termsReviewBy?: string | null
  termsReviewAt?: string | null
  price?: string
  source: 'manual' | 'parser' | 'demo'
  status: AdStatus
  assignee?: string
  reach?: number
}

export type FactorState = 'hit' | 'clear' | 'unknown'
export type EvidenceKind = 'ad' | 'document' | 'photo' | 'appeal' | 'readiness' | 'deadline'

/** Доказательство к фактору: публикация с цитатой, документ, снимок, обращение, значение готовности */
export interface EvidenceItem {
  kind: EvidenceKind
  /** id публикации / обращения, ключ документа (guarantee|mio|bvu), src снимка */
  ref?: string
  label?: string
  /** Фрагмент-основание (для публикации) или реквизиты документа */
  quote?: string
  /** Маркер из словаря, по которому найдена цитата */
  marker?: string
  /** Откуда цитата публикации: текст объявления или распознанный скриншот (OCR) */
  quoteFrom?: 'text' | 'ocr'
  source?: string
  date?: string
  url?: string
  /** Состояние документа (yes|no|unknown), статус обращения, значение готовности */
  value?: string | number
}

export interface RiskFactor {
  code: string
  title: string
  /** Вес фактора по §5; в балл идёт только при state = 'hit' */
  points: number
  state: FactorState
  /** Ключ пояснения в shared/risk_model.json */
  reason: string
  evidence: string
  items: EvidenceItem[]
}

export type ObservationState = 'ok' | 'flag' | 'unknown'

/** Дополнительное наблюдение вне балла (перечень ГАСК, профиль застройщика, короткий простой) */
export interface Observation {
  code: string
  title: string
  state: ObservationState
  text: string
}

export interface RiskResult {
  score: number
  zone: Zone
  needsCheck: boolean
  factors: RiskFactor[]
  observations: Observation[]
  summary: string
  modelVersion: string
}

export interface AdSignals {
  sale: string[]
  funds: string[]
  safe: string[]
}

/** Оценка публикации: основание объекта (R1) + её собственные R2, R3 */
export interface AdScore {
  score: number
  zone: Zone
  needsCheck: boolean
  factors: RiskFactor[]
  signals: AdSignals
}

/** Вид события журнала: данные и источники, статус, привязка, комментарий, запрос сведений, сигнал, выгрузка, системное,
 *  ИИ (гипотеза и решения аналитика по ней) */
export type AuditKind = 'system' | 'data' | 'status' | 'link' | 'comment' | 'request' | 'signal' | 'export' | 'ai'

/** Событие журнала (audit_events): кто, что, когда; к чему относится — публикация, ЖК, сигнал */
export interface AuditEvent {
  id: number
  /** ISO 8601 c UTC-смещением */
  at: string
  who: string
  what: string
  kind: AuditKind
  adId: string | null
  complexId: string | null
  signalId: number | null
}

/** Сотрудник для входа: name пишется в журнал, short — инициалы */
export interface Analyst {
  id: string
  name: string
  fullName: string
  short: string
  role: string
}

// ---------- Сигнальная карточка (модуль 05) ----------

export type SignalStatus = 'new' | 'confirmed' | 'returned'

/** Публикация в снимке доказательств */
export interface SignalAd {
  id: string
  platform: Platform
  account: string
  url: string
  date: string
  status: AdStatus
  matchConfidence: number
  /** Кто подтвердил привязку к ЖК; null — гипотеза ИИ, в балл не входит */
  linkConfirmedBy?: string | null
  text: string
  ocr?: string | null
  image?: string | null
  price?: string | null
  signals: AdSignals
}

/** Снимок доказательств на момент создания сигнала — дальше не меняется */
export interface SignalEvidence {
  complex: Complex
  developer: Developer | null
  ads: SignalAd[]
  appeals: Appeal[]
  photos: SitePhoto[]
  /** Документы-основания: гарантия ЕО, разрешение МИО, банк (как EvidenceItem kind='document') */
  documents: EvidenceItem[]
}

/** Строка реестра сигналов */
export interface SignalSummary {
  id: number
  /** Человекочитаемый номер: S-0001 */
  code: string
  complexId: string
  complexName: string
  status: SignalStatus
  score: number
  zone: Zone
  needsCheck: boolean
  /** Коды подтверждённых факторов */
  hits: string[]
  createdAt: string
  createdBy: string
  decidedAt?: string | null
  decidedBy?: string | null
}

export interface Signal extends SignalSummary {
  summary: string
  factors: RiskFactor[]
  observations: Observation[]
  evidence: SignalEvidence
  modelVersion: string
  decisionComment?: string | null
  /** Имя файла выгрузки: signal-S-0001.pdf */
  fileName: string
  exportedAt?: string | null
  /** Журнал решений и событий по сигналу */
  journal: AuditEvent[]
}

// ---------- ИИ-анализ публикаций (ТЗ §6) ----------

export type TermKind = 'booking' | 'deposit' | 'down_payment' | 'installment' | 'ddu' | 'direct_payment' | 'price' | 'investment'

/** Условие оплаты, найденное в тексте или на скриншоте — гипотеза ИИ с дословной цитатой и уверенностью */
export interface Term {
  kind: TermKind
  /** По-русски: «Платная бронь», «Первоначальный взнос»… */
  label: string
  amount?: number | null
  currency?: 'KZT' | 'USD' | 'RUB' | null
  /** m2 — цена за квадратный метр */
  unit?: 'm2' | null
  percent?: number | null
  months?: number | null
  /** Дословный фрагмент исходного текста */
  quote: string
  from: 'text' | 'ocr'
  /** 0…1 */
  confidence: number
  /** Уверенность ниже порога lowConfidence — пометка «низкая уверенность» */
  low: boolean
  /** Предложение внести деньги (для цены — false) */
  money: boolean
}

export type ReasonFeature = 'name' | 'translit' | 'address' | 'account' | 'phone' | 'history' | 'district'

/** Почему ИИ считает, что публикация про этот ЖК */
export interface LinkReason {
  feature: ReasonFeature
  detail: string
  weight: number
}

/** Гипотеза сопоставления публикации с ЖК */
export interface LinkHypothesis {
  complexId: string
  complexName: string
  confidence: number
  low: boolean
  reasons: LinkReason[]
  /** Фрагмент, где найдено */
  quote?: string | null
}

/** Результат ИИ-анализа: условия оплаты, гипотезы ЖК (до hypothesesTop, по убыванию уверенности), найденные телефоны и адреса */
export interface Analysis {
  /** Версия движка, например saq-ai-1 */
  engine: string
  terms: Term[]
  hypotheses: LinkHypothesis[]
  signals: AdSignals
  phones: string[]
  addresses: string[]
  note?: string | null
}

/** Что анализируем: текст объявления, распознанный скриншот, аккаунт, площадка, ссылка */
export interface AnalyzeInput {
  text: string
  ocr?: string
  account?: string
  platform?: Platform
  url?: string
}

/** Анализ сохранённой публикации: без отклонённых гипотез и с решениями аналитика */
export interface AdAnalysis extends Analysis {
  termsReview: TermsReview | null
  termsReviewBy: string | null
  termsReviewAt: string | null
  linkConfirmedBy: string | null
  linkConfirmedAt: string | null
  rejectedLinks: string[]
}

/** Распознавание скриншота на сервере (Tesseract); engine none — OCR на сервере недоступен */
export interface OcrResult {
  text: string
  engine: 'tesseract' | 'none'
  /** Средняя уверенность слов 0…1 */
  confidence?: number | null
  languages: string[]
  durationMs: number
  version?: string | null
  /** Предобработка перед распознаванием: оттенки серого, увеличение, автоконтраст */
  preprocess?: string[]
  detail?: string | null
}

export interface OcrStatus {
  available: boolean
  engine: 'tesseract' | 'none'
  version?: string | null
  languages: string[]
  detail?: string | null
}

/** Прогон движка ИИ на кейсах shared/ai_cases.json — тексты, которых нет в демо-наборе */
export interface AiEvalCase {
  id: string
  title?: string | null
  text: string
  ocr?: string | null
  account?: string | null
  /** Ожидание: complexId (или noHypotheses — гипотез быть не должно), условия оплаты */
  expected: Record<string, unknown>
  /** Результат движка: первая гипотеза, все гипотезы, найденные условия */
  got: Record<string, unknown>
  ok: boolean
  /** Что разошлось */
  notes?: string | null
}

export interface AiEval {
  engine: string
  cases: AiEvalCase[]
  metrics: {
    termsFound: number
    termsExpected: number
    termsPrecision: number
    linkTop1: number
    linkCases: number
    falsePositives: number
  }
}
