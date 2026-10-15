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
  matchConfidence: number
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

/** Вид события журнала: данные и источники, статус, привязка, комментарий, запрос сведений, сигнал, выгрузка, системное */
export type AuditKind = 'system' | 'data' | 'status' | 'link' | 'comment' | 'request' | 'signal' | 'export'

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
