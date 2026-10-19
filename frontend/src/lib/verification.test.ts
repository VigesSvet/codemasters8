// Сверка сведений аналитиком: что сверено, подсказка «Сверено N из 4», подтверждение в один клик, формы и их проверка.
import { describe, expect, it } from 'vitest'
import type { Appeal, Complex } from '../data/types'
import {
  OTHER, appealBody, appealLines, checkBody, checkedLine, confirmBody, confirmLines, dateProblem, dmy, docChecked, enteredLine,
  initialForm, isChecked, listChecked, newAppeal, newAppealsCheck, newPhoto, newReadiness, photoFields, progress, progressText,
  readinessBody, sourceChoice, sourceOf, suggestionFor, todayLocal, validateAppeal, validateAppealsCheck, validateCheck,
  validatePhoto, validateReadiness, type CheckForm,
} from './verification'

const TODAY = '2026-10-19'
const SG = { ref: '№ KZ40VWE00002526 от 15.07.2025', source: 'Homeportal', sourceName: 'Homeportal — карточки ЖК', url: 'https://homeportal.kz/ru/projects/1', records: 1 }

/** Карточка из открытого реестра: ничего не сверено, реестр называет разрешение МИО */
const reg = (patch: Partial<Complex> = {}): Complex => ({
  id: 'r-x', name: 'X', developerId: 'rd-1', address: '', district: '', lng: null, lat: null, stage: '', progress: 0, floors: 0,
  flats: 0, start: '', plannedEnd: '',
  basis: { guarantee: 'unknown', mio: 'unknown', bvu: 'unknown', mioRef: 'По данным открытого реестра (Homeportal): разрешение МИО № KZ40VWE00002526 от 15.07.2025. Аналитиком не проверено' },
  readiness: { declared: null, confirmed: null }, inPublishedList: 'unknown', cover: '', photos: [], idleDays: 0, origin: 'registry',
  basisSuggest: { mio: SG }, ...patch,
})

const form = (patch: Partial<CheckForm> = {}): CheckForm =>
  ({ value: 'yes', ref: '', choice: 'Homeportal', other: '', url: '', date: TODAY, comment: '', ...patch })

