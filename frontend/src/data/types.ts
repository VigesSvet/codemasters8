export type Zone = 'green' | 'yellow' | 'red'
export type Tri = 'yes' | 'no' | 'unknown'

export type Stage = 'котлован' | 'фундамент' | 'каркас' | 'кровля' | 'отделка' | 'введён'

/** Происхождение сущности (ТЗ §4, §7): demo — вымышленный сценарий из сида, source — запись открытого источника,
 *  analyst — добавлено аналитиком (публикация через ручной ввод), collected — собрано подключённым сборщиком
 *  или прислано жителем через Telegram-бота (source = 'bot') */
export type Origin = 'demo' | 'source' | 'analyst' | 'collected'

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
  origin?: Origin
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
  /** Где сверено основание и когда (без источника «нет» не превращается в R1) — общая сверка */
  source?: string
  checkedAt?: string
  /** Провенанс по каждому документу: где сверен, ссылка, когда. Нет записи — документ не проверялся */
  sources?: Partial<Record<BasisDoc, BasisSource>>
}

/** Документ основания привлечения средств */
export type BasisDoc = 'guarantee' | 'mio' | 'bvu'

/** Где и когда сверен документ основания */
export interface BasisSource {
  source: string
  url?: string | null
  checkedAt: string
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
  origin?: Origin
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
  origin?: Origin
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
  /** manual — ручной ввод, parser — сборщик или приём публикаций API, bot — заявка жителя через Telegram-бота, demo — сид */
  source: 'manual' | 'parser' | 'demo' | 'bot'
  status: AdStatus
  assignee?: string
  reach?: number
  origin?: Origin
  /** Когда публикацию получил сервер (сборщик, приём, бот), UTC ISO — не время публикации в источнике. Нет у демо и ручных */
  collectedAt?: string | null
  /** Сохранённые файлы публикации текущей версии (изображения и видео), адреса /api/media/{id}. Нет у демо и ручных */
  media?: MediaRef[]
  /** Анализ собранной публикации на сервере: очередь, идёт, готов, не выполнен. Нет у публикаций без приёма */
  processing?: Processing | null
}

// ---------- Сбор публикаций: файлы, анализ, версии (integration-spec §11.2) ----------

/** Файл публикации в хранилище сервера: url — /api/media/{id}; в <img>/<video> — только через mediaSrc */
export interface MediaRef {
  id: string
  kind: 'image' | 'video'
  mime?: string | null
  url: string
  /** cover — обложка, slide — слайд карусели, attachment — вложение */
  role?: string | null
}

export type ProcessingStatus = 'queued' | 'running' | 'done' | 'failed'

/** Состояние анализа собранной публикации: version — текущая версия в источнике, analyzedVersion — последняя проанализированная;
 *  stage — этап (media | frames | ocr | rules | events) во время анализа или этап, на котором анализ упал */
export interface Processing {
  status: ProcessingStatus
  version: number
  analyzedVersion?: number | null
  stage?: string | null
  error?: string | null
}

/** Версия публикации в источнике (история правок): новые первыми, current — актуальная */
export interface AdVersion {
  version: number
  collectedAt?: string | null
  text: string
  price?: string | null
  /** Ключи файлов этой версии (по ним видно, что поменялись изображения) */
  mediaKeys: string[]
  current: boolean
}

/** Ответ загрузки скриншота POST /api/media: url — /api/media/{id}, его и отправляем в image публикации */
export interface MediaUpload {
  id: string
  url: string
  mime: string
  size: number
}

// ---------- Подключения источников и запуски сбора (integration-spec §10–§11) ----------

/** Код подключения; порядок — как в реестре сервера */
export type ConnectorCode = 'instagram' | 'telegram' | 'tiktok' | 'krisha' | 'telegram_bot' | 'intake' | 'dgis' | 'openai' | 'olx'

/** collector — сборщик публикаций, intake — приём заявок и публикаций, geo — геосервис, oauth — ограниченный доступ (OLX),
 *  analyzer — анализатор */
export type ConnectorKind = 'collector' | 'intake' | 'geo' | 'oauth' | 'analyzer'
export type ConnectorGroup = 'collectors' | 'intake' | 'geo' | 'analyzer' | 'limited'

