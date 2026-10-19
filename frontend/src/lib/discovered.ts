// Объекты из рекламы (сервер: app.discovery): ЖК, которых нет ни в справочнике, ни в открытом реестре, — их называет реклама.
// Аналитик решает по каждому: создать карточку ЖК (origin «advertised»), привязать к существующему ЖК или отклонить с причиной.
// 2ГИС запрашивается только по кнопке «Найти в 2ГИС»; кандидат — подсказка, точку выбирает аналитик при создании карточки.
import type { Complex, Developer, Discovered, DiscoveredCreateIn, DiscoveredGeoResult, DiscoveredSaleSignals, DiscoveredStageHints, DiscoveredStatus, GeoCandidate, GeoCheck, LlmStage } from '../data/types'
import { adminCall } from './admin'
import { apiGet, apiPost, isRouteMissing, orNoRoute } from './api'
import { rankComplex } from './find'
import { signalName } from './screening'

// ---------- Запросы ----------

/** Все объекты из рекламы (новые первыми); null — на сервере нет этой функции (старая версия) или ответ не того вида.
 *  Нет связи и ошибки сервера пробрасываются — страница покажет их текст */
export async function listDiscovered(): Promise<Discovered[] | null> {
  try {
    const got = await apiGet<Discovered[]>('/discovered')
    return Array.isArray(got) ? got : null
  } catch (e) {
    if (isRouteMissing(e)) return null
    throw e
  }
}

export const getDiscovered = (id: number) => orNoRoute(apiGet<Discovered>(`/discovered/${id}`))

/** «Найти в 2ГИС»: по названию и, если реклама называла адрес, по адресу. 201 — новый запрос, 200 — из кэша.
 *  force — мимо кэша, только администратор (токен уходит лишь в этом случае). 503 — 2ГИС не настроен, 429 — лимит */
export function runDiscoveredGeo(id: number, author: string, force = false): Promise<DiscoveredGeoResult> {
  return force
    ? adminCall((h) => apiPost<DiscoveredGeoResult>(`/discovered/${id}/geo`, { author, force: true }, h))
    : orNoRoute(apiPost<DiscoveredGeoResult>(`/discovered/${id}/geo`, { author }))
}

/** Создать карточку ЖК по объекту → ЖК (origin «advertised»). 409 — уже привязан или ЖК с таким названием есть, 422 — кандидата нет */
export const createFromDiscovered = (id: number, body: DiscoveredCreateIn) => orNoRoute(apiPost<Complex>(`/discovered/${id}/create-complex`, body))

/** «Это существующий ЖК»: название объекта — другое написание ЖК */
export const linkDiscovered = (id: number, author: string, complexId: string) =>
  orNoRoute(apiPost<Discovered>(`/discovered/${id}/link`, { author, complexId }))

/** «Не ЖК — отклонить» с причиной */
export const dismissDiscovered = (id: number, author: string, reason: string) =>
  orNoRoute(apiPost<Discovered>(`/discovered/${id}/dismiss`, { author, reason: reason.trim() }))

// ---------- Вкладки ----------

export const DISCOVERED_TABS: readonly [DiscoveredStatus, string][] = [['new', 'Новые'], ['linked', 'Привязаны'], ['dismissed', 'Отклонены']]
export const parseDiscoveredTab = (v?: string | null): DiscoveredStatus => (v === 'linked' || v === 'dismissed' ? v : 'new')
/** Офлайн-копия: список — из неё, решения по объектам — с сервером */
export const NO_DISCOVERED = 'Офлайн-копия только для просмотра — решения по объектам из рекламы доступны с сервером (запустите ./dev.sh)'

/** Число объектов по статусам */
export function countDiscovered(xs: readonly Pick<Discovered, 'status'>[]): Record<DiscoveredStatus, number> {
  const out = { new: 0, linked: 0, dismissed: 0 }
  for (const x of xs) if (x.status in out) out[x.status] += 1
  return out
}

// ---------- Подписи карточки объекта ----------

export const STAGE_LABEL: Record<LlmStage, string> = { under_construction: 'строится', completed: 'сдан', unknown: 'не ясно' }

/** «Стадия по рекламе: строится — 2, сдан — 1»; без упоминаний стадии — «в рекламе стадия не названа» */
export function stageLine(h?: DiscoveredStageHints | null): string {
  const parts = ([['under_construction', h?.underConstruction ?? 0], ['completed', h?.completed ?? 0]] as const)
    .filter(([, n]) => n > 0).map(([k, n]) => `${STAGE_LABEL[k]} — ${n}`)
  return parts.length ? parts.join(', ') : 'в рекламе стадия не названа'
}

/** «бронь — 2 · рассрочка — 1» по убыванию; пусто — '' */
export function signalTypesLine(types?: Record<string, number> | null): string {
  return Object.entries(types ?? {}).filter(([, n]) => typeof n === 'number' && n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k, n]) => `${signalName(k)} — ${n}`).join(' · ')
}

/** «Признаки приёма денег — в 2 из 5 публикаций (за 120 дн. — 1)»; рекламы нет — «Публикаций с упоминанием нет» */
export function saleLine(s?: DiscoveredSaleSignals | null, windowDays = 120): string {
  if (!s || !s.ads) return 'Публикаций с упоминанием нет'
  if (!s.concerning) return `Признаков приёма денег нет — ${s.ads} публ.`
  return `Признаки приёма денег — в ${s.concerning} из ${s.ads} публ. (за ${windowDays} дн. — ${s.recentConcerning})`
}