describe('что сверено', () => {
  it('документ — «есть»/«нет» с источником и датой (своими или общими), «нет сведений» — нет', () => {
    const c = reg()
    expect(docChecked(c, 'mio')).toBe(false)
    const own = reg({ basis: { ...c.basis, mio: 'yes', sources: { mio: { source: 'Homeportal', checkedAt: TODAY, checkedBy: 'Омарова Ж.' } } } })
    expect(docChecked(own, 'mio')).toBe(true)
    // общая сверка — для «есть/нет» без своей записи (демо-набор), но не для «нет сведений»
    const general = reg({ basis: { guarantee: 'no', mio: 'unknown', bvu: 'unknown', source: 'Картотека ГАСК', checkedAt: '2026-10-09' } })
    expect([docChecked(general, 'guarantee'), docChecked(general, 'mio')]).toEqual([true, false])
    expect(docChecked(reg({ basis: { guarantee: 'no', mio: 'unknown', bvu: 'unknown' } }), 'guarantee')).toBe(false)
  })
  it('перечень ГАСК: сверка аналитика; у демо-ЖК — демо-перечень', () => {
    expect(listChecked(reg({ inPublishedList: 'yes' }))).toBe(false)
    expect(listChecked(reg({ inPublishedList: 'no', publishedListCheck: { value: 'no', source: 'Instagram ГАСК', checkedAt: TODAY, checkedBy: 'А' } }))).toBe(true)
    expect(listChecked(reg({ inPublishedList: 'yes', origin: 'demo' }))).toBe(true)
    expect(listChecked(reg({ inPublishedList: 'unknown', origin: 'demo' }))).toBe(false)
  })
  it('подсказка «Сверено N из 4» и первое несверенное; всё сверено — подсказки нет', () => {
    const c = reg()
    expect(progress(c)).toEqual({ checked: 0, total: 4, first: 'guarantee' })
    expect(progressText(progress(c), false)).toBe('Сверено 0 из 4 оснований — сверьте остальные, чтобы рассчитать риск')
    const one = reg({ basis: { ...c.basis, guarantee: 'no', sources: { guarantee: { source: 'ЕГКН', checkedAt: TODAY } } } })
    expect(progress(one)).toEqual({ checked: 1, total: 4, first: 'mio' })
    expect(progressText(progress(one), true)).toBe('Сверено 1 из 4 оснований — сверьте остальные, чтобы уточнить риск')
    const all = reg({
      basis: { guarantee: 'yes', mio: 'no', bvu: 'no', source: 'S', checkedAt: TODAY },
      inPublishedList: 'yes', publishedListCheck: { value: 'yes', source: 'S', checkedAt: TODAY, checkedBy: 'А' },
    })
    expect(progress(all).first).toBeNull()
    expect(progressText(progress(all), true)).toBeNull()
    expect(isChecked(all, 'list')).toBe(true)
  })
  it('подсказка реестра — только у несверенного документа', () => {
    expect(suggestionFor(reg(), 'mio')).toEqual(SG)
    expect(suggestionFor(reg(), 'guarantee')).toBeNull()
    expect(suggestionFor(reg(), 'list')).toBeNull()
    const done = reg({ basis: { ...reg().basis, mio: 'yes', sources: { mio: { source: 'Homeportal', checkedAt: TODAY } } } })
    expect(suggestionFor(done, 'mio')).toBeNull()
  })
  it('кто и когда сверил, внёс, решил', () => {
    expect(checkedLine({ checkedBy: 'Омарова Ж.', checkedAt: TODAY, source: 'Homeportal' })).toBe('Сверено: Омарова Ж., 19.10.2026 · Homeportal')
    expect(checkedLine({ source: 'Картотека ГАСК', checkedAt: TODAY })).toBeNull() // демо: автора нет
    expect(enteredLine({ value: 31, source: 'Акт', date: TODAY, by: 'Омарова Ж.', at: '2026-10-19T05:00:00+00:00' })).toBe('Внёс: Омарова Ж., 19.10.2026')
    expect(enteredLine({ value: 31, source: 'Акт', date: TODAY })).toBeNull()
    const a: Appeal = { id: 'ap1', complexId: 'r-x', date: TODAY, collective: true, applicants: 3, status: 'verified', verifiedAt: TODAY,
      subject: 'Тема', source: 'ДЭР', createdBy: 'Омарова Ж.', createdAt: '2026-10-18T05:00:00+00:00', decidedBy: 'Нестерова А.', decisionComment: 'сверено' }
    expect(appealLines(a)).toEqual(['Внёс: Омарова Ж., 18.10.2026', 'Подтвердил: Нестерова А., 19.10.2026 — сверено'])
    // время с поясом — день по Усть-Каменогорску (UTC+5): 21:40 UTC 18-го — уже 19-е
    expect(appealLines({ ...a, createdAt: '2026-10-18T21:40:00+00:00', decidedBy: null })).toEqual(['Внёс: Омарова Ж., 19.10.2026'])
    expect(appealLines({ ...a, status: 'pending', createdBy: null })).toEqual([])
  })
})

describe('подтверждение в один клик', () => {
  it('«есть» с номером, источником и ссылкой реестра, сверено сегодня', () => {
    expect(confirmBody('mio', SG, 'Омарова Ж.', TODAY)).toEqual({
      author: 'Омарова Ж.', doc: 'mio', value: 'yes', ref: SG.ref, source: 'Homeportal', sourceUrl: SG.url, checkedAt: TODAY,
    })
    expect(confirmLines('mio', SG, 'Омарова Ж.', TODAY)).toEqual([
      ['Документ', 'Разрешение МИО — есть'], ['Номер и дата', SG.ref], ['Источник', 'Homeportal'], ['Ссылка', SG.url],
      ['Дата сверки', '19.10.2026'], ['Сверил', 'Омарова Ж.'],
    ])
    expect(confirmLines('mio', { ...SG, url: null }, 'А', TODAY).map(([k]) => k)).not.toContain('Ссылка')
  })
})

