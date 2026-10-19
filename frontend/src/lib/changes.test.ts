import { describe, expect, it } from 'vitest'
import { changed, ddmm, diffWords, tokens } from './changes'

const side = (d: [string, string][], skip: string) => d.filter(([op]) => op !== skip).map(([, t]) => t).join('')

describe('diffWords', () => {
  it('отмечает удалённую бронь и добавленные слова, обе стороны восстанавливаются', () => {
    const a = 'Старт продаж! Бронь 200 000 ₸, рассрочка до сдачи'
    const b = 'Старт продаж! Рассрочка до сдачи 2027'
    const d = diffWords(a, b)
    expect(side(d, 'add')).toBe(a)
    expect(side(d, 'del')).toBe(b)
    expect(d).toContainEqual(['del', 'Бронь 200 000 ₸, рассрочка'])
    expect(d).toContainEqual(['add', 'Рассрочка'])
    expect(d[d.length - 1]).toEqual(['add', ' 2027'])
    expect(changed(d)).toBe(true)
  })
  it('совпадает с серверным diff_words на тех же примерах', () => {
    expect(diffWords('а  б', 'а б')).toEqual([['eq', 'а б']])
    expect(diffWords('', 'новый текст')).toEqual([['add', 'новый текст']])
    expect(diffWords('старый', '')).toEqual([['del', 'старый']])
    expect(diffWords(null, undefined)).toEqual([])
    expect(changed(diffWords('одно и то же', 'одно и то же'))).toBe(false)
  })
  it('длинный текст сверх предела таблицы — середина целиком', () => {
    expect(diffWords('x a b c y', 'x d e f y', 4)).toEqual([['eq', 'x '], ['del', 'a b c'], ['add', 'd e f'], ['eq', ' y']])
  })
  it('слова и пробелы склеиваются в исходный текст', () => {
    expect(tokens(' a  b\nc ').join('')).toBe(' a  b\nc ')
  })
})

describe('ddmm', () => {
  it('дата копии ДД.ММ', () => {
    expect(ddmm('2026-10-19T08:00:00+00:00')).toBe('19.10')
    expect(ddmm(null)).toBe('')
  })
})
