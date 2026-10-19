// Маршрут рассмотрения сигнала (T-79): этапы, сроки, кто согласует. Сроки (SLA, рабочие дни) считает сервер —
// backend/app/route.py, константы там же; здесь — типы, запросы и подписи «осталось / просрочено» к сроку.
import { apiGet, apiPost } from './api'
import type { User } from './session'

export type RouteStageCode = 'analysis' | 'approval' | 'confirmed' | 'returned'
export type RouteAction = 'submit' | 'approve' | 'return'

/** Кратко по сигналу — GET /api/signals/routes (колонка «Срок», панель статистики) */
export interface RouteSummary {
  signalId: number
  code: string
  status: 'new' | 'confirmed' | 'returned'
  stage: RouteStageCode
  stageLabel: string
  since: string
  dueAt: string | null
  slaDays: number | null
  overdue: boolean
}

export interface RouteStage {
  stage: RouteStageCode
  label: string
  startedAt: string
  startedBy: string
  comment: string | null
  slaDays: number | null
  dueAt: string | null
  endedAt: string | null
  endedBy: string | null
  outcome: 'submitted' | 'returned_to_analyst' | 'confirmed' | 'returned' | null
  outcomeComment: string | null
  overdue: boolean
}

/** Полный маршрут — GET /api/signals/{id}/route */
export interface Route extends RouteSummary {
  now: string
  actions: RouteAction[]
  approver: string
  sla: Record<'analysis' | 'approval', number>
  stages: RouteStage[]
}

/** В пилоте согласует роль «Ведущий аналитик» (an2) — так же проверяет сервер (403 остальным) */
export const APPROVER_ROLE = 'Ведущий аналитик'
export const isApprover = (u?: Pick<User, 'role'> | null) => !!u && u.role.startsWith(APPROVER_ROLE)

export const getRoutes = () => apiGet<RouteSummary[]>('/signals/routes')
export const getRoute = (id: number) => apiGet<Route>(`/signals/${id}/route`)
export const routeAction = (id: number, action: RouteAction, author: string, comment?: string) =>
  apiPost<Route>(`/signals/${id}/route`, { action, author, comment: comment?.trim() || null })

const HOUR = 3_600_000

export type DueTone = 'ok' | 'soon' | 'overdue'
/** Подпись к сроку: «осталось 5 ч», «осталось 2 дн.», «просрочено на 3 ч», «просрочено на 2 дн.».
 *  Меньше суток — в часах (вверх, не меньше 1 ч), от суток — в полных календарных днях (вниз: 50 ч — «2 дн.»);
 *  меньше суток до срока — soon */
export function dueInfo(dueAt: string | null | undefined, now: Date | number = Date.now()): { tone: DueTone; text: string; hours: number } | null {
  if (!dueAt) return null
  const due = new Date(dueAt).getTime()
  if (Number.isNaN(due)) return null
  const t = typeof now === 'number' ? now : now.getTime()
  const diff = due - t
  const span = (ms: number) => {
    const h = Math.max(1, Math.ceil(ms / HOUR))
    return h < 24 ? `${h} ч` : `${Math.floor(h / 24)} дн.`
  }
  if (diff < 0) return { tone: 'overdue', text: `просрочено на ${span(-diff)}`, hours: diff / HOUR }
  return { tone: diff < 24 * HOUR ? 'soon' : 'ok', text: `осталось ${span(diff)}`, hours: diff / HOUR }
}

/** Счётчики для панели статистики /signals: открытые по этапам и просроченные */
export function routeCounts(rows: RouteSummary[]): { analysis: number; approval: number; overdue: number } {
  const open = rows.filter((r) => r.status === 'new')
  return {
    analysis: open.filter((r) => r.stage === 'analysis').length,
    approval: open.filter((r) => r.stage === 'approval').length,
    overdue: open.filter((r) => r.overdue).length,
  }
}

export const OUTCOME: Record<NonNullable<RouteStage['outcome']>, string> = {
  submitted: 'передан на согласование',
  returned_to_analyst: 'возвращён аналитику',
  confirmed: 'сигнал подтверждён',
  returned: 'возвращён на доработку',
}
