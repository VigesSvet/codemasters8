import { describe, expect, it } from 'vitest'
import { matches, norm, rank, rankIn, skel } from './search'

describe('быстрый поиск', () => {
  it('сводит регистр, ё, казахские буквы и латиницу', () => {
    expect(norm('ЖК «Шығыс Тауэр»')).toBe('жк шыгыс тауэр')
    expect(norm('Shygys')).toBe('шыгыс')
    expect(norm('Нұр Әлем')).toBe(norm('Нур Алем'))
    expect(norm('Altai')).toBe(norm('Алтай'))
  })
  it('находит транслит и опечатки по скелету', () => {
    expect(matches('Shygys', 'Шығыс Тауэр')).toBe(true)
    expect(matches('Шыгыс', 'Шығыс Тауэр')).toBe(true)
    expect(matches('shygys tower', 'Шығыс Тауэр')).toBe(true)
    expect(matches('Altai Residence', 'Алтай Резиденс')).toBe(true)
    expect(skel('residence')).toBe(skel('резиденс'))
  })
  it('ищет по нескольким полям и требует все слова', () => {
    expect(matches('сатпаева 14', 'Шығыс Тауэр', 'пр. Сатпаева, 14/2')).toBe(true)
    expect(matches('230240077003', 'Шығыс Тауэр', '', '230240077003')).toBe(true)
    expect(matches('шыгыс гараж', 'Шығыс Тауэр')).toBe(false)
    expect(matches('', 'что угодно')).toBe(true)
  })
  it('короткие запросы не ловят случайные совпадения скелета', () => {
    expect(matches('ab', 'Береке Сити')).toBe(false)
  })
  it('ранг: 2 — слова как есть, 1 — только по скелету, 0 — нет', () => {
    expect(rank('Береке', 'Береке Сити')).toBe(2)
    expect(rank('Шыгыс', 'Шығыс Тауэр')).toBe(2)
    expect(rank('Shygys', 'Шығыс Тауэр')).toBe(2)
    expect(rank('бере', 'Береке Сити')).toBe(2)
    expect(rank('Altai Residence', 'Алтай Резиденс')).toBe(1)
    expect(rank('shygys tower', 'Шығыс Тауэр')).toBe(1)
    expect(rank('Береке', 'ул. Кокжал Барака')).toBe(1)
    expect(rank('шыгыс гараж', 'Шығыс Тауэр')).toBe(0)
    expect(rank('', 'что угодно')).toBe(2)
  })
  it('скелет — только в полях fuzzy, как есть — во всех', () => {
    const asyltas = ['ЖК "ASYLTAS"', 'ул. Кокжал Барака, уч. 1']
    expect(rankIn('Береке', asyltas, asyltas.slice(0, 1))).toBe(0)
    expect(rankIn('Кокжал Барака', asyltas, asyltas.slice(0, 1))).toBe(2)
    // слово как есть в адресе + слово по скелету в названии
    expect(rankIn('Altai Residence Казахстан', ['Алтай Резиденс', 'ул. Казахстан, 66'], ['Алтай Резиденс'])).toBe(1)
    expect(rankIn('Протозанова', ['Ульба Ривер', 'ул. Протозанова, 81'], ['Ульба Ривер'])).toBe(2)
    expect(rankIn('Ертис', ['Ульба Ривер', 'ул. Протозанова, 81'], ['Ульба Ривер'])).toBe(0)
  })
})
