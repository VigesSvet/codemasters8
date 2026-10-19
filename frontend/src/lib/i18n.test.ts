import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fmtDate, statusMeta, zoneName, zoneShort } from '../components/ui'
import type { AdStatus, ScreeningCounts, Zone } from '../data/types'
import { READ_ONLY } from '../data/snapshot'
import {
  LANG_KEY, countsLineT, getLang, i18nFor, pluralIndex, readLang, roleIn, setLang, subscribeLang, translate, translatePhrase, translatePlural, writeLang,
} from './i18n'
import { kk, kkPhrases } from './i18n/kk'
import { ru, type Key } from './i18n/ru'
import { SHOW_GROUPS, SHOW_HINT, SHOW_LABEL, SHOW_SHORT } from './mapFilters'
import { NOT_ASSESSED, model } from './risk'
import {
  DEV_SCREENING_LABEL, DISCLAIMER, DISCLAIMER_SHORT, LEGEND_LABEL, NO_SCREENING, OK_LEGEND, SCREENING_FILTER_LABEL, SCREENING_HINT, SCREENING_LABEL,
  SCREENING_SHORT, SCREENING_STATUSES, VARIANT_LABEL, VARIANT_SHORT, countsLine,
} from './screening'

/** Простой storage в памяти — как localStorage */
function memory(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init))
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, String(v)) }, raw: m }
}
const broken = () => ({
  getItem: () => { throw new DOMException('denied', 'SecurityError') },
  setItem: () => { throw new DOMException('quota', 'QuotaExceededError') },
})
const keys = Object.keys(ru) as Key[]
/** Подстановки {имя} в строке — по множеству, без порядка (в казахском порядок слов другой) */
const vars = (s: string) => [...new Set([...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]))].sort()

afterEach(() => { vi.unstubAllGlobals(); setLang('ru', memory()) })

describe('словари RU/KZ', () => {
  it('в казахском словаре те же ключи, что в русском: ни одного пропущенного и ни одного лишнего', () => {
    expect(keys.filter((k) => !(k in kk))).toEqual([])
    expect(Object.keys(kk).filter((k) => !(k in ru))).toEqual([])
  })

  it('казахские тексты не пустые и не копируют русские (кроме слов, одинаковых в обоих языках)', () => {
    for (const k of keys) {
      const v = kk[k]!
      expect(v.trim(), k).not.toBe('')
      // одинаковыми могут быть только подписи без русских слов: «{label}: {n}», «Карта», «Профиль», «Спутник», «демо»
      if (v === ru[k]) expect(['common.labelN', 'nav.map', 'shell.profile', 'lp.navSat', 'lp.sat', 'map.sat', 'cx.demo'], k).toContain(k)
    }
  })

  it('подстановки {имя} в казахском — те же, что в русском', () => {
    for (const k of keys) expect(vars(kk[k]!), k).toEqual(vars(ru[k]))
  })

  it('формы по числу: в русском три формы, в казахском одна (после числительного — единственное число)', () => {
    for (const k of keys) {
      if (!ru[k].includes('|')) continue
      expect(ru[k].split('|'), k).toHaveLength(3)
      expect(kk[k], k).not.toContain('|')
    }
  })
})

describe('перевод по ключу', () => {
  it('русский — как в словаре, казахский — свой текст; {имя} подставляются', () => {
    expect(translate('ru', 'nav.queue')).toBe('Очередь проверки')
    expect(translate('kk', 'nav.queue')).toBe('Тексеру кезегі')
    expect(translate('ru', 'common.complex', { name: 'Ертіс' })).toBe('ЖК «Ертіс»')
    expect(translate('kk', 'common.complex', { name: 'Ертіс' })).toBe('«Ертіс» ТҮК')
    // неизвестная подстановка остаётся как есть — видно, что её забыли передать
    expect(translate('ru', 'common.complex')).toBe('ЖК «{name}»')
  })

  it('нет казахского перевода (или он пустой) — русский текст', () => {
    const saved = kk['nav.map']
    try {
      delete kk['nav.map']
      expect(translate('kk', 'nav.map')).toBe('Карта')
      kk['nav.map'] = ''
      expect(translate('kk', 'nav.map')).toBe('Карта')
    } finally {
      kk['nav.map'] = saved
    }
  })

  it('формы по числу: русские правила 1/2/5 и 11–14; в казахском форма одна', () => {
    expect([1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111, 0].map((n) => pluralIndex('ru', n))).toEqual([0, 1, 1, 2, 2, 2, 2, 0, 1, 2, 0, 2, 2])
    expect(translatePlural('ru', 'map.objects', 1)).toBe('1 объект')
    expect(translatePlural('ru', 'map.objects', 3)).toBe('3 объекта')
    expect(translatePlural('ru', 'map.objects', 12)).toBe('12 объектов')
    expect(translatePlural('kk', 'map.objects', 12)).toBe('12 нысан')
    expect(translatePlural('ru', 'lp.complexesN', 21)).toBe('жилой комплекс')
  })
})