/** Состояние подключения (сохранённое + вычисленное на сервере), §10.4 */
export type ConnectorStatus =
  | 'not_configured' | 'unchecked' | 'checking' | 'queued' | 'running' | 'ready' | 'working' | 'partial'
  | 'waiting' | 'off' | 'error' | 'expired' | 'denied' | 'access_check' | 'rate_limited'

/** Статус запуска сбора или проверки, §10.5; конечные — все, кроме queued и running */
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'failed' | 'access_check' | 'rate_limited' | 'not_configured' | 'cancelled'

export type RunTrigger = 'schedule' | 'manual' | 'test'

/** Списки, которые задаёт администратор: аккаунты Instagram, каналы Telegram, профили TikTok, URL Krisha */
export type ConfigField = 'accounts' | 'channels' | 'profiles' | 'searchUrls' | 'cardUrls'

/** Настройки сбора подключения: списки источников и опции (TikTok — resultsPerPage, downloadVideos) */
export interface ConnectorConfig {
  accounts?: string[]
  channels?: string[]
  profiles?: string[]
  searchUrls?: string[]
  cardUrls?: string[]
  resultsPerPage?: number
  downloadVideos?: boolean
}

/** PATCH /api/sources/{code} — только администратор; author пишется в журнал */
export interface ConnectorPatch {
  author: string
  enabled?: boolean
  intervalMin?: number
  config?: ConnectorConfig
}

export interface RunCounts {
  seen: number
  new: number
  updated: number
  unchanged: number
  failed: number
  mediaSaved: number
  mediaFailed: number
}

export interface RunNote {
  at: string
  level: 'info' | 'warn' | 'error'
  text: string
}

/** Запуск сбора или проверки подключения («запуск №N») */
export interface CollectionRun {
  id: number
  /** Код подключения */
  source: ConnectorCode | string
  trigger: RunTrigger
  status: RunStatus
  /** Подпись статуса по-русски (сервер) */
  statusLabel: string
  requestedBy?: string | null
  createdAt?: string | null
  startedAt?: string | null
  finishedAt?: string | null
  counts: RunCounts
  /** ID запуска у поставщика (Apify) */
  externalRunId?: string | null
  /** Стоимость платного запуска, USD (TikTok через Apify) */
  costUsd?: number | null
  errorCode?: string | null
  error?: string | null
  /** Заметки запуска (до 100 на сервере): что пропущено, что упало */
  notes: RunNote[]
  /** Когда будет повтор (пауза по лимиту, повтор после ошибки) */
  retryAt?: string | null
}

export interface ConnectorCounts {
  /** Материалов из этого источника (записей приёма) */
  items: number
  /** Публикаций */
  ads: number
  /** Сохранённых файлов */
  media: number
}

/** Что можно делать с подключением: проверить, собрать, включить расписание, настроить списки, подключить аккаунт (OAuth) */
export interface ConnectorCaps {
  test: boolean
  collect: boolean
  toggle: boolean
  configure: boolean
  connect: boolean
}

/** Подключение источника (GET /api/sources/connectors): фактическое состояние с сервера, без секретов */
export interface Connector {
  code: ConnectorCode | string
  kind: ConnectorKind
  group: ConnectorGroup
  /** Площадка публикаций (instagram, telegram, tiktok, krisha, olx) — для значка */
  platform?: string | null
  name: string
  /** Через что подключено: «Meta Graph API · Business Discovery» */
  via: string
  status: ConnectorStatus
  statusLabel: string
  statusDetail?: string | null
  /** Сбор по расписанию включён */
  enabled: boolean
  /** Ключи и сессия есть на сервере */
  configured: boolean
  /** Каких переменных или файлов нет на сервере — имена, не значения */
  missing: string[]
  /** Где хранятся секреты (env:ИМЯ) — без значений */
  secretRefs: string[]
  config: ConnectorConfig & Record<string, unknown>
  configFields: ConfigField[]
  /** Дополнительные опции настройки (resultsPerPage, downloadVideos) */
  options: string[]
  intervalMin?: number | null
  /** [мин, макс] периода в минутах */
  intervalBounds?: number[] | null
  nextRunAt?: string | null
  lastSuccessAt?: string | null
  lastError?: string | null
  lastErrorAt?: string | null
  checkedAt?: string | null
  /** Несекретные факты последней проверки: аккаунт, срок доступа, кандидаты 2ГИС… */
  facts: Record<string, unknown>
  counts: ConnectorCounts
  lastRun?: CollectionRun | null
  /** Запуск, который сейчас в очереди или выполняется */
  activeRunId?: number | null
  capabilities: ConnectorCaps
  /** Ограничение источника — показывается рядом с карточкой */
  limitation?: string | null
  /** false — контракт внешнего адаптера не сверен (Telegram-бот) */
  contractVerified?: boolean | null
  /** Платный источник (TikTok через Apify) */
  paid: boolean
  /** Сбор остановлен после ошибки — до ручной проверки */
  stopped: boolean
}

