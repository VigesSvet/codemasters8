import { describe, expect, it } from 'vitest'
import { ADDRESSEES } from './letter'
import { dueInfo, isApprover, routeCounts, type RouteSummary } from './route'

const NOW = Date.parse('2026-10-20T10:00:00+05:00')
const H = 3_600_000

describe('dueInfo — подпись к сроку этапа', () => {
  it('нет срока или битая дата — null', () => {
    expect(dueInfo(null, NOW)).toBeNull()
    expect(dueInfo(undefined, NOW)).toBeNull()
    expect(dueInfo('не дата', NOW)).toBeNull()
  })

  it('от суток — в полных днях, тон ok', () => {
    expect(dueInfo(new Date(NOW + 30 * H).toISOString(), NOW)).toMatchObject({ tone: 'ok', text: 'осталось 1 дн.' })
    expect(dueInfo(new Date(NOW + 24 * H).toISOString(), NOW)).toMatchObject({ tone: 'ok', text: 'осталось 1 дн.' })
    expect(dueInfo(new Date(NOW + 48 * H).toISOString(), NOW)).toMatchObject({ tone: 'ok', text: 'осталось 2 дн.' })
  })

  it('меньше суток — в часах вверх, тон soon; меньше часа — «1 ч»', () => {
    expect(dueInfo(new Date(NOW + 5.2 * H).toISOString(), NOW)).toMatchObject({ tone: 'soon', text: 'осталось 6 ч' })
    expect(dueInfo(new Date(NOW + 60_000).toISOString(), NOW)).toMatchObject({ tone: 'soon', text: 'осталось 1 ч' })
    expect(dueInfo(new Date(NOW).toISOString(), NOW)).toMatchObject({ tone: 'soon', text: 'осталось 1 ч' })
  })

  it('срок прошёл — просрочено на часы или дни, hours < 0', () => {
    const late = dueInfo(new Date(NOW - 3 * H).toISOString(), NOW)!
    expect(late).toMatchObject({ tone: 'overdue', text: 'просрочено на 3 ч' })
    expect(late.hours).toBeCloseTo(-3)
    expect(dueInfo(new Date(NOW - 50 * H).toISOString(), new Date(NOW))).toMatchObject({ tone: 'overdue', text: 'просрочено на 2 дн.' })
  })

  it('срок с UTC-смещением сервера сравнивается как момент времени', () => {
    // 23:59:59 по Алматы = 18:59:59Z
    expect(dueInfo('2026-10-20T18:59:59+00:00', Date.parse('2026-10-20T23:00:00+05:00'))).toMatchObject({ tone: 'soon', text: 'осталось 1 ч' })
    expect(dueInfo('2026-10-20T18:59:59+00:00', Date.parse('2026-10-21T00:30:00+05:00'))!.tone).toBe('overdue')
  })
})

describe('маршрут: роли и счётчики', () => {
  it('согласует только «Ведущий аналитик» (пилот, an2)', () => {
    expect(isApprover({ role: 'Ведущий аналитик · ДЭР ВКО' })).toBe(true)
    expect(isApprover({ role: 'Аналитик · ДЭР ВКО' })).toBe(false)
    expect(isApprover({ role: 'Специалист · ГАСК ВКО' })).toBe(false)
    expect(isApprover(null)).toBe(false)
  })

  it('счётчики панели: только открытые сигналы, просроченные — отдельно', () => {
    const r = (id: number, status: RouteSummary['status'], stage: RouteSummary['stage'], overdue = false): RouteSummary =>
      ({ signalId: id, code: `S-${id}`, status, stage, stageLabel: '', since: '', dueAt: null, slaDays: null, overdue })
    expect(routeCounts([r(1, 'new', 'analysis', true), r(2, 'new', 'analysis'), r(3, 'new', 'approval', true), r(4, 'confirmed', 'confirmed', true)]))
      .toEqual({ analysis: 2, approval: 1, overdue: 2 })
    expect(routeCounts([])).toEqual({ analysis: 0, approval: 0, overdue: 0 })
  })

  it('адресаты запроса — ЖАО (МИО), Единый оператор, ГАСК', () => {
    expect(ADDRESSEES.map(([a]) => a)).toEqual(['mio', 'operator', 'gask'])
  })
})