describe('русский словарь совпадает с подписями lib/*.ts — по-русски экраны не изменились', () => {
  it('статусы публикаций и зоны', () => {
    for (const s of Object.keys(statusMeta) as AdStatus[]) expect(translate('ru', `status.${s}`)).toBe(statusMeta[s].name)
    for (const z of Object.keys(zoneName) as Zone[]) {
      expect(translate('ru', `zone.name.${z}`)).toBe(zoneName[z])
      expect(translate('ru', `zone.short.${z}`)).toBe(zoneShort[z])
    }
  })

  it('«Проверка по рекламе» и «Показывать»', () => {
    for (const s of SCREENING_STATUSES) {
      expect(translate('ru', `scr.filter.${s}`)).toBe(SCREENING_FILTER_LABEL[s])
      expect(translate('ru', `scr.short.${s}`)).toBe(SCREENING_SHORT[s])
      expect(translate('ru', `scr.hint.${s}`)).toBe(SCREENING_HINT[s])
      expect(translate('ru', `scr.legend.${s}`)).toBe(LEGEND_LABEL[s])
    }
    expect(translate('ru', 'scr.okLegend')).toBe(OK_LEGEND)
    for (const g of SHOW_GROUPS) {
      expect(translate('ru', `show.label.${g}`)).toBe(SHOW_LABEL[g])
      expect(translate('ru', `show.short.${g}`)).toBe(SHOW_SHORT[g])
      expect(translate('ru', `show.hint.${g}`)).toBe(SHOW_HINT[g])
    }
  })

  it('строка счётчиков рекламы — как countsLine', () => {
    const t = i18nFor('ru').t
    const fmt = () => '17 окт.'
    const cs: (ScreeningCounts | null)[] = [
      null,
      { ads: 0, concerning: 0, windowDays: 120, lastAdAt: null } as unknown as ScreeningCounts,
      { ads: 5, concerning: 2, windowDays: 120, lastAdAt: '2026-10-17T10:00:00Z' } as unknown as ScreeningCounts,
      { ads: 3, concerning: 0, windowDays: 90, lastAdAt: null } as unknown as ScreeningCounts,
    ]
    for (const c of cs) expect(countsLineT(t, c, fmt)).toBe(countsLine(c, fmt))
    expect(countsLineT(i18nFor('kk').t, cs[2], fmt)).toBe('5 жарияланым · 120 күнде ақша қабылдау белгісі бар 2 · соңғысы 17 окт.')
  })
})

describe('даты', () => {
  it('по-казахски — свои названия месяцев (браузерная локаль kk даёт «М10»), по-русски — как раньше', () => {
    const d = new Date(2026, 9, 19, 12).toISOString()
    expect(i18nFor('kk').dayMonth(d)).toBe('19 қаз.')
    expect(i18nFor('kk').date(d)).toBe('2026 ж. 19 қазан')
    expect(i18nFor('ru').dayMonth(d)).toBe(new Date(d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }))
    expect(i18nFor('ru').date(d)).toBe(fmtDate(d))
    expect(i18nFor('kk').date('не дата')).toBe('не дата')
  })
})

