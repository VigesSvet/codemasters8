import { describe, expect, it } from 'vitest'
import type { ScreeningDecision } from '../data/types'
import {
  DECISION_SOURCES, actText, byLine, cancelBody, cancelHint, commissionedBody, decisionButtons, decisionSummary, isActive, newCommissioned,
  newNoIssues, noIssuesBody, reopenedNotice, validateCancel, validateCommissioned, validateNoIssues,
} from './decision'
import { OTHER } from './verification'

const TODAY = '2026-10-19'
const dec = (x: Partial<ScreeningDecision> = {}): ScreeningDecision => ({
  id: 1, kind: 'commissioned', status: 'active', label: 'Дом сдан — замечаний нет', by: 'Омарова Ж.', at: '2026-10-19T04:30:00+00:00',
  actNumber: '12', actDate: '2026-09-01', source: 'ЕГКН', sourceUrl: null, reason: null, comment: null, reopenedBy: null, ...x,
})

describe('тексты решения', () => {
  it('акт: номер с «№» не удваивается, без номера — только дата (как на сервере)', () => {
    expect(actText('12', '2026-09-01')).toBe('акт № 12 от 01.09.2026')
    expect(actText('№ 12/2026', '2026-09-01')).toBe('акт № 12/2026 от 01.09.2026')
    expect(actText(null, '2026-09-01')).toBe('акт от 01.09.2026')
    expect(actText('  ', null)).toBe('акт')
  })

  it('сводка: «Сдан: акт …, источник … · подтвердил: имя, дата» и «Проверено: … · проверил: …»', () => {
    expect(decisionSummary(dec())).toBe('Сдан: акт № 12 от 01.09.2026, источник ЕГКН · подтвердил: Омарова Ж., 19.10.2026')
    expect(decisionSummary(dec({ kind: 'no_issues', reason: 'Сверено с ГАСК: продажи по разрешению МИО.', source: 'Ответ акимата / ГАСК', actNumber: null, actDate: null })))
      .toBe('Проверено: Сверено с ГАСК: продажи по разрешению МИО · источник Ответ акимата / ГАСК · проверил: Омарова Ж., 19.10.2026')
    // время записи — UTC: день — по Усть-Каменогорску (UTC+5)
    expect(byLine(dec({ at: '2026-10-19T20:00:00+00:00' }))).toBe('Омарова Ж., 20.10.2026')
    expect(byLine(dec({ at: null }))).toBe('Омарова Ж.')
  })

  it('снято новой рекламой: уведомление с датой и цитатой; действующее — без уведомления', () => {
    const r = dec({ status: 'reopened', reopenedBy: { adId: 'a9', date: '2026-10-18T14:05', signal: 'booking', quote: 'бронь квартиры 100 000 ₸' } })
    expect(reopenedNotice(r)).toBe('Решение снято автоматически: новая реклама 18.10.2026 — «бронь квартиры 100 000 ₸»')
    expect(reopenedNotice(dec({ status: 'reopened', reopenedBy: { adId: 'a9', date: null, signal: 'presale', quote: null } })))
      .toBe('Решение снято автоматически: новая реклама — старт продаж')
    expect(reopenedNotice(dec())).toBeNull()
    expect(reopenedNotice(null)).toBeNull()
    expect([isActive(dec()), isActive(r), isActive(null)]).toEqual([true, false, false])
  })

  it('кнопки: заметные у «Требует проверки», приглушённые у остальных; при действующем решении — нет, у снятого — снова есть', () => {
    expect(decisionButtons('check', null)).toBe('primary')
    expect(decisionButtons('suspect', null)).toBe('secondary')
    expect(decisionButtons('ok', null)).toBe('secondary')
    expect(decisionButtons('unknown', undefined)).toBe('secondary')
    expect(decisionButtons('ok', dec())).toBeNull()
    expect(decisionButtons('check', dec({ status: 'reopened' }))).toBe('primary')
  })

  it('отмена: что будет — у «сдан» вернётся прежняя стадия', () => {
    expect(cancelHint({ decision: dec() })).toContain('стадия карточки вернётся к прежней')
    expect(cancelHint({ decision: dec({ kind: 'no_issues' }) })).not.toContain('стадия')
    expect(cancelHint({ decision: null })).toBe('')
  })
})

