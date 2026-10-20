export type Zone = 'green' | 'yellow' | 'red'
export type Tri = 'yes' | 'no' | 'unknown'

export type Stage = 'котлован' | 'фундамент' | 'каркас' | 'кровля' | 'отделка' | 'введён'

/** Происхождение сущности (ТЗ §4, §7): demo — вымышленный сценарий прежнего демо-набора (только старые БД), source — запись открытого источника,
 *  analyst — добавлено аналитиком (публикация через ручной ввод), collected — собрано подключённым сборщиком
 *  или прислано жителем через Telegram-бота (source = 'bot'), registry — карточка ЖК или застройщик, созданные по открытому
 *  реестру (сведения источника, аналитиком не проверены), advertised — ЖК или застройщик, известные только по рекламе: в открытом
 *  реестре их нет, карточку создал аналитик или импорт, сведения не проверены, формальный риск не оценивается */
export type Origin = 'demo' | 'source' | 'analyst' | 'collected' | 'registry' | 'advertised'

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
  /** Телефоны отдела продаж — признак сопоставления публикации с ЖК */
  phones?: string[]
  origin?: Origin
  /** «Проверка по рекламе» по ЖК застройщика (сервер, в офлайн-копии — из неё); нет — сервер старой версии */
  screening?: DeveloperScreening | null
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

/** Где и когда сверен документ основания; checkedBy — кто сверил (сверка аналитиком на сервере), ref — номер и дата
 *  документа или банк, comment — пояснение */
export interface BasisSource {
  source: string
  url?: string | null
  checkedAt: string
  checkedBy?: string | null
  ref?: string | null
  comment?: string | null
}

/** Документ основания, который называет открытый реестр: подсказка для «Подтвердить — совпадает с источником» */
export interface BasisSuggestion {
  /** Номер и дата документа («№ KZ40VWE00002526 от 15.07.2025») */
  ref: string
  /** Короткое название источника для сверки («Homeportal») */
  source: string
  /** Как у источника («Homeportal — карточки ЖК») */
  sourceName: string
  url?: string | null
  records: number
}

/** Сверка с опубликованным перечнем ГАСК (O1) аналитиком */
export interface PublishedListCheck {
  value: Tri
  source: string
  url?: string | null
  checkedAt: string
  checkedBy: string
  comment?: string | null
}

/** «Обращений нет — сверено»: где проверено и когда (R4 у карточек реестра и рекламы) */
export interface AppealsCheck {
  source: string
  checkedAt: string
  checkedBy: string
  comment?: string | null
}

/** Значение готовности с источником и датой — для R5 нужны оба; by/at — кто и когда внёс (сверка аналитиком) */
export interface ReadinessValue {
  value: number
  source: string
  date: string
  by?: string | null
  at?: string | null
  comment?: string | null
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
  /** Источник снимка (дрон, камера, выезд) — как указал аналитик */
  source: string
  activity: 'есть' | 'нет' | 'низкая' | 'нет сведений'
  note?: string
  /** Снимок, загруженный аналитиком: файл /api/media/{mediaId}, кто и когда загрузил */
  mediaId?: string
  by?: string
  at?: string
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
  /** null — координат нет (объект реестра без координат, ЖК по рекламе до сверки в 2ГИС) */
  lng: number | null
  lat: number | null
  /** '' — стадия неизвестна (карточка из открытого реестра: «В строительстве / Завершён» источника — фильтр, а не стадия) */
  stage: Stage | ''
  /** Подтверждённая готовность, % (совпадает с readiness.confirmed.value) */
  progress: number
  /** 0 — нет сведений (карточка из открытого реестра) */
  floors: number
  flats: number
  /** '' — нет сведений (у карточки из реестра — если записи источника расходятся или объект завершён по источнику) */
  start: string
  plannedEnd: string
  basis: Basis
  readiness: Readiness
  inPublishedList: Tri
  cover: string
  photos: SitePhoto[]
  idleDays: number
  origin?: Origin
  /** Основание привлекать деньги дольщиков по открытым данным или сверке аналитика (сервер) */
  permit?: Permit | null
  /** «Проверка по рекламе» — предварительная автоматическая оценка, не вывод о нарушении (сервер) */
  screening?: Screening | null
  /** Объекты из рекламы, привязанные к ЖК: другие названия, аккаунты, ссылки */
  advert?: Advert | null
  /** Сверка с перечнем ГАСК аналитиком (сервер); null — не сверялось */
  publishedListCheck?: PublishedListCheck | null
  /** «Обращений нет — сверено» (сервер); null — не сверялось */
  appealsCheck?: AppealsCheck | null
  /** Документы основания по открытому реестру — подсказка для сверки в один клик (только карточки реестра) */
  basisSuggest?: Partial<Record<BasisDoc, BasisSuggestion>> | null
  /** Решение аналитика по «Проверке по рекламе» («сдан» / «проверено — замечаний нет», сервер); null — оценка автоматическая */
  decision?: ScreeningDecision | null
  /** Фото объекта (сервер: app.gallery), обложка первой; нет поля — сервер старой версии, обложка — cover */
  gallery?: GalleryPhoto[]
  /** Есть карточка Homeportal в записях реестра — фото можно подгрузить оттуда */
  homeportal?: boolean
}