describe('подписи из данных (tr)', () => {
  it('известная фраза — по-казахски, неизвестная и любая по-русски — как есть', () => {
    expect(translatePhrase('kk', 'каркас')).toBe('қаңқа')
    expect(translatePhrase('kk', 'ЖК «Ертіс» на Протозанова')).toBe('ЖК «Ертіс» на Протозанова')
    expect(translatePhrase('ru', 'каркас')).toBe('каркас')
    expect(translatePhrase('kk', '')).toBe('')
  })

  it('у всех подписей статусов, стадий, зон модели и общих текстов есть казахская фраза', () => {
    const must = [
      ...Object.values(SCREENING_LABEL), ...Object.values(DEV_SCREENING_LABEL), ...Object.values(VARIANT_LABEL), ...Object.values(VARIANT_SHORT),
      ...Object.values(SCREENING_SHORT), 'котлован', 'фундамент', 'каркас', 'кровля', 'отделка', model.builtStage, 'сдан', 'стадия не известна',
      ...model.zones.items.map((z) => z.name), model.zones.items.find((z) => z.zone === 'red')!.rule, NOT_ASSESSED,
      READ_ONLY, DISCLAIMER, DISCLAIMER_SHORT, NO_SCREENING,
    ]
    expect(must.filter((s) => !kkPhrases[s])).toEqual([])
  })

  it('роли стартового набора: должность, ведомство и значок по-казахски; автор в журнале — прежний', () => {
    const analysts = JSON.parse(readFileSync(new URL('../../../shared/snapshot/analysts.json', import.meta.url), 'utf8')) as { name: string; role: string }[]
    const kz = i18nFor('kk')
    for (const a of analysts) {
      const r = roleIn(kz, a)
      expect(r.title, a.role).not.toMatch(/аналитик|специалист/i)
      expect(r.dept, a.role).not.toBe('')
      expect(r.dept, a.role).not.toMatch(/ДЭР|ГАСК|ВКО/)
    }
    expect(roleIn(kz, { name: 'Ведущий аналитик ДЭР', role: 'Ведущий аналитик · ДЭР ВКО' })).toEqual({ title: 'Жетекші талдаушы', dept: 'ШҚО бойынша ЭТД', short: 'ЖТ' })
    expect(roleIn(i18nFor('ru'), { name: 'Ведущий аналитик ДЭР', role: 'Ведущий аналитик · ДЭР ВКО' })).toEqual({ title: 'Ведущий аналитик', dept: 'ДЭР ВКО', short: 'ВА' })
  })
})

describe('выбор языка', () => {
  it('сохраняется под ключом saq.lang и читается обратно', () => {
    const box = memory()
    expect(writeLang('kk', box)).toBe(true)
    expect(box.raw.get(LANG_KEY)).toBe('kk')
    expect(readLang(box)).toBe('kk')
  })

  it('нет записи, чужое значение или недоступный storage — русский по умолчанию', () => {
    expect(readLang(memory())).toBe('ru')
    expect(readLang(memory({ [LANG_KEY]: 'en' }))).toBe('ru')
    expect(readLang(broken())).toBe('ru')
    expect(readLang(null)).toBe('ru')
    expect(writeLang('kk', broken())).toBe(false)
    expect(writeLang('kk', null)).toBe(false)
  })

  it('setLang меняет язык без перезагрузки: подписчики узнают сразу, <html lang> обновляется, выбор запомнен', () => {
    const html = { lang: 'ru' }
    vi.stubGlobal('document', { documentElement: html })
    const box = memory()
    const seen: string[] = []
    const off = subscribeLang(() => seen.push(getLang()))
    setLang('kk', box)
    setLang('kk', box) // тот же язык — без лишней перерисовки
    expect(getLang()).toBe('kk')
    expect(seen).toEqual(['kk'])
    expect(html.lang).toBe('kk')
    expect(box.raw.get(LANG_KEY)).toBe('kk')
    off()
    setLang('ru', box)
    expect(seen).toEqual(['kk'])
    expect(html.lang).toBe('ru')
  })

  it('storage недоступен — язык всё равно меняется (до перезагрузки)', () => {
    setLang('kk', broken())
    expect(getLang()).toBe('kk')
  })
})

