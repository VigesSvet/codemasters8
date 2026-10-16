// Быстрый поиск: регистр, ё, казахские буквы и латиница сводятся к одному виду,
// чтобы «Shygys», «Шыгыс» и «Шығыс» находили один и тот же ЖК.

const LETTERS: Record<string, string> = {
  'ё': 'е', 'й': 'и', 'ә': 'а', 'ғ': 'г', 'қ': 'к', 'ң': 'н', 'ө': 'о', 'ұ': 'у', 'ү': 'у', 'һ': 'х', 'і': 'и',
}
// латиница → кириллица: сначала сочетания, потом одиночные буквы
const LATIN: [RegExp, string][] = [
  [/shch|sch/g, 'щ'], [/zh/g, 'ж'], [/kh/g, 'х'], [/ch/g, 'ч'], [/sh/g, 'ш'], [/ts/g, 'ц'],
  [/yu/g, 'ю'], [/ya/g, 'я'], [/yo|ye/g, 'е'], [/c(?=[eiy])/g, 'с'], [/ph/g, 'ф'],
]
const ONE: Record<string, string> = {
  a: 'а', b: 'б', c: 'к', d: 'д', e: 'е', f: 'ф', g: 'г', h: 'х', i: 'и', j: 'ж', k: 'к', l: 'л', m: 'м', n: 'н', o: 'о',
  p: 'п', q: 'к', r: 'р', s: 'с', t: 'т', u: 'у', v: 'в', w: 'у', x: 'кс', y: 'ы', z: 'з',
}

/** «ЖК «Шығыс Тауэр»» и «shygys tower» → «шыгыс тауэр» */
export function norm(s: string | null | undefined): string {
  let t = (s ?? '').toLowerCase().replace(/[ёйәғқңөұүһі]/g, (ch) => LETTERS[ch] ?? ch)
  for (const [re, to] of LATIN) t = t.replace(re, to)
  t = t.replace(/[a-z]/g, (ch) => ONE[ch] ?? ch)
  return t.replace(/[^\p{L}\d]+/gu, ' ').trim()
}

/** Согласный скелет для опечаток и транслита: «residence» ≈ «резиденс», «shygys» ≈ «шығыс» */
export function skel(s: string | null | undefined): string {
  return norm(s).replace(/[аеиоуыэюяьъ\s]/g, '').replace(/[зц]/g, 'с')
}

type Field = string | null | undefined

/** Насколько запрос подходит полям: 2 — каждое слово запроса есть в полях как есть (после norm); 1 — остальные слова нашлись
 *  только по согласному скелету (от трёх согласных) или весь запрос — по скелету (короткие слова транслита: «shygys tower»
 *  ≈ «Шығыс Тауэр»); 0 — не подходит. Скелет ловит и случайное («Береке» ≈ «Кокжал Барака»), поэтому ищется только
 *  в полях fuzzy (по умолчанию — во всех), а выдача берёт скелетные совпадения, только когда точных нет (find.ts: finder) */
export function rankIn(query: string, fields: Field[], fuzzy: Field[] = fields): 0 | 1 | 2 {
  const words = norm(query).split(' ').filter(Boolean)
  const text = fields.map(norm).join(' ')
  const miss = words.filter((w) => !text.includes(w))
  if (!miss.length) return 2
  const sk = fuzzy.map(skel).join(' ')
  if (miss.every((w) => skel(w).length >= 3 && sk.includes(skel(w)))) return 1
  const whole = skel(query)
  return whole.length >= 4 && sk.includes(whole) ? 1 : 0
}

/** rankIn со скелетом по всем полям */
export const rank = (query: string, ...fields: Field[]) => rankIn(query, fields)

/** Каждое слово запроса нашлось хотя бы в одном поле — как есть или по скелету (rank > 0); пустой запрос подходит всему */
export const matches = (query: string, ...fields: Field[]) => rank(query, ...fields) > 0