describe('форма «Сверить»', () => {
  it('несверенный документ: номер, источник и ссылка — из реестра, результат аналитик отмечает сам', () => {
    expect(initialForm(reg(), 'mio', TODAY)).toEqual({ value: '', ref: SG.ref, choice: 'Homeportal', other: '', url: SG.url, date: TODAY, comment: '' })
    expect(initialForm(reg(), 'guarantee', TODAY)).toEqual({ value: '', ref: '', choice: '', other: '', url: '', date: TODAY, comment: '' })
  })
  it('сверенный документ: как сверено; свой источник — «Другое» с текстом', () => {
    const c = reg({ basis: { ...reg().basis, mio: 'no', mioRef: '№ 5', sources: { mio: { source: 'Письмо МИО № 7', url: 'https://x.kz/1', checkedAt: '2026-10-01', checkedBy: 'А', ref: '№ 5' } } } })
    expect(initialForm(c, 'mio', TODAY)).toEqual({ value: 'no', ref: '№ 5', choice: OTHER, other: 'Письмо МИО № 7', url: 'https://x.kz/1', date: TODAY, comment: '' })
    const list = reg({ inPublishedList: 'yes', publishedListCheck: { value: 'yes', source: 'Перечень ГАСК (Instagram)', checkedAt: TODAY, checkedBy: 'А' } })
    expect(initialForm(list, 'list', TODAY)).toMatchObject({ value: 'yes', ref: '', choice: 'Перечень ГАСК (Instagram)' })
  })
  it('источник: вариант списка без учёта регистра или «Другое»', () => {
    expect(sourceChoice('homeportal', 'basis')).toEqual({ choice: 'Homeportal', other: '' })
    expect(sourceChoice('Ответ ДЭР', 'basis')).toEqual({ choice: OTHER, other: 'Ответ ДЭР' })
    expect(sourceChoice('', 'basis')).toEqual({ choice: '', other: '' })
    expect(sourceOf({ choice: OTHER, other: '  Ответ ДЭР ' })).toBe('Ответ ДЭР')
  })
  it('проверка по-русски', () => {
    expect(validateCheck(form(), TODAY)).toEqual({})
    expect(validateCheck(form({ value: '' }), TODAY)).toEqual({ value: 'Отметьте: есть, нет или нет сведений' })
    expect(validateCheck(form({ choice: '' }), TODAY).source).toMatch(/^Выберите, где проверено/)
    expect(validateCheck(form({ choice: OTHER, other: ' ' }), TODAY).source).toBe('Напишите, где проверено')
    expect(validateCheck(form({ value: 'unknown', choice: '' }), TODAY)).toEqual({}) // «нет сведений» — без источника
    expect(validateCheck(form({ url: 'homeportal.kz' }), TODAY).url).toBe('Ссылка должна начинаться с http:// или https://')
    expect(validateCheck(form({ date: '2026-10-20' }), TODAY).date).toBe('Дата не может быть позже сегодняшней')
    expect(validateCheck(form({ date: '' }), TODAY)).toEqual({}) // пустая дата — сегодня на сервере
    expect(validateCheck(form({ ref: 'x'.repeat(501) }), TODAY).ref).toBe('Не длиннее 500 символов')
  })
  it('даты', () => {
    expect(dateProblem('2026-02-30', TODAY)).toBe('Дата в формате ГГГГ-ММ-ДД')
    expect(dateProblem('19.10.2026', TODAY)).toBe('Дата в формате ГГГГ-ММ-ДД')
    expect(dateProblem('1999-12-31', TODAY)).toBe('Дата раньше 2000 года — проверьте год')
    expect(dateProblem('', TODAY)).toBe('Укажите дату')
    expect(dateProblem('', TODAY, false)).toBeUndefined()
    expect(todayLocal(Date.UTC(2026, 9, 18, 19, 30))).toBe('2026-10-19') // UTC+5: уже 19-е
    expect([dmy('2026-10-01'), dmy('2026-10-18T19:30:00+00:00'), dmy(null)]).toEqual(['01.10.2026', '19.10.2026', ''])
  })
  it('тело запроса: документ и перечень', () => {
    expect(checkBody('mio', form({ ref: ' № 1 ', url: 'https://h.kz', comment: '' }), 'А')).toEqual({
      author: 'А', doc: 'mio', ref: '№ 1', value: 'yes', source: 'Homeportal', sourceUrl: 'https://h.kz', checkedAt: TODAY, comment: undefined,
    })
    expect(checkBody('list', form({ value: 'no', choice: OTHER, other: 'Ответ ГАСК № 3' }), 'А')).toEqual({
      author: 'А', value: 'no', source: 'Ответ ГАСК № 3', sourceUrl: undefined, checkedAt: TODAY, comment: undefined,
    })
    expect(checkBody('bvu', form({ value: 'unknown', choice: '' }), 'А')).toMatchObject({ value: 'unknown', source: undefined })
  })
})