/** operator — загрузил оператор, site — снимок площадки фото-мониторинга, homeportal — фото карточки ЖК на Homeportal (фото
 *  или визуализация застройщика), ad — изображение рекламы ЖК */
export type GalleryKind = 'operator' | 'site' | 'homeportal' | 'ad'

export interface GalleryPhoto {
  src: string
  kind: GalleryKind
  /** Откуда: «Фото оператора», «Homeportal», «Instagram, @account», источник снимка площадки */
  source: string
  date?: string | null
  by?: string | null
  note?: string | null
  /** Страница источника (Homeportal) */
  url?: string | null
  adId?: string | null
  mediaId?: string | null
  /** Публикация отнесена к ЖК гипотезой — привязка не подтверждена аналитиком */
  hypothesis?: boolean
  /** Закреплённая обложка карточки */
  cover?: boolean
}

// ---------- «Проверка по рекламе» (сервер: app.screening) ----------

/** ok — в реестре, замечаний нет; check — требует проверки; suspect — подозрение: продажа без разрешения; unknown — нет данных */
export type ScreeningStatus = 'ok' | 'check' | 'suspect' | 'unknown'
/** permitted — основание есть по открытым данным или сверке; not_permitted — раздел «Без разрешения на дольщиков» или сверенное
 *  «нет»; unknown — сведений нет. Сведения источника — не юридический вывод */
export type PermitStatus = 'permitted' | 'not_permitted' | 'unknown'
/** hypothesis — по гипотезам ИИ (привязки не подтверждены); confirmed — по привязкам аналитика и открытым данным */
export type ScreeningBasis = 'hypothesis' | 'confirmed'
/** Чем обоснован статус ok, если не реестром: commissioned — аналитик подтвердил ввод в эксплуатацию («Дом сдан — замечаний
 *  нет»), analyst — «Проверено — замечаний нет»; null — автоматическая оценка */
export type ScreeningVariant = 'commissioned' | 'analyst'
/** Вид решения аналитика: «Объект сдан» или «Проверено — замечаний нет» */
export type DecisionKind = 'commissioned' | 'no_issues'

export interface Permit {
  status: PermitStatus
  text: string
  /** homeportal | homeportal_section | ashyq | analyst */
  source?: string | null
  url?: string | null
  /** Объект открытых данных (/registry/{key}) */
  registryKey?: string | null
  /** Фильтр источника «В строительстве / Завершён», не стадия */
  registryStage?: 'completed' | 'under_construction' | null
  /** Сверено аналитиком, а не только сведения источника */
  verified: boolean
}

/** Причина оценки. Порядок сервера: причины решения аналитика (kind 'decision') — первыми, затем строка основания
 *  (permit.text), затем остальные — по рекламе, с цитатой и публикацией */
export interface ScreeningReason {
  text: string
  adId?: string | null
  quote?: string | null
  /** booking | presale | ddu | installment | down_payment | money | sale | price | … */
  signal?: string | null
  /** 'decision' — сведения решения аналитика или «новая реклама после решения» */
  kind?: 'decision' | null
}

export interface ScreeningCounts {
  /** Публикаций, отнесённых к ЖК (без отклонённых) */
  ads: number
  /** Из них свежих (окно windowDays) с признаком приёма денег */
  concerning: number
  /** Дата последней публикации (местное время) */
  lastAdAt?: string | null
  windowDays: number
}