describe('формы', () => {
  it('«Объект сдан»: дата акта и источник обязательны, ссылка — http(s), проверка по-русски', () => {
    expect(validateCommissioned(newCommissioned(), TODAY)).toEqual({
      date: 'Укажите дату акта ввода в эксплуатацию', source: 'Выберите, где подтверждён ввод в эксплуатацию' })
    const f = { ...newCommissioned(), date: '2026-10-20', choice: OTHER, url: 'ftp://x', act: '1'.repeat(129) }
    expect(validateCommissioned(f, TODAY)).toEqual({
      date: 'Дата не может быть позже сегодняшней', source: 'Напишите, где подтверждён ввод в эксплуатацию',
      url: 'Ссылка должна начинаться с http:// или https://', ref: 'Номер акта — не длиннее 128 символов' })
    expect(validateCommissioned({ ...f, date: '1999-01-01', other: 'x'.repeat(256), url: '', act: '' }, TODAY)).toEqual({
      date: 'Дата раньше 2000 года — проверьте год', source: 'Не длиннее 255 символов' })
    const ok = { act: ' 12 ', date: '2026-09-01', choice: '2ГИС — жилой дом', other: '', url: ' https://2gis.kz/x ', comment: ' ' }
    expect(validateCommissioned(ok, TODAY)).toEqual({})
    expect(commissionedBody(ok, 'Омарова Ж.')).toEqual({ author: 'Омарова Ж.', actNumber: '12', actDate: '2026-09-01', source: '2ГИС — жилой дом',
      sourceUrl: 'https://2gis.kz/x', comment: undefined })
    expect(commissionedBody({ ...ok, act: '', choice: OTHER, other: ' Письмо акимата № 5 ' }, 'А').actNumber).toBeUndefined()
    expect(commissionedBody({ ...ok, choice: OTHER, other: ' Письмо акимата № 5 ' }, 'А').source).toBe('Письмо акимата № 5')
    expect(DECISION_SOURCES.commissioned).toEqual(['ЕГКН', 'Акт приёмки в эксплуатацию', '2ГИС — жилой дом', 'Ответ акимата / ГАСК', OTHER])
  })

  it('«Проверено — замечаний нет»: что проверено — не короче 10 символов, источник обязателен', () => {
    expect(validateNoIssues(newNoIssues())).toEqual({ subject: 'Опишите, что проверено', source: 'Выберите, где проверено' })
    expect(validateNoIssues({ ...newNoIssues(), reason: ' проверено ', choice: 'ЕГКН' }).subject).toBe('Опишите, что проверено, — не короче 10 символов')
    expect(validateNoIssues({ ...newNoIssues(), reason: 'ок, всё ок', choice: 'ЕГКН' })).toEqual({})
    expect(validateNoIssues({ ...newNoIssues(), reason: 'коротко', choice: 'ЕГКН', comment: 'x'.repeat(2001) })).toEqual({
      subject: 'Опишите, что проверено, — не короче 10 символов', comment: 'Комментарий не длиннее 2000 символов' })
    const f = { reason: ' Сверено с ГАСК — продажи по разрешению ', choice: 'Ответ акимата / ГАСК', other: '', url: '', comment: 'вх. 15' }
    expect(noIssuesBody(f, 'Омарова Ж.')).toEqual({ author: 'Омарова Ж.', decision: 'no_issues', reason: 'Сверено с ГАСК — продажи по разрешению',
      source: 'Ответ акимата / ГАСК', sourceUrl: undefined, comment: 'вх. 15' })
  })

  it('отмена: причина обязательна, не короче 5 символов', () => {
    expect(validateCancel({ reason: ' ' })).toEqual({ comment: 'Укажите причину отмены' })
    expect(validateCancel({ reason: 'нет' })).toEqual({ comment: 'Причина отмены — не короче 5 символов' })
    expect(validateCancel({ reason: 'Акт другой очереди' })).toEqual({})
    expect(cancelBody({ reason: ' Акт другой очереди ' }, 'Омарова Ж.')).toEqual({ author: 'Омарова Ж.', reason: 'Акт другой очереди' })
  })
})