/** Воркер сбора: жив ли (сигнал не старше 2 мин) */
export interface WorkerInfo {
  alive: boolean
  lastSeenAt?: string | null
  id?: string | null
}

export interface ConnectorsResponse {
  connectors: Connector[]
  worker: WorkerInfo
  /** На сервере задан SAQ_ADMIN_TOKEN — режим администратора доступен */
  adminConfigured: boolean
}

export interface CollectAllItem {
  code: ConnectorCode | string
  runId?: number | null
  /** Почему пропущен: «выключен», «не настроен», «уже идёт»… */
  reason?: string | null
}

/** POST /api/sources/collect-all: что поставлено в очередь, что пропущено и почему */
export interface CollectAllResult {
  queued: CollectAllItem[]
  skipped: CollectAllItem[]
}

/** POST /api/integrations/olx/start: адрес авторизации OLX (одноразовый, 10 минут) */
export interface OlxStart {
  authorizeUrl: string
  expiresAt?: string | null
}

// ---------- 2ГИС: сверка адреса (integration-spec §12.5) ----------

export type GeoTargetKind = 'complex' | 'registry'

/** Точность совпадения адреса у 2ГИС: exact — точный дом, number — отличается литера/корпус, near — ближайший дом,
 *  street — только улица */
export type GeoPrecision = 'exact' | 'number' | 'near' | 'street' | 'other'

/** Кандидат адреса из 2ГИС; координаты — lat/lon (у ЖК — lat/lng, у карты — [lng, lat]) */
export interface GeoCandidate {
  id: string
  name: string
  fullName?: string | null
  addressName?: string | null
  purpose?: string | null
  type?: string | null
  precision?: GeoPrecision | string | null
  lat: number
  lon: number
  city?: string | null
  /** Расстояние до точки в карточке, м (null — у объекта нет координат) */
  distanceM?: number | null
  /** Единственный точный дом в городе — подсказка, не выбор: ничего не принимается автоматически */
  recommended: boolean
}

/** candidates — ждёт решения аналитика; not_found — 2ГИС ничего не нашёл; accepted / rejected — решение принято */
export type GeoCheckStatus = 'candidates' | 'not_found' | 'accepted' | 'rejected'

/** Сверка адреса в 2ГИС: запрос, кандидаты, решение аналитика */
export interface GeoCheck {
  id: number
  targetKind: GeoTargetKind
  targetId: string
  provider: string
  query: string
  status: GeoCheckStatus
  candidates: GeoCandidate[]
  fetchedAt?: string | null
  /** Ответ из кэша — 2ГИС повторно не запрашивался */
  cached: boolean
  /** Кандидаты удалены по сроку хранения */
  purged: boolean
  requestedBy?: string | null
  chosenId?: string | null
  /** Координаты перенесены в карточку ЖК */
  applied: boolean
  /** Точка в карточке до решения */
  prevPoint?: { lat: number; lng: number } | null
  decidedBy?: string | null
  decidedAt?: string | null
  comment?: string | null
  /** До какого времени хранятся кандидаты */
  expiresAt?: string | null
}

/** Квота и кэш 2ГИС: запросов по ключу за период и за сегодня */
export interface GeoStatus {
  configured: boolean
  used: number
  limit?: number | null
  periodStart?: string | null
  usedToday: number
  dailyLimit?: number | null
  cacheDays: number
}

/** POST /api/geo/checks; force — обновить кэш (только администратор) */
export interface GeoCheckIn {
  targetKind: GeoTargetKind
  targetId: string
  query?: string
  author: string
  force?: boolean
}

/** Принять кандидата; apply — перенести координаты в карточку ЖК (только для ЖК) */
export interface GeoAcceptIn {
  candidateId: string
  author: string
  apply?: boolean
  comment?: string
}

