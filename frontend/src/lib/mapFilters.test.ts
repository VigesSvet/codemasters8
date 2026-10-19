import { describe, expect, it } from 'vitest'
import type { Complex, RiskResult, Screening, ScreeningDecision, ScreeningStatus } from '../data/types'
import {
  MAP_DEFAULT, RED_FROM, RISK_DEFAULT, RISK_MAX, SHOW_DEFAULT, SHOW_GROUPS, YELLOW_FROM, countShowGroups, inRisk, isRiskDefault, isShowDefault, isShown,
  passesMapFilters, riskCounts, riskSummary, screeningSummary, showGroupOf, showSummary, toggleStatus, type MapFilters,
} from './mapFilters'

type C = Pick<Complex, 'stage' | 'screening' | 'decision'>
const scr = (status: ScreeningStatus, variant: Screening['variant'] = null): Screening => ({
  status, label: '', variant, reasons: [], counts: { ads: 0, concerning: 0, windowDays: 120 }, basis: 'hypothesis', inRegistry: status === 'ok', disclaimer: '',
})
const dec = (kind: ScreeningDecision['kind'], status: ScreeningDecision['status'] = 'active'): ScreeningDecision => ({
  id: 1, kind, status, label: '', by: 'Омарова Ж.', source: 'ЕГКН',
})
const cx = (x: Partial<C> = {}): C => ({ stage: '', screening: null, decision: null, ...x })
const risk = (score: number, x: Partial<RiskResult> = {}): Pick<RiskResult, 'score' | 'needsCheck' | 'assessed'> => ({ score, needsCheck: false, assessed: true, ...x })
const NA = risk(0, { assessed: false, needsCheck: true })

describe('модель', () => {
  it('шкала и пороги — из shared/risk_model.json', () => {
    expect(RISK_MAX).toBe(100)
    expect(YELLOW_FROM).toBe(25)
    expect(RED_FROM).toBe(60)
    expect(RISK_DEFAULT).toEqual({ min: 0, max: 100, withNA: true, needsCheck: false })
  })
})

describe('«Проверка по рекламе»', () => {
  it('toggleStatus: добавляет и снимает, порядок — как у чипов', () => {
    expect(toggleStatus([], 'suspect')).toEqual(['suspect'])
    expect(toggleStatus(['suspect'], 'check')).toEqual(['check', 'suspect'])
    expect(toggleStatus(['check', 'suspect'], 'suspect')).toEqual(['check'])
    expect(toggleStatus(['check'], 'check')).toEqual([])
    expect(toggleStatus(['unknown'], 'ok')).toEqual(['ok', 'unknown'])
  })
  it('screeningSummary: подписи чипов через точку; ничего — «все статусы»', () => {
    expect(screeningSummary([])).toBe('все статусы')
    expect(screeningSummary(['check', 'suspect'])).toBe('Подозрение · Требуют проверки')
    expect(screeningSummary(['ok', 'unknown', 'check'])).toBe('Требуют проверки · Нет данных · Без замечаний')
    expect(screeningSummary(['ok'])).toBe('Без замечаний')
  })
})

