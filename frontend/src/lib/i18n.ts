// Двуязычный интерфейс RU/KZ (ТЗ §11): словари — lib/i18n/ru.ts (основной, по нему типы ключей) и lib/i18n/kk.ts.
// Без библиотек: язык — маленькое внешнее хранилище (useSyncExternalStore — работает и в StrictMode, и вне StoreProvider),
// выбор — в localStorage (saq.lang), применяется без перезагрузки, <html lang> меняется вместе с ним.
// Нет ключа в казахском словаре — русский текст; данные сервера (тексты публикаций, журнал, адреса) не переводятся.
// Подписи из данных и общих таблиц (роли, стадии, зоны, статусы оценки) переводит tr() по точной русской фразе — сами
// таблицы в lib/*.ts не меняются (на них опираются тесты).
import { useSyncExternalStore } from 'react'
import type { ScreeningCounts } from '../data/types'
import { localBox, roleOf, type User } from './session'
import { kk, kkPhrases } from './i18n/kk'
import { ru, type Key } from './i18n/ru'

export type { Key } from './i18n/ru'
export type Lang = 'ru' | 'kk'
export const LANGS: readonly Lang[] = ['ru', 'kk']
export const LANG_KEY = 'saq.lang'
/** Подписи переключателя «РУС | ҚАЗ» и значение для <html lang> */
export const LANG_LABEL: Record<Lang, string> = { ru: 'РУС', kk: 'ҚАЗ' }
export const LANG_TITLE: Record<Lang, string> = { ru: 'Русский язык', kk: 'Қазақ тілі' }
/** Локаль дат и чисел */
export const LOCALE: Record<Lang, string> = { ru: 'ru-RU', kk: 'kk-KZ' }

type Box = Pick<Storage, 'getItem' | 'setItem'>
type Vars = Record<string, string | number>

const DICT: Record<Lang, Partial<Record<Key, string>>> = { ru, kk }

export const isLang = (x: unknown): x is Lang => x === 'ru' || x === 'kk'

/** Сохранённый язык; нет записи, испорченная запись или недоступный storage — русский */
export function readLang(box: Box | null = localBox()): Lang {
  try {
    const v = box?.getItem(LANG_KEY)
    return isLang(v) ? v : 'ru'
  } catch {
    return 'ru'
  }
}

/** Запомнить язык; false — storage недоступен (выбор живёт до перезагрузки) */
export function writeLang(l: Lang, box: Box | null = localBox()): boolean {
  if (!box) return false
  try {
    box.setItem(LANG_KEY, l)
    return true
  } catch {
    return false
  }
}

/** Текст по ключу: нет перевода (или пустой) — русский, нет и его — сам ключ. {имя} — подстановка из vars */
export function translate(lang: Lang, key: Key, vars?: Vars): string {
  const s = DICT[lang][key] || ru[key] || key
  return vars ? fill(s, vars) : s
}

const fill = (s: string, vars: Vars) => s.replace(/\{(\w+)\}/g, (m, k: string) => (Object.hasOwn(vars, k) ? String(vars[k]) : m))

/** Номер формы по числу. Русский: 1 объект, 2 объекта, 5 объектов. Казахский: после числительного форма одна
 *  (5 нысан) — в словаре одна форма, лишние формы (если есть) не нужны */
export function pluralIndex(lang: Lang, n: number): 0 | 1 | 2 {
  if (lang === 'kk') return 0
  const a = Math.abs(n) % 100, b = a % 10
  return a > 10 && a < 20 ? 2 : b === 1 ? 0 : b >= 2 && b <= 4 ? 1 : 2
}

/** Форма по числу: значение словаря — «объект|объекта|объектов» (у казахского — одна форма); {n} — само число */
export function translatePlural(lang: Lang, key: Key, n: number, vars?: Vars): string {
  const forms = translate(lang, key).split('|')
  const s = forms[Math.min(pluralIndex(lang, n), forms.length - 1)]
  return fill(s, { n, ...vars })
}

/** Перевод русской подписи из данных и общих таблиц (роль, ведомство, стадия, статус оценки); неизвестная — как есть */
export function translatePhrase(lang: Lang, s: string): string {
  if (lang === 'ru' || !s) return s
  return kkPhrases[s] ?? kkPhrases[s.trim()] ?? s
}

// ---------- текущий язык ----------

const listeners = new Set<() => void>()
let current: Lang = readLang()

const applyHtml = (l: Lang) => { if (typeof document !== 'undefined') document.documentElement.lang = l }
applyHtml(current)

export const getLang = (): Lang => current

