// Предупреждения жителям (сервер: app.alerts): текст по данным ЖК → согласование другим сотрудником в режиме
// администратора → отправка воркером в канал ГАСК и подписчикам района в Telegram-боте заявок. Здесь — типы ответа,
// чистые помощники (как увидит житель, хэштег района, кому уйдёт, сводка доставки, кто может согласовать) и запросы.
// Токен администратора уходит только в adminCall (согласовать, отклонить, повторить, проверить бота).
import { adminCall } from './admin'
import { apiGet, apiPost, orNoRoute, orNull } from './api'

export type AlertStatus = 'pending' | 'rejected' | 'sending' | 'sent' | 'partial' | 'failed'
export type AlertTemplate = 'basis' | 'idle' | 'ok'
export type DeliveryStatus = 'pending' | 'sent' | 'failed' | 'skipped'

export interface DeliveryCounts { total: number; sent: number; failed: number; pending: number; skipped: number }
export interface ChannelDelivery { target: string; status: DeliveryStatus; error: string | null; sentAt: string | null; link: string | null }

/** Предупреждение (GET /api/alerts): получатели — только счётчики, chat_id сервер не отдаёт */
export interface ResidentAlert {
  id: number
  code: string
  complexId: string
  complexName: string
  district: string
  template: AlertTemplate
  title: string
  text: string
  status: AlertStatus
  statusLabel: string
  createdBy: string
  createdAt: string | null
  decidedBy: string | null
  decidedAt: string | null
  decisionComment: string | null
  sentAt: string | null
  lastError: string | null
  channel: ChannelDelivery | null
  subscribers: DeliveryCounts
  errors: { text: string; count: number }[]
}

/** Кому уйдёт по району и готова ли отправка (GET /api/alerts/audience) */
export interface Audience {
  configured: boolean
  adminConfigured: boolean
  workerAlive: boolean
  problem: string | null
  channel: string | null
  channelProblem: string | null
  district: string
  subscribers: number
  everywhere: number
  districts: { district: string; subscribers: number }[]
}

export interface BotCheck { ok: boolean; detail: string; bot: string | null; channel: string | null; channelTitle: string | null }

/** Пределы — как у сервера (AlertIn) */
export const TITLE_MAX = 80
export const TEXT_MAX = 240
export const SIGNATURE = 'ГАСК ВКО · SAQ QURYLYS'

export const STATUS_TONE: Record<AlertStatus, string> = {
  pending: 'yellow', rejected: 'gray', sending: 'blue', sent: 'green', partial: 'yellow', failed: 'red',
}

/** «Северо-запад» → «#Северо_запад»: хэштег района — как alerts.hashtag на сервере; без района — пусто */
export function hashtag(district: string | null | undefined): string {
  const tag = (district ?? '').replace(/[^\p{L}\p{N}_]+/gu, '_').replace(/^_+|_+$/g, '')
  return tag ? `#${tag}` : ''
}

/** Строка под текстом сообщения: хэштег района и подпись — как в сообщении Telegram (alerts.render) */
export const footer = (district: string | null | undefined) => [hashtag(district), SIGNATURE].filter(Boolean).join(' · ')

const n = (x: number, [one, few, many]: [string, string, string]) => {
  const a = Math.abs(x) % 100
  const b = a % 10
  return `${x} ${a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many}`
}
const SUBS: [string, string, string] = ['подписчик', 'подписчика', 'подписчиков']

/** Кому уйдёт: «канал @gask_vko и 3 подписчика района «Ульбинский» в боте»; некому — null */
export function audienceLine(a: Pick<Audience, 'channel' | 'subscribers' | 'district'>): string | null {
  const where = a.district ? `района «${a.district}»` : 'всех районов'
  const parts = [a.channel ? `канал ${a.channel}` : '', a.subscribers ? `${n(a.subscribers, SUBS)} ${where} в боте` : '']
  const s = parts.filter(Boolean).join(' и ')
  return s || null
}