describe('готовность, обращения, снимки', () => {
  it('готовность', () => {
    const f = { value: '31,5', choice: 'Акт осмотра', other: '', date: TODAY, comment: '' }
    expect(validateReadiness(f, TODAY)).toEqual({})
    expect(readinessBody('confirmed', f, 'А')).toEqual({ author: 'А', kind: 'confirmed', value: 31.5, source: 'Акт осмотра', date: TODAY, comment: undefined })
    expect(validateReadiness({ ...f, value: '120', choice: '', date: '' }, TODAY)).toEqual({
      value: 'Число от 0 до 100', source: 'Выберите источник', date: 'Укажите дату' })
    expect(validateReadiness({ ...f, value: '' }, TODAY).value).toBe('Укажите готовность, %')
  })
  it('обращение', () => {
    const f = { date: TODAY, subject: ' Взносы без ДДУ ', applicants: '12', collective: true, choice: 'eOtinish', other: '', comment: '' }
    expect(validateAppeal(f, TODAY)).toEqual({})
    expect(appealBody(f, 'А')).toEqual({ author: 'А', date: TODAY, subject: 'Взносы без ДДУ', applicants: 12, collective: true, source: 'eOtinish', comment: undefined })
    expect(validateAppeal({ ...f, applicants: '1' }, TODAY).applicants).toBe('Коллективное обращение — от двух заявителей')
    expect(validateAppeal({ ...f, subject: '', applicants: '0', choice: '' }, TODAY)).toEqual({
      subject: 'Опишите суть обращения', applicants: 'Целое число от 1', source: 'Выберите, откуда обращение' })
    expect(validateAppealsCheck({ choice: '', other: '', date: '2030-01-01', comment: '' }, TODAY)).toEqual({
      source: 'Выберите, где проверено', date: 'Дата не может быть позже сегодняшней' })
  })
  it('снимок площадки', () => {
    const file = new File([new Uint8Array(10)], 'site.png', { type: 'image/png' })
    const f = { file, date: TODAY, choice: 'Дрон', other: '', activity: 'idle' as const, note: ' Кран стоит ' }
    expect(validatePhoto(f, TODAY)).toEqual({})
    expect(photoFields(f, 'А')).toEqual({ author: 'А', date: TODAY, source: 'Дрон', activity: 'idle', note: 'Кран стоит' })
    expect(validatePhoto({ ...f, file: null, activity: '' }, TODAY)).toEqual({ file: 'Выберите снимок', activity: 'Отметьте, идут ли работы' })
    const pdf = new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })
    expect(validatePhoto({ ...f, file: pdf }, TODAY).file).toBe('Нужно изображение: JPEG, PNG, WEBP или GIF')
    const big = { name: 'big.png', type: 'image/png', size: 11 * 1024 * 1024 } as File
    expect(validatePhoto({ ...f, file: big }, TODAY).file).toBe('Файл больше 10 МБ')
  })
})

describe('новые окна', () => {
  it('готовность — с текущим значением и источником, остальные — пустые с сегодняшней датой', () => {
    const c = reg({ readiness: { declared: { value: 55, source: 'Сайт застройщика', date: '2026-10-01' }, confirmed: null },
      appealsCheck: { source: 'ДЭР ВКО', checkedAt: TODAY, checkedBy: 'А' } })
    expect(newReadiness(c, 'declared', TODAY)).toEqual({ kind: 'readiness', which: 'declared',
      form: { value: '55', choice: 'Сайт застройщика', other: '', date: TODAY, comment: '' } })
    expect(newReadiness(c, 'confirmed', TODAY)).toMatchObject({ form: { value: '', choice: '' } })
    expect(newAppeal(TODAY)).toMatchObject({ kind: 'appeal', form: { date: TODAY, applicants: '1', collective: false } })
    expect(newAppealsCheck(c, TODAY)).toMatchObject({ kind: 'appealsCheck', form: { choice: 'ДЭР ВКО', date: TODAY } })
    expect(newPhoto(TODAY)).toMatchObject({ kind: 'photo', form: { file: null, activity: '', date: TODAY } })
  })
})
