// Решение аналитика по «Проверке по рекламе» (сервер: app.decisions): «Объект сдан» (ввод в эксплуатацию подтверждён — акт,
// дата акта, источник) и «Проверено — замечаний нет»; отмена — оценка снова автоматическая. Новая реклама после решения с
// признаком приёма денег снимает его автоматически (у «сдан» — только старт продаж или строящаяся очередь). Чистые помощники:
// тексты сводки и уведомления о снятии, акцент кнопок, проверка форм по-русски и тела запросов. Отправку делает lib/store.tsx.
import type { Complex, ScreeningDecision, ScreeningStatus } from '../data/types'
import { signalName } from './screening'
import { OTHER, dateProblem, dmy, sourceOf, todayLocal, type FormErrors } from './verification'

/** Подписи кнопок и решений — как у сервера */
export const DECISION_TITLE = { commissioned: 'Объект сдан', no_issues: 'Проверено — замечаний нет' } as const
export const CANCEL_TITLE = 'Отменить решение'

const HTTP = /^https?:\/\/\S+$/i
const opt = (s: string) => (s.trim() ? s.trim() : undefined)

/** «акт № 12 от 01.09.2026»; номер с «№» не удваивается; без номера — «акт от 01.09.2026» (как screening.act_text) */
export function actText(actNumber?: string | null, actDate?: string | null): string {
  const n = (actNumber ?? '').trim()
  const num = n ? (n.includes('№') ? n : `№ ${n}`) : ''
  return ['акт', num, actDate ? `от ${dmy(actDate)}` : ''].filter(Boolean).join(' ')
}

/** Кто и когда записал: «Омарова Ж., 19.10.2026» */
export const byLine = (d: Pick<ScreeningDecision, 'by' | 'at'>) => `${d.by}${d.at ? `, ${dmy(d.at)}` : ''}`

/** Сводка решения: «Сдан: акт № 12 от 01.09.2026, источник ЕГКН · подтвердил: Омарова Ж., 19.10.2026» /
 *  «Проверено: … · источник … · проверил: …» */
export function decisionSummary(d: ScreeningDecision): string {
  if (d.kind === 'commissioned') return `Сдан: ${actText(d.actNumber, d.actDate)}, источник ${d.source} · подтвердил: ${byLine(d)}`
  const why = (d.reason ?? '').trim().replace(/\.$/, '')
  return `Проверено: ${why} · источник ${d.source} · проверил: ${byLine(d)}`
}

/** Решение снято новой рекламой: «Решение снято автоматически: новая реклама 18.10.2026 — «цитата»»; действует — null */
export function reopenedNotice(d?: ScreeningDecision | null): string | null {
  if (!d || d.status !== 'reopened' || !d.reopenedBy) return null
  const t = d.reopenedBy
  const when = t.date ? ` ${dmy(t.date.slice(0, 10))}` : ''
  const what = t.quote ? ` — «${t.quote}»` : t.signal ? ` — ${signalName(t.signal)}` : ''
  return `Решение снято автоматически: новая реклама${when}${what}`
}

/** Действующее решение (не снятое рекламой) */
export const isActive = (d?: ScreeningDecision | null): d is ScreeningDecision => !!d && d.status === 'active'

/** Кнопки «Объект сдан» и «Проверено — замечаний нет»: заметные у «Требует проверки», приглушённые у остальных; при
 *  действующем решении их нет (сначала «Отменить решение»), у снятого рекламой — снова есть */
export function decisionButtons(status: ScreeningStatus | null | undefined, d?: ScreeningDecision | null): 'primary' | 'secondary' | null {
  if (isActive(d)) return null
  return status === 'check' ? 'primary' : 'secondary'
}

// ---------- формы ----------

/** Где подтверждён ввод в эксплуатацию и где проверено; «Другое» — свой текст */
export const DECISION_SOURCES = {
  commissioned: ['ЕГКН', 'Акт приёмки в эксплуатацию', '2ГИС — жилой дом', 'Ответ акимата / ГАСК', OTHER],
  no_issues: ['Ответ акимата / ГАСК', 'ЕГКН', 'Homeportal', 'Казреестр (Ashyq)', 'Выезд на площадку', OTHER],
} as const

