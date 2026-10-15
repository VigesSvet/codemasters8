// Журнал действий (T-16, T-17): подписи видов событий, темы запроса сведений, формат времени, инициалы авторов.
import type { AdStatus, Analyst, AuditEvent, AuditKind, Complex } from '../data/types'
import { IcBolt, IcCheck, IcComment, IcDatabase, IcDownload, IcFlag, IcLink, IcSend, IcSparkSearch } from '../ui/icons'

export const SYSTEM = 'Система'

/** Вид события: подпись, цвет метки (классы .tag), иконка */
export const AUDIT_KIND: Record<AuditKind, { name: string; tag: string; Icon: typeof IcCheck }> = {
  system: { name: 'система', tag: 'gray', Icon: IcBolt },
  data: { name: 'данные', tag: 'green', Icon: IcDatabase },
  status: { name: 'решение', tag: 'blue', Icon: IcCheck },
  link: { name: 'привязка', tag: 'gray', Icon: IcLink },
  comment: { name: 'комментарий', tag: 'gray', Icon: IcComment },
  request: { name: 'запрос сведений', tag: 'yellow', Icon: IcSend },
  signal: { name: 'сигнал', tag: 'red', Icon: IcFlag },
  export: { name: 'выгрузка', tag: 'gray', Icon: IcDownload },
  ai: { name: 'ИИ', tag: 'blue', Icon: IcSparkSearch },
}
export const kindOf = (e: Pick<AuditEvent, 'kind'>) => AUDIT_KIND[e.kind] ?? AUDIT_KIND.system

/** Группы фильтра журнала объекта */
export type AuditGroup = 'all' | 'data' | 'ads' | 'signals' | 'requests'
export const AUDIT_GROUPS: [AuditGroup, string][] = [
  ['all', 'Все'], ['data', 'Данные и источники'], ['ads', 'Публикации'], ['signals', 'Сигналы'], ['requests', 'Запросы'],
]
export const inGroup = (e: AuditEvent, g: AuditGroup) =>
  g === 'all' ? true
    : g === 'data' ? e.kind === 'data'
      : g === 'signals' ? e.kind === 'signal' || e.kind === 'export' || (e.signalId != null && e.kind !== 'request')
        : g === 'requests' ? e.kind === 'request'
          : !!e.adId && e.kind !== 'signal' && e.kind !== 'export'

/** Темы внутреннего запроса сведений — как в контракте POST /api/complexes/{id}/requests */
export type RequestTopic = 'basis' | 'readiness' | 'appeals' | 'list' | 'other'
export const REQUEST_TOPICS: [RequestTopic, string][] = [
  ['basis', 'основание привлечения средств (гарантия ЕО / разрешение МИО / банк)'],
  ['readiness', 'подтверждённая готовность'],
  ['appeals', 'обращения граждан'],
  ['list', 'перечень ГАСК'],
  ['other', 'другое'],
]

/** Названия статусов публикации в журнале — как пишет сервер */
export const STATUS_LOG: Record<AdStatus, string> = {
  new: 'Новая', needs_check: 'Нужна проверка', in_review: 'На проверке', confirmed: 'Нарушение подтверждено', dismissed: 'Отклонено',
}

/** Строка смены статуса — тот же формат, что у сервера (для журнала демо-сессии); без «..» после «Омарова Ж.».
 *  Тот же статус — «без изменений»: повторное решение с основанием или смена ответственного не выглядят сменой статуса */
export function statusLine(from: AdStatus, to: AdStatus, assignee?: string, comment?: string) {
  const st = from === to ? `Статус: «${STATUS_LOG[from]}» без изменений` : `Статус: «${STATUS_LOG[from]}» → «${STATUS_LOG[to]}»`
  const head = `${st}${assignee ? ` · ответственный: ${assignee}` : ''}`
  return comment ? `${head}${head.endsWith('.') ? '' : '.'} Основание: ${comment}` : head
}

/** Строка привязки публикации к ЖК аналитиком — как у сервера (PATCH /ads/{id}/complex); to нет — привязку сняли */
export function linkLine(from?: Pick<Complex, 'id' | 'name'>, to?: Pick<Complex, 'id' | 'name'>) {
  if (!to) return 'Привязка к ЖК снята'
  return from && from.id !== to.id ? `Привязка изменена: «${from.name}» → «${to.name}»` : `Привязка к ЖК «${to.name}» подтверждена аналитиком`
}

/** Действия людей для «Последних действий»: решения, привязки, решения по гипотезам ИИ, комментарии, запросы, сигналы, выгрузки.
 *  Гипотезы, которые ИИ записал сам от имени «Система», отсекает сервер (human), — иначе ручные добавления вытеснят людей из окна */
export const HUMAN_KINDS: AuditKind[] = ['status', 'link', 'ai', 'comment', 'request', 'signal', 'export']

/** Фильтр журнала: публикация, ЖК, сигнал, вид события (один или несколько), только действия людей (без «Системы»
 *  и без событий сбора и сверки данных — фильтр на сервере, до лимита), последние N */
export interface AuditScope { adId?: string; complexId?: string; signalId?: number; kind?: AuditKind | AuditKind[]; human?: boolean; limit?: number }

/** Путь журнала: у публикации, ЖК, сигнала — свой; с фильтрами — общий /audit (несколько видов — через запятую) */
export function auditPath({ adId, complexId, signalId, kind, human, limit }: AuditScope) {
  const kinds = kind === undefined ? [] : Array.isArray(kind) ? kind : [kind]
  const only = [adId, complexId, signalId].filter((x) => x !== undefined).length === 1 && !kinds.length && !human && limit === undefined
  const enc = encodeURIComponent
  if (only && adId !== undefined) return `/ads/${enc(adId)}/audit`
  if (only && complexId !== undefined) return `/complexes/${enc(complexId)}/audit`
  if (only && signalId !== undefined) return `/signals/${signalId}/audit`
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries({ adId, complexId, signalId, kind: kinds.join(','), human: human ? 'true' : undefined, limit })) if (v !== undefined && v !== '') q.set(k, String(v))
  const s = q.toString()
  return s ? `/audit?${s}` : '/audit'
}

/** «12 окт. 2026, 09:30» — время браузера; журнал хранит UTC */
export function fmtStamp(at: string) {
  const d = new Date(at)
  if (Number.isNaN(d.getTime())) return at
  return `${d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' }).replace(' г.', '')}, ${d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`
}

/** Инициалы автора: из справочника аналитиков, «S» — система, иначе первые буквы слов */
export function initialsOf(who: string, analysts: Pick<Analyst, 'name' | 'short'>[]) {
  if (who === SYSTEM) return 'S'
  const a = analysts.find((x) => x.name === who)
  if (a) return a.short
  return who.split(/\s+/).filter(Boolean).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || '?'
}
