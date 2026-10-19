import { describe, expect, it } from 'vitest'
import type { Ad, LinkHypothesis, OcrResult } from '../data/types'
import { OCR_EMPTY, OCR_KEPT, OCR_NOT_FOUND, decisionPatch, linkConf, linkText, ocrNotice, ocrOnEdit, ocrOnNewShot, ocrOnResult, visibleHypotheses } from './ai'

const ocr = (text: string, extra: Partial<OcrResult> = {}): OcrResult => ({ text, engine: 'tesseract', languages: ['rus', 'eng'], durationMs: 900, ...extra })
const count = (s: string, sub: string) => s.split(sub).length - 1

describe('поле OCR при смене скриншота', () => {
  const A = 'ALTAI RESIDENCE · Бронь 200 000 тг · взнос 30% · рассрочка 18 мес'

  it('новый скриншот без текста или с ошибкой не оставляет текст прежнего', () => {
    const afterA = ocrOnResult(OCR_EMPTY, ocr(`  ${A}\n`))
    expect(afterA).toEqual({ text: A, auto: A, kept: false })
    // скриншот B: поле очищается сразу при загрузке — и пустой ответ, и 422 оставляют его пустым
    const newB = ocrOnNewShot(afterA)
    expect(newB.text).toBe('')
    expect(ocrOnResult(newB, ocr('')).text).toBe('')
    expect(ocrOnResult(newB, ocr('', { engine: 'none', languages: [] })).text).toBe('')
  })

  it('найденный текст нового скриншота заменяет прежний', () => {
    const afterA = ocrOnResult(OCR_EMPTY, ocr(A))
    expect(ocrOnResult(ocrOnNewShot(afterA), ocr('ШЫГЫС ТАУЭР · ДДУ')).text).toBe('ШЫГЫС ТАУЭР · ДДУ')
  })

  it('введённое вручную не пропадает молча: остаётся с пометкой, пока OCR не найдёт текст', () => {
    const typed = ocrOnEdit(ocrOnResult(OCR_EMPTY, ocr(A)), `${A} (поправлено)`)
    const newB = ocrOnNewShot(typed)
    expect(newB).toEqual({ text: `${A} (поправлено)`, auto: '', kept: true })
    expect(ocrOnResult(newB, ocr(''))).toEqual(newB)
    expect(ocrOnResult(newB, ocr('', { engine: 'none' }))).toEqual(newB)
    expect(ocrOnResult(newB, ocr('Новый текст'))).toEqual({ text: 'Новый текст', auto: 'Новый текст', kept: false })
    // аналитик поправил поле — пометка снята
    expect(ocrOnEdit(newB, 'проверено').kept).toBe(false)
  })

  it('пустое поле при новом скриншоте — без пометки', () => {
    expect(ocrOnNewShot(OCR_EMPTY)).toEqual(OCR_EMPTY)
    expect(ocrOnNewShot({ text: '   ', auto: '', kept: false })).toEqual(OCR_EMPTY)
  })
})

describe('ocrNotice — без повторов сообщения сервера', () => {
  it('«не найден» — один раз, без дубля из detail', () => {
    const n = ocrNotice(ocr('', { detail: OCR_NOT_FOUND }))!
    expect(count(n.text, OCR_NOT_FOUND)).toBe(1)
    expect(n.extra).toBeUndefined()
  })

  it('состояние OCR из /ocr/status (уже показано) не повторяется', () => {
    const st = 'Нет языковых пакетов: kaz — распознаём на rus+eng'
    const n = ocrNotice(ocr('', { detail: `${st}. ${OCR_NOT_FOUND}` }), st)!
    expect(count(n.text, OCR_NOT_FOUND)).toBe(1)
    expect(n.text).not.toContain(st)
    expect(n.extra).toBeUndefined()
  })

  it('другая причина пустого текста — вместо «не найден»', () => {
    const n = ocrNotice(ocr('', { detail: 'Распознавание не уложилось в 60 с — уменьшите или обрежьте скриншот' }))!
    expect(n.text).toContain('Текст не распознан: Распознавание не уложилось в 60 с — уменьшите или обрежьте скриншот.')
    expect(n.text).not.toContain(OCR_NOT_FOUND)
  })

  it('OCR недоступен: пояснение — если оно не повторяет статус', () => {
    const st = 'Tesseract не установлен на сервере'
    expect(ocrNotice(ocr('', { engine: 'none', detail: st }), st)!.extra).toBeUndefined()
    expect(ocrNotice(ocr('', { engine: 'none', detail: 'Tesseract не запускается: x' }), st)!.extra).toBe('Tesseract не запускается: x')
  })

  it('в поле текст, введённый раньше, — пометка вместо «введите вручную»', () => {
    const n = ocrNotice(ocr(''), null, true)!
    expect(n.text).toContain(OCR_KEPT)
    expect(n.text).not.toContain('введите его вручную')
  })

  it('текст найден или ответа нет — без сообщения', () => {
    expect(ocrNotice(ocr('Бронь 200 000 тг'))).toBeNull()
    expect(ocrNotice(null)).toBeNull()
  })
})

