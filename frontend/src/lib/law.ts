// Нормативная база модели риска — shared/law.json (общий с бэкендом, backend/app/law.py).
// Закон РК от 7 апреля 2016 года № 486-V «О долевом участии в жилищном строительстве», ст. 7, 8, 18 (ориентиры ТЗ) и 23
// (реклама): excerpts — дословный текст из ИПС «Әділет» (адрес и редакция — в act), summary — пересказ, не текст закона.
// Ссылки фактора — поле law модели риска (shared/risk_model.json), документов основания — refs из law.json.
// Ссылки справочные: оценка — не юридическое заключение, «нет разрешения МИО» само по себе не «незаконно».
import lawJson from '../../../shared/law.json'
import { factorDef } from './risk'

/** Ссылка на норму: статья и пункты; без пунктов — вся статья */
export interface LawRef { article: string; parts: string[] }
export interface LawRefWhy extends LawRef { why: string }
export interface LawExcerpt { part: string; text: string; prompt?: boolean }
export interface LawArticle { num: string; title: string; url: string; excerpts: LawExcerpt[]; footnote?: string; summary: string }
export interface Law {
  act: { title: string; short: string; number: string; date: string; heading: string; url: string; source: string; sourceApi?: string; redaction: string; fetchedAt: string }
  note: string
  nuance: string
  articles: LawArticle[]
  refs: Record<string, LawRefWhy[]>
}

export const law = lawJson as Law
const ARTICLES: Record<string, LawArticle> = Object.fromEntries(law.articles.map((a) => [a.num, a]))

export const lawArticle = (num: string): LawArticle | undefined => ARTICLES[num]

/** Ссылки на нормы по коду: фактор (R1–R6) — поле law модели риска, документ основания (guarantee, mio, bvu) — refs law.json */
export function lawRefs(code: string): LawRef[] {
  const f = factorDef(code)
  return f ? (f.law ?? []) : (law.refs[code] ?? []).map(({ article, parts }) => ({ article, parts }))
}

/** «ст. 7 п. 1, 2»; без пунктов — «ст. 23» */
export const refText = (r: LawRef) => `ст. ${r.article}${r.parts.length ? ` п. ${r.parts.join(', ')}` : ''}`

/** «Основание: ст. 7 п. 1, 2; ст. 8 п. 6 Закона РК «О долевом участии…»»; '' — норма к фактору не относится (как basis_text в law.py) */
export function basisText(code: string): string {
  const rs = lawRefs(code)
  return rs.length ? `Основание: ${rs.map(refText).join('; ')} ${law.act.short}` : ''
}

/** Почему фактор или документ связан с нормой (refs law.json): [ссылка, пояснение] */
export const refWhy = (code: string): LawRefWhy[] => law.refs[code] ?? []

/** Выдержки статьи для ссылки: пункты ссылки, а если их нет в выдержках или ссылка на всю статью — все выдержки */
export function excerptsFor(r: LawRef): LawExcerpt[] {
  const a = ARTICLES[r.article]
  if (!a) return []
  const picked = a.excerpts.filter((e) => r.parts.includes(e.part))
  return picked.length ? picked : a.excerpts
}

/** Коды факторов модели, которые ссылаются на статью (для блока «Нормативная база») */
export const factorsOf = (num: string, codes: string[]): string[] => codes.filter((c) => lawRefs(c).some((r) => r.article === num))