export interface Screening {
  status: ScreeningStatus
  label: string
  /** ok по решению аналитика: «сдан» или «проверено»; нет (сервер старой версии) или null — автоматическая оценка */
  variant?: ScreeningVariant | null
  reasons: ScreeningReason[]
  counts: ScreeningCounts
  basis: ScreeningBasis
  inRegistry: boolean
  disclaimer: string
}

/** Новая реклама после решения, которая его сняла */
export interface DecisionTrigger { adId: string; date?: string | null; signal?: string | null; quote?: string | null }

/** Решение аналитика по ЖК: active — действует; reopened — снято автоматически новой рекламой (видно как история) */
export interface ScreeningDecision {
  id: number
  kind: DecisionKind
  status: 'active' | 'reopened'
  /** «Дом сдан — замечаний нет» | «Проверено — замечаний нет» */
  label: string
  by: string
  /** Когда записано, UTC ISO */
  at?: string | null
  actNumber?: string | null
  /** Дата акта ввода, YYYY-MM-DD */
  actDate?: string | null
  source: string
  sourceUrl?: string | null
  reason?: string | null
  comment?: string | null
  reopenedBy?: DecisionTrigger | null
}

export interface Advert {
  discoveredIds: number[]
  aliases: string[]
  accounts: string[]
  sourceUrls: string[]
  ads: number
}

export interface ScreeningWorst { complexId: string; name: string; status: ScreeningStatus; label: string }

export interface DeveloperScreening {
  status: ScreeningStatus
  label: string
  reasons: ScreeningReason[]
  counts: { complexes: number; ok: number; check: number; suspect: number; unknown: number }
  /** Худшие ЖК застройщика (подозрение первыми) */
  worst: ScreeningWorst[]
  basis: ScreeningBasis
  disclaimer: string
}

/** GET /api/stats — только то, что использует интерфейс */
export interface Stats {
  screening?: {
    complexes: Record<ScreeningStatus, number>
    developers: Record<ScreeningStatus, number>
    windowDays: number
    disclaimer: string
    discovered: { new: number; linked: number; dismissed: number }
    /** ЖК «без замечаний» по решению аналитика: сдан, проверено; снятые новой рекламой */
    decisions?: { commissioned: number; analyst: number; reopened: number }
  } | null
}

// ---------- Объекты из рекламы (сервер: app.discovery) ----------

export type DiscoveredStatus = 'new' | 'linked' | 'dismissed'

export interface DiscoveredQuote {
  adId: string
  quote: string
  name: string
  address?: string | null
  /** parser | bot (заявка жителя) | manual */
  source: string
  date?: string | null
  platform?: string | null
  account?: string | null
}

export interface DiscoveredStageHints {
  underConstruction: number
  completed: number
  unknown: number
  quotes: { adId: string; value: LlmStage; quote: string }[]
}

export interface DiscoveredSaleSignals {
  ads: number
  /** С признаком приёма денег (любой давности) */
  concerning: number
  /** Из них в окне windowDays */
  recentConcerning: number
  /** Вид признака → публикаций */
  types: Record<string, number>
  lastAdAt?: string | null
}

/** ЖК, которого нет в справочнике и открытом реестре, — найден в рекламе (или импортирован) */
export interface Discovered {
  id: number
  name: string
  normKey: string
  aliases: string[]
  addressHint?: string | null
  status: DiscoveredStatus
  origin: 'ads' | 'import'
  complexId?: string | null
  complexName?: string | null
  firstSeen?: string | null
  lastSeen?: string | null
  adsCount: number
  accounts: string[]
  sourceUrls: string[]
  /** Причина отклонения или примечание импорта */
  note?: string | null
  decidedBy?: string | null
  decidedAt?: string | null
  /** parser | bot | manual → упоминаний */
  sources: Record<string, number>
  /** До трёх, новые первыми */
  quotes: DiscoveredQuote[]
  stageHints: DiscoveredStageHints
  saleSignals: DiscoveredSaleSignals
  /** Последние сверки 2ГИС, новые первыми */
  geo: GeoCheck[]
  screening?: Screening | null
}

/** POST /api/discovered/{id}/geo: сверки по названию и, если реклама называла адрес, по адресу; errors — что не выполнилось */
export interface DiscoveredGeoResult {
  checks: GeoCheck[]
  errors: { query: string; status: number; detail: string }[]
}

