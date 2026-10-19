import { describe, expect, it } from 'vitest'
import { HUMAN_KINDS, auditPath, linkLine, statusLine } from './audit'

const query = (path: string) => Object.fromEntries(new URLSearchParams(path.split('?')[1] ?? ''))

describe('statusLine — смена статуса в формате сервера', () => {
  it('смена статуса', () => {
    expect(statusLine('needs_check', 'in_review')).toBe('Статус: «Нужна проверка» → «На проверке»')
    expect(statusLine('in_review', 'confirmed', 'Омарова Ж.', 'сверено с ГАСК'))
      .toBe('Статус: «На проверке» → «Нарушение подтверждено» · ответственный: Омарова Ж. Основание: сверено с ГАСК')
  })

  it('тот же статус — «без изменений», а не «X» → «X»', () => {
    expect(statusLine('confirmed', 'confirmed', undefined, 'повторно сверено'))
      .toBe('Статус: «Нарушение подтверждено» без изменений. Основание: повторно сверено')
    expect(statusLine('dismissed', 'dismissed', 'Исаев Б.')).toBe('Статус: «Отклонено» без изменений · ответственный: Исаев Б.')
    expect(statusLine('dismissed', 'dismissed', 'Исаев Б.')).not.toContain('→')
  })
})

describe('linkLine — привязка к ЖК в формате сервера', () => {
  const A = { id: 'c1', name: 'Алтай' }, B = { id: 'c2', name: 'Нұрлы' }
  it('снята, изменена, подтверждена', () => {
    expect(linkLine(A, undefined)).toBe('Привязка к ЖК снята')
    expect(linkLine(undefined, undefined)).toBe('Привязка к ЖК снята')
    expect(linkLine(A, B)).toBe('Привязка изменена: «Алтай» → «Нұрлы»')
    expect(linkLine(A, A)).toBe('Привязка к ЖК «Алтай» подтверждена аналитиком')
    expect(linkLine(undefined, B)).toBe('Привязка к ЖК «Нұрлы» подтверждена аналитиком')
  })
})

describe('auditPath', () => {
  it('один объект без фильтров — его собственный журнал', () => {
    expect(auditPath({ adId: 'a6' })).toBe('/ads/a6/audit')
    expect(auditPath({ complexId: 'c 2' })).toBe('/complexes/c%202/audit')
    expect(auditPath({ signalId: 3 })).toBe('/signals/3/audit')
  })

  it('несколько видов — одним параметром через запятую, как принимает /api/audit', () => {
    const p = auditPath({ kind: HUMAN_KINDS, limit: 40 })
    expect(p.startsWith('/audit?')).toBe(true)
    expect(query(p)).toEqual({ kind: 'status,link,ai,comment,request,signal,export', limit: '40' })
  })

  it('один вид, фильтры по объекту и лимит — общий /audit', () => {
    expect(query(auditPath({ kind: 'comment' }))).toEqual({ kind: 'comment' })
    expect(query(auditPath({ adId: 'a6', limit: 5 }))).toEqual({ adId: 'a6', limit: '5' })
    expect(query(auditPath({ complexId: 'c2', kind: ['signal', 'export'] }))).toEqual({ complexId: 'c2', kind: 'signal,export' })
  })

  it('только действия людей — фильтр на сервере до лимита (ручные добавления с гипотезами ИИ не вытесняют людей)', () => {
    expect(query(auditPath({ kind: HUMAN_KINDS, human: true, limit: 8 })))
      .toEqual({ kind: 'status,link,ai,comment,request,signal,export', human: 'true', limit: '8' })
    expect(auditPath({ adId: 'a6', human: true })).toBe('/audit?adId=a6&human=true')
    expect(auditPath({ adId: 'a6', human: false })).toBe('/ads/a6/audit')
  })

  it('пустой список видов — без фильтра по виду', () => {
    expect(auditPath({ adId: 'a6', kind: [] })).toBe('/ads/a6/audit')
    expect(auditPath({ kind: [], limit: 8 })).toBe('/audit?limit=8')
    expect(auditPath({})).toBe('/audit')
  })

  it('действия людей — без системных событий и данных', () => {
    expect(HUMAN_KINDS).not.toContain('system')
    expect(HUMAN_KINDS).not.toContain('data')
  })
})