/** Что мешает отправить на согласование и потом отправить (BOT_TOKEN, канал, воркер, некому) — для подсказки в форме */
export function audienceProblems(a: Audience | null): string[] {
  if (!a) return []
  const out: string[] = []
  if (a.problem) out.push(a.problem)
  if (a.channelProblem) out.push(a.channelProblem)
  if (!audienceLine(a)) out.push(`Некому отправить: канал ГАСК не задан, подписчиков ${a.district ? `района «${a.district}»` : 'всех районов'} в боте нет`)
  if (!a.adminConfigured) out.push('Согласовать некому: режим администратора не настроен на сервере (SAQ_ADMIN_TOKEN)')
  if (a.configured && !a.workerAlive) out.push('Воркер не запущен — согласованное предупреждение уйдёт, когда он запустится')
  return out
}

/** Сводка доставки: «канал @x — опубликовано · подписчики: доставлено 12 из 13, не доставлено 1» */
export function deliveryLine(a: Pick<ResidentAlert, 'channel' | 'subscribers'>): string {
  const parts: string[] = []
  const ch = a.channel
  if (ch) parts.push(`канал ${ch.target} — ${{ sent: 'опубликовано', failed: 'не опубликовано', skipped: 'пропущено', pending: 'ожидает' }[ch.status]}`)
  const c = a.subscribers
  if (c.total) {
    const of = c.total - c.skipped
    let s = `подписчики: доставлено ${c.sent} из ${of}`
    if (c.failed) s += `, не доставлено ${c.failed}`
    if (c.pending) s += `, ожидают ${c.pending}`
    if (c.skipped) s += `, отписались ${c.skipped}`
    parts.push(s)
  }
  return parts.join(' · ')
}

const same = (x: string, y: string) => x.trim().toLocaleLowerCase('ru') === y.trim().toLocaleLowerCase('ru')

/** Почему этот сотрудник не может согласовать (null — может): автор не согласует сам, нужен режим администратора */
export function approveProblem(a: Pick<ResidentAlert, 'status' | 'createdBy'>, who: string | null | undefined, admin: boolean): string | null {
  if (a.status !== 'pending') return 'Предупреждение уже не на согласовании'
  if (!who) return 'Войдите, чтобы согласовать'
  if (same(who, a.createdBy)) return 'Согласует другой сотрудник — автор не может согласовать своё предупреждение'
  if (!admin) return 'Согласование — в режиме администратора'
  return null
}

/** «Повторить отправку»: после ошибки остались доставки «ожидает» (неверный BOT_TOKEN, исчерпанные повторы) */
export const canRetry = (a: Pick<ResidentAlert, 'status' | 'channel' | 'subscribers'>) =>
  (a.status === 'failed' || a.status === 'partial') && (a.subscribers.pending > 0 || a.channel?.status === 'pending')

// ---------- запросы ----------

const isAlerts = (x: unknown) => Array.isArray(x)
const isAudience = (x: unknown) => !!x && typeof x === 'object' && 'configured' in x

/** Предупреждения (новые первыми); null — нет связи или сервер без этой функции */
export const getAlerts = (complexId?: string) =>
  orNull(apiGet<ResidentAlert[]>(`/alerts${complexId ? `?complexId=${encodeURIComponent(complexId)}` : ''}`), isAlerts)

export const getAudience = (district: string) =>
  orNull(apiGet<Audience>(`/alerts/audience?district=${encodeURIComponent(district)}`), isAudience)

export interface AlertDraft { complexId: string; template: AlertTemplate; title: string; text: string; author: string }
export const createAlert = (body: AlertDraft) => orNoRoute(apiPost<ResidentAlert>('/alerts', body))

const decide = (id: number, what: 'approve' | 'reject' | 'retry', author: string, comment?: string) =>
  adminCall((h) => apiPost<ResidentAlert>(`/alerts/${id}/${what}`, { author, comment: comment?.trim() || undefined }, h))

export const approveAlert = (id: number, author: string, comment?: string) => decide(id, 'approve', author, comment)
export const rejectAlert = (id: number, author: string, comment?: string) => decide(id, 'reject', author, comment)
export const retryAlert = (id: number, author: string) => decide(id, 'retry', author)
export const checkBot = () => adminCall((h) => apiPost<BotCheck>('/alerts/check', {}, h))
