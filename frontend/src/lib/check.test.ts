import { describe, expect, it } from 'vitest'
import type { SourceRecord } from '../data/types'
import { checkFacts } from './check'

const rec = (over: Partial<SourceRecord>): SourceRecord =>
  ({ id: 1, key: 'k', source: 'homeportal', sourceName: 'Homeportal', name: 'ЖК', fundingMethod: 'guarantee', ...over }) as SourceRecord

describe('публичная проверка ЖК: ссылки записей источников', () => {
  it('ссылка http(s) остаётся ссылкой "Открыть"', () => {
    const f = checkFacts([rec({ sourceUrl: 'https://homeportal.kz/projects/1' }), rec({ id: 2, source: 'ashyq', sourceUrl: 'http://ashyq.data.gov.kz/x' })])
    expect(f.docs.guarantee[0].url).toBe('https://homeportal.kz/projects/1')
    expect(f.kazreestr[0].url).toBe('http://ashyq.data.gov.kz/x')
  })

  it('другая схема, не ссылка или пусто - null во всех трёх местах страницы: документ, Казреестр, раздел', () => {
    const bad = ['javascript:alert(1)', 'data:text/html,<b>x</b>', 'homeportal.kz/projects/1', '  ', null, undefined]
    for (const sourceUrl of bad) {
      const f = checkFacts([
        rec({ sourceUrl }),
        rec({ id: 2, source: 'ashyq', sourceUrl }),
        rec({ id: 3, source: 'homeportal_section', fundingMethod: null, sourceSection: 'Без разрешения на дольщиков', sourceUrl }),
      ])
      expect([f.docs.guarantee[0].url, f.kazreestr[0].url, f.section?.url]).toEqual([null, null, null])
    }
  })
})
