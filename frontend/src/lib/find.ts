// Поиск ЖК и объектов открытых данных — одни и те же поля в шапке, на карте, в реестре и в списке открытых данных.
// Нормализация (регистр, ё, казахские буквы, транслит) и ранжирование — в search.ts.
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Complex, Developer, RegistryObject } from '../data/types'
import { rankIn } from './search'

/** «ЖК»/«МЖК» в запросе не мешают: в названиях источников этого слова может не быть */
const bare = (q: string) => q.replace(/(^|[\s«"])(м?жк|m?zhk)(?=[\s«»"]|$)/giu, '$1')

/** ЖК демо-набора: как есть — название, адрес, застройщик, БИН застройщика, район; по скелету — только название и застройщик
 *  (по адресу скелет ловит случайное: «Ертис» ≈ «Протозанова») */
export const rankComplex = (q: string, c: Complex, dev?: Developer) =>
  rankIn(bare(q), [c.name, c.address, dev?.name, dev?.bin, c.district], [c.name, dev?.name])
export const findComplex = (q: string, c: Complex, dev?: Developer) => rankComplex(q, c, dev) > 0

/** Объект открытых данных: как есть — название, адрес, застройщик, БИН, агент; по скелету — название и застройщик */
export const rankObject = (q: string, o: RegistryObject) =>
  rankIn(bare(q), [o.name, o.address, o.developer, o.developerBin, o.agent], [o.name, o.developer])
export const findObject = (q: string, o: RegistryObject) => rankObject(q, o) > 0

/** Отбор для выдачи по списку xs: есть точные совпадения — только они, нет — совпадения по скелету.
 *  Пустой запрос подходит всему. finder(q, rows, (q, x) => rankComplex(q, x.c, x.dev)) → предикат для filter */
export function finder<T>(q: string, xs: readonly T[], rankOf: (q: string, x: T) => number): (x: T) => boolean {
  const r = new Map(xs.map((x) => [x, rankOf(q, x)]))
  const top = Math.max(0, ...r.values())
  return (x) => top > 0 && (r.get(x) ?? rankOf(q, x)) === top
}

/** Номер публикации: демо a7 → AD-007; добавленные вручную (m1a2b3c4) — весь id: AD-M1A2B3C4, с демо не пересекается */
export function adKey(id: string) {
  const n = /^a(\d+)$/i.exec(id)
  return n ? `AD-${n[1].padStart(3, '0')}` : `AD-${id.toUpperCase()}`
}

export type Code = { kind: 'ad'; key: string } | { kind: 'signal'; n: number }

/** Код в запросе: «AD-7», «ад 007» → AD-007; «AD-M1A2B3C4», «ad m1a2» → публикация, добавленная вручную (номер или его начало);
 *  «S-1», «s0001» → сигнал 1; иначе null */
export function codeQuery(q: string): Code | null {
  const s = q.trim()
  const ad = /^(?:ad|ад)\s*-?\s*0*(\d{1,6})$/iu.exec(s)
  if (ad) return { kind: 'ad', key: adKey(`a${ad[1]}`) }
  // ручной номер — через дефис или с цифрой: «ad group» — не номер
  const own = /^(?:ad|ад)(?:\s*-\s*([a-z][a-z\d]*)|\s+([a-z][a-z\d]*\d[a-z\d]*))$/iu.exec(s)
  if (own) return { kind: 'ad', key: adKey(own[1] ?? own[2]) }
  const sig = /^(?:s|с)\s*-?\s*0*(\d{1,6})$/iu.exec(s)
  return sig ? { kind: 'signal', n: Number(sig[1]) } : null
}

/** Публикация под номер из codeQuery: демо-номер — целиком, номер ручной — и по началу (AD-M1A → AD-M1A2B3C4) */
export function adHit(key: string, id: string) {
  const k = adKey(id)
  return k === key || (/^AD-[A-Z]/.test(key) && k.startsWith(key))
}

/** Строка поиска в адресе (?q=): поле — из своего состояния, адрес догоняет. Роутер меняет адрес переходом (startTransition),
 *  и поле, привязанное прямо к адресу, теряло бы буквы при быстром вводе. Смена адреса не нашим вводом («назад», ссылка
 *  из шапки) — берётся из адреса. also — другие параметры в тот же переход (два перехода подряд затёрли бы друг друга) */
export function useSearchText(key = 'q'): [string, (v: string, also?: Record<string, string>) => void] {
  const [sp, setSp] = useSearchParams()
  const url = sp.get(key) ?? ''
  const [text, setText] = useState(url)
  // что мы записали в адрес, а роутер ещё не показал
  const [pending, setPending] = useState<string[]>([])
  const i = pending.lastIndexOf(url)
  if (i >= 0) setPending(pending.slice(i + 1))
  else if (!pending.length && url !== text) setText(url)
  const set = (v: string, also: Record<string, string> = {}) => {
    setText(v)
    setPending((p) => [...p, v])
    setSp((p) => {
      const n = new URLSearchParams(p)
      for (const [k, x] of Object.entries({ ...also, [key]: v })) { if (x) n.set(k, x); else n.delete(k) }
      return n
    }, { replace: true })
  }
  return [text, set]
}
