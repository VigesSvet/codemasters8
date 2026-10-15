import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { LinkHypothesis } from '../data/types'
import { HypothesisItem } from './Hypothesis'

const h: LinkHypothesis = {
  complexId: 'c10', complexName: 'Ульба Ривер', confidence: 0.34, low: true, quote: '«Ульба Ривер»',
  reasons: [{ feature: 'name', detail: 'Название «Ульба Ривер» в тексте', weight: 1 }],
}
const html = (props: Parameters<typeof HypothesisItem>[0]) => renderToStaticMarkup(createElement(HypothesisItem, props)).replace(/<!-- -->/g, '')
const buttons = (s: string) => [...s.matchAll(/<button\b[^>]*>([\s\S]*?)<\/button>/g)]
const textOf = (s: string) => s.replace(/<[^>]+>/g, '')

describe('HypothesisItem — выбор гипотезы в форме', () => {
  it('кнопка — только заголовок: внутри нет списков и блоков, имя — «ЖК «X» N%», а не вся карточка', () => {
    const s = html({ h, selected: false, onPick: () => {}, note: 'будет выбрана, если не указать другую' })
    const bs = buttons(s)
    expect(bs).toHaveLength(1)
    const inner = bs[0][1]
    expect(inner).not.toMatch(/<(ul|li|div|p)\b/)
    expect(textOf(inner)).toBe('ЖК «Ульба Ривер»34%низкая уверенность')
    expect(bs[0][0]).toContain('aria-pressed="false"')
    // причины, цитата и пометка — рядом с кнопкой, обычным содержимым карточки
    expect(s).toMatch(/<\/button><ul class="ai-reasons">/)
    expect(s).toContain('будет выбрана, если не указать другую')
  })

  it('выбранная — aria-pressed="true"', () => {
    expect(buttons(html({ h, selected: true, onPick: () => {} }))[0][0]).toContain('aria-pressed="true"')
  })

  it('без выбора — карточка без кнопки выбора, с кнопками решения', () => {
    const s = html({ h, children: createElement('button', { type: 'button' }, 'Принять') })
    expect(buttons(s).map((b) => textOf(b[1]))).toEqual(['Принять'])
  })
})