/** POST /api/discovered/{id}/create-complex: координаты — парой; без них, но с candidateId — точка кандидата 2ГИС */
export interface DiscoveredCreateIn {
  author: string
  name: string
  address?: string
  lat?: number
  lng?: number
  developerName?: string
  developerBin?: string
  candidateId?: string
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
  /** Внесено и проверено аналитиком: комментарий, кто и когда внёс, кто решил и почему */
  comment?: string | null
  createdBy?: string | null
  createdAt?: string | null
  decidedBy?: string | null
  decisionComment?: string | null
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
  /** manual — ручной ввод, parser — сборщик или приём публикаций API, bot — заявка жителя через Telegram-бота, demo — прежний демо-набор */
  source: 'manual' | 'parser' | 'demo' | 'bot'
  status: AdStatus
  assignee?: string
  reach?: number
  origin?: Origin
  /** Когда публикацию получил сервер (сборщик, приём, бот), UTC ISO — не время публикации в источнике. Нет у ручных */
  collectedAt?: string | null
  /** Сохранённые файлы публикации текущей версии (изображения и видео), адреса /api/media/{id} (в офлайн-копии — /snapshot-media/…).
   *  Нет у ручных */
  media?: MediaRef[]
  /** Анализ собранной публикации на сервере: очередь, идёт, готов, не выполнен. Нет у публикаций без приёма */
  processing?: Processing | null
  /** Последний анализ ИИ — гипотезы для аналитика. Нет — анализа не было (ключ не задан, ручной ввод) */
  llm?: LlmAnalysis | null
}

// ---------- Анализ ИИ (Claude, этап «llm» анализа публикации) ----------

/** Вид признака продажи в ответе модели */
export type LlmSignalType = 'sale' | 'presale' | 'booking' | 'installment' | 'down_payment' | 'ddu' | 'price' | 'discount' | 'keys_handover' | 'other'
/** done — результат есть; refused — модель отказалась; failed — не выполнен (retryAt — ждёт повтора); skipped — не отправлялся */
export type LlmStatus = 'done' | 'refused' | 'failed' | 'skipped'

/** Элемент ответа: quote — дословно из текста публикации или OCR (сервер сверил), source — откуда цитата */
export interface LlmSignal { type: LlmSignalType; quote: string; source?: 'text' | 'ocr' }
export interface LlmAmount { value: number; currency: string; kind: string; quote: string; source?: 'text' | 'ocr' }
/** Кандидат ЖК — гипотеза: complexName — из справочника сервера, не из ответа модели */
export interface LlmCandidate { complexId: string; complexName: string; confidence: 'low' | 'medium' | 'high'; quote: string; source?: 'text' | 'ocr' }
/** ЖК, который называет публикация: название и цитата сверены с материалом, адрес — дословно или пустой. Может не быть в справочнике
 *  (тогда это объект из рекламы) */
export interface LlmMention { name: string; address?: string; quote: string; source?: 'text' | 'ocr' }
/** Стадия рекламируемого объекта по материалу; без дословной цитаты — unknown */
export type LlmStage = 'under_construction' | 'completed' | 'unknown'
export interface LlmObjectStage { value: LlmStage; quote?: string; source?: 'text' | 'ocr' | null }
/** Норма Закона РК «О долевом участии в жилищном строительстве», к которой может относиться публикация (shared/law.json):
 *  гипотеза модели, не вывод о нарушении. Статья — из правового контекста, quote — дословно из публикации (сервер сверил),
 *  part — номер пункта или пусто */
export interface LlmLegalRef { article: '7' | '8' | '18' | '23'; part?: string; why?: string; quote: string; source?: 'text' | 'ocr' }

/** Анализ ИИ: только гипотезы — статус, привязку и вывод о нарушении модель не задаёт.
 *  failed / refused / skipped — «анализ ИИ не выполнен», а не «риска нет» */