export const SOURCE_LABEL: Record<string, string> = { parser: 'сборщик', bot: 'заявки жителей', manual: 'ручной ввод' }
/** «сборщик — 3 · заявки жителей — 1»; нули не показываем */
export function sourcesLine(sources?: Record<string, number> | null): string {
  return Object.entries(sources ?? {}).filter(([, n]) => typeof n === 'number' && n > 0)
    .map(([k, n]) => `${SOURCE_LABEL[k] ?? k} — ${n}`).join(' · ')
}

/** Решение по объекту: «Привязан к ЖК «X» — Иванов» / «Отклонён: причина — Иванов»; новый — null */
export function decisionText(o: Pick<Discovered, 'status' | 'complexName' | 'complexId' | 'note' | 'decidedBy'>): string | null {
  const by = o.decidedBy ? ` — ${o.decidedBy}` : ''
  if (o.status === 'linked') return `Привязан к ЖК «${o.complexName || o.complexId || '—'}»${by}`
  if (o.status === 'dismissed') return `Отклонён${o.note ? `: ${o.note}` : ''}${by}`
  return null
}

// ---------- Кандидаты 2ГИС ----------

export interface GeoPick { check: GeoCheck; cand: GeoCandidate }

/** Кандидаты из сверок объекта: без удалённых по сроку и решённых не в пользу кандидата, без повторов; точный дом —
 *  первым, затем «рекомендуется», дальше — порядок 2ГИС. Сервер примет кандидата только из сверки «ждёт решения» */
export function geoPicks(checks?: readonly GeoCheck[] | null): GeoPick[] {
  const seen = new Set<string>()
  const out: GeoPick[] = []
  for (const check of checks ?? []) {
    if (check.purged) continue
    for (const cand of check.candidates ?? []) {
      const usable = check.status === 'candidates' || (check.status === 'accepted' && check.chosenId === cand.id)
      if (!usable || seen.has(cand.id)) continue
      seen.add(cand.id)
      out.push({ check, cand })
    }
  }
  const rank = (p: GeoPick) => (p.cand.precision === 'exact' ? 0 : 2) + (p.cand.recommended ? 0 : 1)
  return out.map((p, i) => ({ p, i })).sort((a, b) => rank(a.p) - rank(b.p) || a.i - b.i).map((x) => x.p)
}

/** Сверки из ответа «Найти в 2ГИС» — впереди сохранённых, без повторов по id */
export function mergeChecks(fresh: readonly GeoCheck[], saved: readonly GeoCheck[]): GeoCheck[] {
  const ids = new Set(fresh.map((c) => c.id))
  return [...fresh, ...saved.filter((c) => !ids.has(c.id))]
}

// ---------- Форма «Создать карточку ЖК» ----------

export interface CreateForm {
  name: string
  address: string
  developerName: string
  developerBin: string
  /** Кандидат 2ГИС, точка которого станет координатами карточки; '' — без координат (сверка позже) */
  candidateId: string
}

/** Начальные значения формы: название и адрес из рекламы, кандидат — выбранный или никакой */
export const createForm = (o: Pick<Discovered, 'name' | 'addressHint'>, candidateId = ''): CreateForm =>
  ({ name: o.name, address: o.addressHint ?? '', developerName: '', developerBin: '', candidateId })

/** Ошибка формы по-русски; null — можно отправлять */
export function createProblem(f: CreateForm): string | null {
  if (f.name.trim().length < 2) return 'Укажите название ЖК'
  const bin = f.developerBin.trim()
  if (bin && !/^\d{12}$/.test(bin)) return 'БИН застройщика — 12 цифр'
  return null
}

/** Тело запроса: пустые поля не отправляем; координаты — от кандидата на сервере (candidateId) */
export function createBody(f: CreateForm, author: string): DiscoveredCreateIn {
  const body: DiscoveredCreateIn = { author, name: f.name.trim() }
  if (f.address.trim()) body.address = f.address.trim()
  if (f.developerName.trim()) body.developerName = f.developerName.trim()
  if (f.developerBin.trim()) body.developerBin = f.developerBin.trim()
  if (f.candidateId) body.candidateId = f.candidateId
  return body
}

// ---------- «Это существующий ЖК» ----------

/** ЖК для выбора: пустой запрос — по названию; иначе — найденные (лучшие первыми), не больше limit */
export function pickComplexes(q: string, complexes: readonly Complex[], developers: readonly Developer[], limit = 30): Complex[] {
  const dev = new Map(developers.map((d) => [d.id, d]))
  const query = q.trim()
  if (!query) return [...complexes].sort((a, b) => a.name.localeCompare(b.name, 'ru')).slice(0, limit)
  return complexes.map((c) => ({ c, r: rankComplex(query, c, dev.get(c.developerId)) })).filter((x) => x.r > 0)
    .sort((a, b) => b.r - a.r || a.c.name.localeCompare(b.c.name, 'ru')).slice(0, limit).map((x) => x.c)
}

/** Подсказки причины отклонения */
export const DISMISS_PRESETS: readonly string[] = [
  'Не ЖК: подборка, агентство или общая реклама',
  'Объект не в Усть-Каменогорске',
  'Ошибка распознавания названия',
]