const hyp = (complexId: string, confidence: number, low = confidence < 0.5): LinkHypothesis =>
  ({ complexId, complexName: complexId, confidence, low, reasons: [] })

describe('уверенность в текущей привязке-гипотезе', () => {
  // как a9 тестового сида: при сборе 54%, движок сейчас — подборка, у каждого ЖК 34% и низкая уверенность
  const a9 = { complexId: 'c12', matchConfidence: 0.54, source: 'parser' as const, status: 'new' as const, linkConfirmedBy: null, linkConfirmedAt: null }
  const data = { hypotheses: [hyp('c3', 0.34), hyp('c10', 0.34), hyp('c12', 0.34)] }

  it('на экране — уверенность по текущему анализу, сохранённая подписана отдельно', () => {
    const lc = linkConf(a9, data)
    expect(lc.now?.confidence).toBe(0.34)
    expect(lc.differs).toBe(true)
    expect(linkText(a9, lc, true)).toBe('гипотеза ИИ 34% · низкая')
    expect(linkText(a9, lc)).toBe('гипотеза ИИ 34%, низкая уверенность (при сборе — 54%) · аналитик не принял')
  })

  it('без анализа (офлайн-копия, загрузка, ЖК нет в выдаче) — сохранённая, с пометкой когда получена', () => {
    expect(linkText(a9, linkConf(a9))).toBe('гипотеза ИИ при сборе 54% · аналитик не принял')
    expect(linkText(a9, linkConf(a9, { hypotheses: [hyp('c3', 0.8)] }), true)).toBe('гипотеза ИИ при сборе 54%')
    const manual = { ...a9, source: 'manual' as const }
    expect(linkText(manual, linkConf(manual), true)).toBe('гипотеза ИИ при добавлении 54%')
  })

  it('те же числа — без второго', () => {
    const lc = linkConf({ ...a9, matchConfidence: 0.7 }, { hypotheses: [hyp('c12', 0.704)] })
    expect(lc.differs).toBe(false)
    expect(linkText(a9, lc)).toBe('гипотеза ИИ 70% · аналитик не принял')
  })

  it('факт — без уверенности ИИ', () => {
    const lc = linkConf(a9, data)
    expect(linkText({ ...a9, linkConfirmedBy: 'Нестерова А.' }, lc, true)).toBe('факт · Нестерова А.')
    expect(linkText({ ...a9, status: 'confirmed' }, lc, true)).toBe('факт · нарушение')
  })
})

describe('visibleHypotheses — отклонённый ЖК уходит из списка сразу после решения', () => {
  const data = { hypotheses: [hyp('c3', 0.6), hyp('c10', 0.4)], rejectedLinks: [] as string[] }

  it('стор уже знает об отклонении, анализ ещё старый', () => {
    const v = visibleHypotheses({ rejectedLinks: ['c3'] }, data)
    expect(v.hypotheses.map((h) => h.complexId)).toEqual(['c10'])
    expect(v.rejected).toEqual(['c3'])
  })

  it('в сторе нет поля — берём из анализа', () => {
    expect(visibleHypotheses({}, { ...data, rejectedLinks: ['c10'] }).hypotheses.map((h) => h.complexId)).toEqual(['c3'])
    expect(visibleHypotheses({}, null)).toEqual({ rejected: [], hypotheses: [] })
  })
})

describe('decisionPatch — ответ решения по ИИ не откатывает статус', () => {
  it('статус, ответственный и скриншот вкладки остаются; поля привязки и решения — с сервера', () => {
    const local: Ad = {
      id: 'a9', platform: 'instagram', account: '@x', url: '', date: '2026-10-01', text: 't', image: 'blob:local', complexId: 'c12',
      matchConfidence: 0.54, linkConfirmedBy: null, source: 'parser', status: 'in_review', assignee: 'Нестерова А.',
    }
    // ответ accept сериализован до параллельного PATCH /status: в нём ещё «Новая» и без ответственного
    const got: Ad = { ...local, image: undefined, status: 'new', assignee: undefined, matchConfidence: 0.34, linkConfirmedBy: 'Нестерова А.',
      linkConfirmedAt: '2026-10-16T09:00:00+00:00', rejectedLinks: [], termsReview: null }
    const patch = decisionPatch(got)
    expect(Object.keys(patch)).not.toContain('status')
    expect(Object.keys(patch)).not.toContain('assignee')
    expect(Object.keys(patch)).not.toContain('image')
    const next = { ...local, ...patch }
    expect(next).toMatchObject({ status: 'in_review', assignee: 'Нестерова А.', image: 'blob:local', matchConfidence: 0.34, linkConfirmedBy: 'Нестерова А.' })
  })

  it('снятая привязка и решение по условиям доходят до стора', () => {
    const got = { complexId: null, matchConfidence: 0, rejectedLinks: ['c12'], termsReview: 'rejected', termsReviewBy: 'Исаев Б.' } as unknown as Ad
    expect(decisionPatch(got)).toMatchObject({ complexId: undefined, matchConfidence: 0, linkConfirmedBy: null, rejectedLinks: ['c12'], termsReview: 'rejected', termsReviewBy: 'Исаев Б.', termsReviewAt: null })
  })
})