export interface GeoRejectIn {
  author: string
  comment?: string
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


// ---------- Открытые данные (ТЗ §4): записи источников и объекты по ним — без риска, зон и сигналов ----------

/** Код источника: карточки Homeportal, отдельный раздел Homeportal, Казреестр (CSV Ashyq Data); demo — демо-набор */
export type SourceCode = 'homeportal' | 'homeportal_section' | 'ashyq' | 'demo'

/** Способ привлечения средств дольщиков по данным источника */
export type FundingMethod = 'guarantee' | 'mio' | 'bvu'

/** Запись открытого источника: нормализованные поля строки и её провенанс. Пустое поле — источник не предоставил значение */
export interface SourceRecord {
  id?: number
  /** Ключ объекта реестра, в который сгруппирована запись */
  key?: string
  source: SourceCode | string
  sourceName?: string | null
  /** Раздел источника, из которого взята запись */
  sourceSection?: string | null
  sourceUrl?: string | null
  sourceRecordId?: string | null
  /** Когда собраны данные (UTC ISO) */
  collectedAt?: string | null
  /** Дата загрузки у поставщика данных */
  providerLoadedAt?: string | null
  /** Когда запись загружена в систему (импорт), из какого файла и строки */
  importedAt?: string | null
  importFile?: string | null
  row?: number | null
  name: string
  address?: string | null
  developer?: string | null
  developerBin?: string | null
  /** Уполномоченная компания и её БИН */
  agent?: string | null
  agentBin?: string | null
  stage?: string | null
  fundingMethod?: FundingMethod | null
  /** Способ привлечения средств — как написал источник */
  fundingText?: string | null
  docNumber?: string | null
  docDate?: string | null
  start?: string | null
  plannedEnd?: string | null
  actNumber?: string | null
  actDate?: string | null
  /** Готовность по отчёту, % */
  readiness?: number | null
  reportPeriod?: string | null
  lat?: number | null
  lng?: number | null
  cadastral?: string | null
  site?: string | null
  /** Примечание источника */
  note?: string | null
  origin?: Origin
}

/** Объект открытых данных — записи источников, сгруппированные по нормализованному названию */
export interface RegistryObject {
  key: string
  name: string
  address?: string | null
  developer?: string | null
  developerBin?: string | null
  agent?: string | null
  fundingMethods: string[]
  stage?: string | null
  plannedEnd?: string | null
  lat?: number | null
  lng?: number | null
  /** Сколько записей источников */
  records: number
  /** Коды источников */
  sources: string[]
  collectedAt?: string | null
  /** sourceSection — раздел источника (только как атрибут, без оценки); coordsFarKm — координаты источника дальше 30 км от города */
  flags: { sourceSection?: string | null; coordsFarKm?: number | null }
  origin?: Origin
}

/** Реестр открытых данных: с координатами — на карту, без координат — отдельным списком */
export interface RegistryList {
  objects: RegistryObject[]
  withoutCoords: RegistryObject[]
}

/** Карточка объекта открытых данных: сам объект и все записи источников */
export interface RegistryCard {
  object: RegistryObject
  records: SourceRecord[]
}

/** Источник данных: что, сколько записей, когда собрано */
export interface SourceInfo {
  code: SourceCode | string
  name: string
  url?: string | null
  /** Записей в БД (у demo — число вымышленных ЖК) */
  records: number
  /** Объектов с записями этого источника (у demo — ЖК) */
  objects?: number
  /** Последний сбор */
  collectedAt?: string | null
  providerLoadedAt?: string | null
  note?: string | null
  /** Только у demo */
  counts?: { complexes: number; developers: number; ads: number; appeals: number }
}

/** Отчёт импорта файла открытых данных: дедупликация по (источник, ID в источнике) */
export interface ImportReport {
  file?: string | null
  format?: 'xlsx' | 'csv'
  /** Строк данных прочитано */
  read: number
  /** Новых записей */
  created: number
  /** Записей с изменившимися полями */
  updated: number
  /** Известные записи без изменений */
  unchanged?: number
  /** Строк пропущено: нет обязательного поля, неизвестный источник… */
  skipped: number
  /** Повторы (источник, ID) внутри файла */
  duplicates?: number
  /** Почему строки пропущены */
  errors?: string[]
  warnings?: string[]
  /** После импорта: записей и объектов всего, объектов без координат */
  records?: number
  objects?: number
  withoutCoords?: number
  /** Принято строк по кодам источников */
  sources?: Record<string, number>
  eventId?: number
}