describe('«Формальный риск»', () => {
  it('без оценки — по переключателю, независимо от диапазона', () => {
    expect(inRisk(NA, RISK_DEFAULT)).toBe(true)
    expect(inRisk(NA, { ...RISK_DEFAULT, min: 25 })).toBe(true)
    expect(inRisk(NA, { ...RISK_DEFAULT, withNA: false })).toBe(false)
    // assessed не пришло (сервер старой версии) — оценён
    expect(inRisk({ score: 10, needsCheck: false }, { ...RISK_DEFAULT, withNA: false })).toBe(true)
  })
  it('с оценкой — балл в диапазоне, границы включительно', () => {
    const f = { ...RISK_DEFAULT, min: 25, max: 60 }
    expect(inRisk(risk(24), f)).toBe(false)
    expect(inRisk(risk(25), f)).toBe(true)
    expect(inRisk(risk(60), f)).toBe(true)
    expect(inRisk(risk(61), f)).toBe(false)
    expect(inRisk(risk(0), RISK_DEFAULT)).toBe(true)
    expect(inRisk(risk(100), RISK_DEFAULT)).toBe(true)
  })
  it('«нужна проверка» — у всех, и у ЖК без оценки', () => {
    const f = { ...RISK_DEFAULT, needsCheck: true }
    expect(inRisk(risk(35), f)).toBe(false)
    expect(inRisk(risk(35, { needsCheck: true }), f)).toBe(true)
    expect(inRisk(NA, f)).toBe(true)
    expect(inRisk(risk(0, { assessed: false, needsCheck: false }), f)).toBe(false)
  })
  it('isRiskDefault и сводка', () => {
    expect(isRiskDefault(RISK_DEFAULT)).toBe(true)
    expect(isRiskDefault({ ...RISK_DEFAULT, min: 1 })).toBe(false)
    expect(isRiskDefault({ ...RISK_DEFAULT, max: 99 })).toBe(false)
    expect(isRiskDefault({ ...RISK_DEFAULT, withNA: false })).toBe(false)
    expect(isRiskDefault({ ...RISK_DEFAULT, needsCheck: true })).toBe(false)
    expect(riskSummary(RISK_DEFAULT)).toBe('все баллы')
    expect(riskSummary({ ...RISK_DEFAULT, min: 25 })).toBe('баллы 25–100')
    expect(riskSummary({ min: 25, max: 100, withNA: false, needsCheck: false })).toBe('баллы 25–100 · без оценки скрыты')
    expect(riskSummary({ ...RISK_DEFAULT, needsCheck: true })).toBe('все баллы · нужна проверка')
  })
  it('riskCounts: без оценки, с оценкой, в диапазоне, нужна проверка', () => {
    const rs = [NA, NA, risk(35, { needsCheck: true }), risk(10), risk(60), risk(24)]
    expect(riskCounts(rs, { min: 25, max: 100 })).toEqual({ na: 2, assessed: 4, inRange: 2, needsCheck: 3 })
    expect(riskCounts(rs, { min: 0, max: 100 })).toEqual({ na: 2, assessed: 4, inRange: 4, needsCheck: 3 })
    expect(riskCounts([], { min: 0, max: 100 })).toEqual({ na: 0, assessed: 0, inRange: 0, needsCheck: 0 })
  })
})

describe('«Показывать»: группы ЖК', () => {
  it('действующее решение «Объект сдан» — сданные; «Проверено» — прошедшие проверку', () => {
    expect(showGroupOf(cx({ decision: dec('commissioned'), screening: scr('ok', 'commissioned'), stage: 'введён' }))).toBe('built')
    expect(showGroupOf(cx({ decision: dec('no_issues'), screening: scr('ok', 'analyst') }))).toBe('checked')
  })
  it('решение важнее стадии: проверенный аналитиком введённый дом — «прошедшие проверку», а не «сданные»', () => {
    expect(showGroupOf(cx({ decision: dec('no_issues'), screening: scr('ok', 'analyst'), stage: 'введён' }))).toBe('checked')
  })
  it('без объекта решения — по варианту оценки', () => {
    expect(showGroupOf(cx({ screening: scr('ok', 'commissioned') }))).toBe('built')
    expect(showGroupOf(cx({ screening: scr('ok', 'analyst') }))).toBe('checked')
    // вариант бывает только у ok
    expect(showGroupOf(cx({ screening: scr('check', 'analyst') }))).toBeNull()
  })
  it('стадия «введён» без решения — сданные, если замечаний нет; требующий проверки введённый дом не прячется', () => {
    expect(showGroupOf(cx({ stage: 'введён' }))).toBe('built')
    expect(showGroupOf(cx({ stage: 'введён', screening: scr('ok') }))).toBe('built')
    expect(showGroupOf(cx({ stage: 'введён', screening: scr('check') }))).toBeNull()
    expect(showGroupOf(cx({ stage: 'введён', screening: scr('suspect') }))).toBeNull()
  })
  it('ok без решения (в реестре) — без замечаний', () => {
    expect(showGroupOf(cx({ screening: scr('ok'), stage: 'каркас' }))).toBe('clean')
  })
  it('решение, снятое новой рекламой, группы не даёт — по автоматической оценке', () => {
    expect(showGroupOf(cx({ decision: dec('no_issues', 'reopened'), screening: scr('suspect') }))).toBeNull()
    expect(showGroupOf(cx({ decision: dec('commissioned', 'reopened'), screening: scr('check') }))).toBeNull()
    // сданный дом с решением, снятым новой рекламой (старт продаж новой очереди), — на виду при любых переключателях
    expect(showGroupOf(cx({ decision: dec('commissioned', 'reopened'), screening: scr('check'), stage: 'введён' }))).toBeNull()
    expect(showGroupOf(cx({ decision: dec('no_issues', 'reopened'), screening: scr('ok') }))).toBe('clean')
  })
  it('требуют проверки, подозрение, нет данных и без оценки — вне групп', () => {
    for (const s of ['check', 'suspect', 'unknown'] as const) expect(showGroupOf(cx({ screening: scr(s) }))).toBeNull()
    expect(showGroupOf(cx())).toBeNull()
  })
  it('isShown: группа скрыта переключателем, вне групп — всегда видны', () => {
    const off = { built: false, clean: false, checked: false }
    expect(isShown(cx({ screening: scr('ok') }), off)).toBe(false)
    expect(isShown(cx({ screening: scr('ok') }), { ...off, clean: true })).toBe(true)
    expect(isShown(cx({ stage: 'введён' }), { ...SHOW_DEFAULT, built: false })).toBe(false)
    expect(isShown(cx({ screening: scr('suspect') }), off)).toBe(true)
    expect(isShown(cx(), off)).toBe(true)
  })
  it('countShowGroups: каждый ЖК ровно в одной группе или в other — сумма равна числу ЖК', () => {
    const cs = [
      cx({ decision: dec('commissioned'), screening: scr('ok', 'commissioned'), stage: 'введён' }),
      cx({ decision: dec('no_issues'), screening: scr('ok', 'analyst'), stage: 'введён' }),
      cx({ stage: 'введён', screening: scr('ok') }),
      cx({ screening: scr('ok') }),
      cx({ screening: scr('ok') }),
      cx({ screening: scr('check') }),
      cx({ screening: scr('suspect') }),
      cx({ decision: dec('no_issues', 'reopened'), screening: scr('check') }),
      cx(),
    ]
    const n = countShowGroups(cs)
    expect(n).toEqual({ built: 2, checked: 1, clean: 2, other: 4 })
    expect(n.built + n.clean + n.checked + n.other).toBe(cs.length)
    // выключенная группа скрывает ровно столько ЖК, сколько в её счётчике
    for (const g of SHOW_GROUPS) expect(cs.filter((c) => !isShown(c, { ...SHOW_DEFAULT, [g]: false })).length).toBe(n[g])
  })
  it('isShowDefault и сводка', () => {
    expect(isShowDefault(SHOW_DEFAULT)).toBe(true)
    expect(isShowDefault({ ...SHOW_DEFAULT, clean: false })).toBe(false)
    expect(showSummary(SHOW_DEFAULT)).toBe('все группы')
    expect(showSummary({ ...SHOW_DEFAULT, built: false })).toBe('скрыты: сданные')
    expect(showSummary({ built: false, clean: true, checked: false })).toBe('скрыты: сданные, проверенные')
  })
})