export interface LlmAnalysis {
  status: LlmStatus
  provider: 'anthropic'
  model: string
  servedModel?: string | null
  promptVersion: string
  contentVersion?: number | null
  saleSignals: LlmSignal[]
  amounts: LlmAmount[]
  complexCandidates: LlmCandidate[]
  /** saq-llm-2: ЖК, названные в публикации (в том числе вне справочника); у старых ответов — пусто или нет поля */
  mentionedComplexes?: LlmMention[]
  /** saq-llm-2: стадия объекта по материалу; у старых ответов — null */
  objectStage?: LlmObjectStage | null
  /** saq-llm-3: нормы закона, к которым может относиться публикация, — гипотезы; у старых ответов — пусто или нет поля */
  legalRefs?: LlmLegalRef[]
  uncertainties: string[]
  summary?: string | null
  /** Сколько элементов ответа сервер отбросил: quote — цитаты нет в тексте, complex — ЖК не из справочника, amount — не число, limit — сверх предела */
  dropped: Record<string, number>
  error?: string | null
  retryAt?: string | null
  attempts: number
  refusalCategory?: string | null
  /** Ответила резервная модель Anthropic после отказа основной (fallbacks: default) */
  fallback: boolean
  inputTokens: number
  outputTokens: number
  costUsd: number
  createdAt?: string | null
  updatedAt?: string | null
}

// ---------- Сбор публикаций: файлы, анализ, версии (integration-spec §11.2) ----------

/** Речь в ролике (faster-whisper на сервере): done — расшифровка есть (она же — абзац «Речь в ролике: …» в ocr публикации);
 *  empty — нет звука или речи; unavailable — нет движка, ffmpeg или модели; failed — сбой; skipped — выключено, ролик длиннее
 *  предела или не уложился по времени. language — ru | kk, audioS — секунд звука */
export interface MediaSpeech {
  status: 'done' | 'empty' | 'unavailable' | 'failed' | 'skipped'
  text?: string | null
  engine?: string | null
  model?: string | null
  language?: string | null
  audioS?: number | null
  detail?: string | null
}

/** Файл публикации в хранилище сервера: url — /api/media/{id}; в <img>/<video> — только через mediaSrc */
export interface MediaRef {
  id: string
  kind: 'image' | 'video'
  mime?: string | null
  url: string
  /** cover — обложка, slide — слайд карусели, attachment — вложение */
  role?: string | null
  /** только у видео, речь которого распознавалась */
  speech?: MediaSpeech
}

export type ProcessingStatus = 'queued' | 'running' | 'done' | 'failed'

/** Состояние анализа собранной публикации: version — текущая версия в источнике, analyzedVersion — последняя проанализированная;
 *  stage — этап (media | frames | speech | ocr | rules | llm | events) во время анализа или этап, на котором анализ упал */
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
export type ConnectorCode = 'instagram' | 'telegram' | 'tiktok' | 'krisha' | 'telegram_bot' | 'intake' | 'dgis' | 'anthropic' | 'olx'

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
  /** true — запрос с верным токеном администратора: полные сведения; false — вид для посетителя (без способа подключения,
   *  имён ключей, настроек, пояснений, ошибок, фактов, автора, стоимости и заметок запусков); нет — сервер старой версии */
  admin?: boolean
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

/** complex — карточка ЖК, registry — объект открытых данных, discovered — объект из рекламы */
export type GeoTargetKind = 'complex' | 'registry' | 'discovered'

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
  /** false — риск не оценивается: карточка из открытого реестра без проверенных сведений (балл и зону не показывать) */
  assessed?: boolean
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
 *  ИИ(гипотеза и решения аналитика по ней), предупреждение жителям, подключение источника(статус проверки, настройки,
 *  аккаунт - только администратору) */
export type AuditKind = 'system' | 'data' | 'status' | 'link' | 'comment' | 'request' | 'signal' | 'export' | 'ai' | 'alert' | 'connector'

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

/** Прогон движка ИИ на кейсах shared/ai_cases.json — синтетические тестовые тексты, не из собранной рекламы */
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

/** Код источника: карточки Homeportal, отдельный раздел Homeportal, Казреестр (CSV Ashyq Data); demo — прежний демо-набор (старые БД);
 *  snapshot — офлайн-копия стартового набора (только во фронтенде, когда сервер недоступен) */
export type SourceCode = 'homeportal' | 'homeportal_section' | 'ashyq' | 'demo' | 'snapshot'

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
  /** Записей в БД (у demo — число вымышленных ЖК, у snapshot — публикаций копии) */
  records: number
  /** Объектов с записями этого источника (у demo и snapshot — ЖК) */
  objects?: number
  /** Последний сбор */
  collectedAt?: string | null
  providerLoadedAt?: string | null
  note?: string | null
  /** Только у demo и snapshot */
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