/** Сменить язык: запомнить, обновить <html lang> и все подписанные экраны — без перезагрузки */
export function setLang(l: Lang, box: Box | null = localBox()): void {
  writeLang(l, box)
  if (l === current) return
  current = l
  applyHtml(l)
  for (const f of listeners) f()
}

/** Подписка на смену языка; язык, выбранный в соседней вкладке, приходит событием storage */
export function subscribeLang(cb: () => void): () => void {
  listeners.add(cb)
  return () => { listeners.delete(cb) }
}
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key !== LANG_KEY && e.key !== null) return
    const l = readLang()
    if (l !== current) { current = l; applyHtml(l); for (const f of listeners) f() }
  })
}

// Месяцы — свои: казахская локаль браузеров даёт «М10» вместо названия месяца
const KK_MONTHS = ['қаңтар', 'ақпан', 'наурыз', 'сәуір', 'мамыр', 'маусым', 'шілде', 'тамыз', 'қыркүйек', 'қазан', 'қараша', 'желтоқсан']
const KK_MONTHS_SHORT = ['қаң.', 'ақп.', 'нау.', 'сәу.', 'мам.', 'мау.', 'шіл.', 'там.', 'қыр.', 'қаз.', 'қар.', 'жел.']

/** Набор функций перевода для языка — то же, что даёт useT(), но без React (для тестов и чистых функций) */
export function i18nFor(lang: Lang) {
  const locale = LOCALE[lang]
  return {
    lang,
    locale,
    t: (key: Key, vars?: Vars) => translate(lang, key, vars),
    tn: (key: Key, n: number, vars?: Vars) => translatePlural(lang, key, n, vars),
    tr: (s: string) => translatePhrase(lang, s),
    /** «19 окт.» / «19 қаз.» */
    dayMonth: (iso: string) => {
      const d = new Date(iso)
      if (Number.isNaN(d.getTime())) return iso
      return lang === 'kk' ? `${d.getDate()} ${KK_MONTHS_SHORT[d.getMonth()]}` : d.toLocaleDateString(locale, { day: 'numeric', month: 'short' })
    },
    /** «19 октября 2026» / «2026 ж. 19 қазан» */
    date: (iso: string) => {
      const d = new Date(iso)
      if (Number.isNaN(d.getTime())) return iso
      return lang === 'kk' ? `${d.getFullYear()} ж. ${d.getDate()} ${KK_MONTHS[d.getMonth()]}` : d.toLocaleDateString(locale, { day: 'numeric', month: 'long', year: 'numeric' }).replace(' г.', '')
    },
    num: (n: number) => n.toLocaleString(locale),
  }
}
export type I18n = ReturnType<typeof i18nFor>

/** Должность и ведомство на языке интерфейса («Аналитик · ДЭР ВКО» → «Талдаушы», «ШҚО бойынша ЭТД», значок «ТА»);
 *  в журнал автором по-прежнему пишется name — по-русски, как в данных */
export function roleIn(i: Pick<I18n, 'lang' | 'tr'>, u: Pick<User, 'role' | 'name'>): { title: string; dept: string; short: string } {
  const r = roleOf(u)
  if (i.lang === 'ru') return r
  return roleOf({ name: i.tr(u.name), role: [i.tr(r.title), i.tr(r.dept)].filter(Boolean).join(' · ') })
}

const cache: Partial<Record<Lang, I18n>> = {}
const forLang = (l: Lang) => (cache[l] ??= i18nFor(l))

/** Текущий язык; экран перерисуется при смене */
export const useLang = (): Lang => useSyncExternalStore(subscribeLang, getLang, () => 'ru')

/** t(ключ), tn(ключ, n) — с формой по числу, tr(русская подпись из данных) — для текущего языка */
export const useT = (): I18n => forLang(useLang())

// ---------- подписи из общих функций lib/*.ts на языке интерфейса ----------

/** Как countsLine (lib/screening): «5 публ. · 2 с признаком приёма денег за 120 дн. · последняя 17 окт.» */
export function countsLineT(t: I18n['t'], c: ScreeningCounts | null | undefined, fmt: (iso: string) => string): string {
  if (!c || !c.ads) return t('scr.counts.none')
  const parts = [t('common.adsN', { n: c.ads })]
  parts.push(c.concerning ? t('scr.counts.concerning', { n: c.concerning, d: c.windowDays }) : t('scr.counts.clean', { d: c.windowDays }))
  if (c.lastAdAt) parts.push(t('scr.counts.last', { date: fmt(c.lastAdAt) }))
  return parts.join(' · ')
}