describe('видимые ЖК: три блока вместе', () => {
  const items = [
    { id: 'suspect-35', c: cx({ screening: scr('suspect') }), r: risk(35, { needsCheck: true }) },
    { id: 'check-na', c: cx({ screening: scr('check') }), r: NA },
    { id: 'ok-reg-10', c: cx({ screening: scr('ok') }), r: risk(10) },
    { id: 'built-na', c: cx({ decision: dec('commissioned'), screening: scr('ok', 'commissioned'), stage: 'введён' }), r: NA },
    { id: 'checked-70', c: cx({ decision: dec('no_issues'), screening: scr('ok', 'analyst') }), r: risk(70) },
    { id: 'unknown-na', c: cx({ screening: scr('unknown') }), r: NA },
  ]
  const ids = (f: MapFilters) => items.filter((x) => passesMapFilters(x, f)).map((x) => x.id)

  it('по умолчанию видны все', () => {
    expect(ids(MAP_DEFAULT)).toEqual(items.map((x) => x.id))
  })
  it('баллы 25–100 без ЖК без оценки — только оценённые в диапазоне', () => {
    expect(ids({ ...MAP_DEFAULT, risk: { ...RISK_DEFAULT, min: 25, withNA: false } })).toEqual(['suspect-35', 'checked-70'])
  })
  it('баллы 25–100 с ЖК без оценки — плюс все без оценки', () => {
    expect(ids({ ...MAP_DEFAULT, risk: { ...RISK_DEFAULT, min: 25 } })).toEqual(['suspect-35', 'check-na', 'built-na', 'checked-70', 'unknown-na'])
  })
  it('чипы статусов ∧ риск ∧ «Показывать»', () => {
    expect(ids({ ...MAP_DEFAULT, screening: ['ok'] })).toEqual(['ok-reg-10', 'built-na', 'checked-70'])
    expect(ids({ ...MAP_DEFAULT, screening: ['ok'], show: { ...SHOW_DEFAULT, built: false } })).toEqual(['ok-reg-10', 'checked-70'])
    expect(ids({ ...MAP_DEFAULT, screening: ['ok'], show: { built: false, clean: false, checked: false } })).toEqual([])
    expect(ids({ ...MAP_DEFAULT, screening: ['suspect', 'check'], risk: { ...RISK_DEFAULT, withNA: false } })).toEqual(['suspect-35'])
    expect(ids({ ...MAP_DEFAULT, show: { built: false, clean: false, checked: false } })).toEqual(['suspect-35', 'check-na', 'unknown-na'])
  })
})