export interface CommissionedForm { act: string; date: string; choice: string; other: string; url: string; comment: string }
export interface NoIssuesForm { reason: string; choice: string; other: string; url: string; comment: string }
export interface CancelForm { reason: string }

export const newCommissioned = (): CommissionedForm => ({ act: '', date: '', choice: '', other: '', url: '', comment: '' })
export const newNoIssues = (): NoIssuesForm => ({ reason: '', choice: '', other: '', url: '', comment: '' })

function sourceUrlComment(f: { choice: string; other: string; url: string; comment: string }, e: FormErrors, what: string) {
  if (!f.choice) e.source = `Выберите, ${what}`
  else if (f.choice === OTHER && !f.other.trim()) e.source = `Напишите, ${what}`
  else if (sourceOf(f).length > 255) e.source = 'Не длиннее 255 символов'
  const u = f.url.trim()
  if (u && !HTTP.test(u)) e.url = 'Ссылка должна начинаться с http:// или https://'
  else if (u.length > 512) e.url = 'Ссылка не длиннее 512 символов'
  if (f.comment.trim().length > 2000) e.comment = 'Комментарий не длиннее 2000 символов'
}

/** «Объект сдан»: дата акта обязательна (не позже сегодня), источник обязателен, номер акта — до 128 символов */
export function validateCommissioned(f: CommissionedForm, today = todayLocal()): FormErrors {
  const e: FormErrors = {}
  const d = dateProblem(f.date, today)
  if (d) e.date = d === 'Укажите дату' ? 'Укажите дату акта ввода в эксплуатацию' : d
  if (f.act.trim().length > 128) e.ref = 'Номер акта — не длиннее 128 символов'
  sourceUrlComment(f, e, 'где подтверждён ввод в эксплуатацию')
  return e
}

/** «Проверено — замечаний нет»: что проверено — не короче 10 символов, источник обязателен */
export function validateNoIssues(f: NoIssuesForm): FormErrors {
  const e: FormErrors = {}
  const r = f.reason.trim()
  if (!r) e.subject = 'Опишите, что проверено'
  else if (r.length < 10) e.subject = 'Опишите, что проверено, — не короче 10 символов'
  else if (r.length > 2000) e.subject = 'Не длиннее 2000 символов'
  sourceUrlComment(f, e, 'где проверено')
  return e
}

/** Отмена решения: причина обязательна (не короче 5 символов) */
export function validateCancel(f: CancelForm): FormErrors {
  const r = f.reason.trim()
  if (!r) return { comment: 'Укажите причину отмены' }
  if (r.length < 5) return { comment: 'Причина отмены — не короче 5 символов' }
  if (r.length > 2000) return { comment: 'Не длиннее 2000 символов' }
  return {}
}

/** Тело POST /api/complexes/{id}/commissioned */
export const commissionedBody = (f: CommissionedForm, author: string) =>
  ({ author, actNumber: opt(f.act), actDate: f.date.trim(), source: sourceOf(f), sourceUrl: opt(f.url), comment: opt(f.comment) })

/** Тело POST /api/complexes/{id}/screening-decision */
export const noIssuesBody = (f: NoIssuesForm, author: string) =>
  ({ author, decision: 'no_issues' as const, reason: f.reason.trim(), source: sourceOf(f), sourceUrl: opt(f.url), comment: opt(f.comment) })

/** Тело DELETE /api/complexes/{id}/screening-decision */
export const cancelBody = (f: CancelForm, author: string) => ({ author, reason: f.reason.trim() })

/** Что будет при отмене: оценка снова автоматическая; у «сдан» — прежняя стадия карточки */
export function cancelHint(c: Pick<Complex, 'decision'>): string {
  const d = c.decision
  if (!d) return ''
  return d.kind === 'commissioned'
    ? 'Оценка снова станет автоматической, стадия карточки вернётся к прежней. Решение останется в журнале и истории.'
    : 'Оценка снова станет автоматической. Решение останется в журнале и истории.'
}

// ---------- окна (components/Decision.tsx) ----------

type Busy = { busy?: boolean; err?: string; errors?: FormErrors }
export type DecisionDlg = Busy & (
  | { kind: 'commissioned'; form: CommissionedForm }
  | { kind: 'no_issues'; form: NoIssuesForm }
  | { kind: 'cancel'; form: CancelForm }
)
