// Нормативная база (shared/law.json): ссылки факторов модели риска, тексты оснований — те же, что basis_text в
// backend/app/law.py (PDF), — и ссылки модели на нормы в анализе ИИ как гипотезы.
import { describe, expect, it } from 'vitest'
import { basisText, excerptsFor, factorsOf, law, lawArticle, lawRefs, refText, refWhy } from './law'
import { legalRefArticle, legalRefText } from './llm'
import { model } from './risk'

const CODES = model.factors.map((f) => f.code)

describe('нормативная база', () => {
  it('закон и статьи: ориентиры ТЗ 7, 8, 18 и реклама — 23, ссылки на «Әділет» с якорем статьи', () => {
    expect(law.act.number).toBe('486-V')
    expect(law.articles.map((a) => a.num)).toEqual(['7', '8', '18', '23'])
    for (const a of law.articles) expect(a.url).toBe(`${law.act.url}#z${a.num}`)
    expect(law.nuance).toContain('«нет разрешения = незаконно»')
  })

  it('у каждого фактора поле law; ссылки фактора совпадают с refs law.json', () => {
    for (const f of model.factors) {
      expect(f.law, f.code).toBeDefined()
      expect(lawRefs(f.code)).toEqual(refWhy(f.code).map(({ article, parts }) => ({ article, parts })))
    }
    expect(lawRefs('R4')).toEqual([])
    expect(lawRefs('mio')).toEqual([{ article: '18', parts: ['1', '8'] }]) // документ основания — из refs law.json
  })

  it('текст основания — как basis_text на бэкенде', () => {
    expect(basisText('R1')).toBe('Основание: ст. 7 п. 1, 2; ст. 8 п. 6; ст. 18 п. 1 Закона РК «О долевом участии в жилищном строительстве»')
    expect(basisText('R3')).toBe('Основание: ст. 23 Закона РК «О долевом участии в жилищном строительстве»')
    expect(basisText('R6')).toBe('')
    expect(refText({ article: '7', parts: [] })).toBe('ст. 7')
  })

  it('выдержки для ссылки: пункты ссылки, без пунктов — вся статья; факторы статьи', () => {
    expect(excerptsFor({ article: '8', parts: ['6'] }).map((e) => e.part)).toEqual(['6'])
    expect(excerptsFor({ article: '8', parts: ['6'] })[0].text).toMatch(/^6\. Заключенный договор о предоставлении гарантии/)
    expect(excerptsFor({ article: '23', parts: [] })).toEqual(lawArticle('23')!.excerpts)
    expect(excerptsFor({ article: '99', parts: [] })).toEqual([])
    expect(factorsOf('18', CODES)).toEqual(['R1', 'R2'])
    expect(factorsOf('23', CODES)).toEqual(['R3'])
  })

  it('ссылка модели на норму — гипотеза «может относиться», не вердикт', () => {
    expect(legalRefText({ article: '7', part: '2' })).toBe('Может относиться к ст. 7 п. 2 Закона РК «О долевом участии в жилищном строительстве»')
    expect(legalRefText({ article: '23', part: '' })).toBe('Может относиться к ст. 23 Закона РК «О долевом участии в жилищном строительстве»')
    expect(legalRefArticle({ article: '8' })?.url).toBe('https://adilet.zan.kz/rus/docs/Z1600000486#z8')
    expect(legalRefArticle({ article: '99' as '7' })).toBeNull()
  })
})
