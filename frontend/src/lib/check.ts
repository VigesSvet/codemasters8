// Публичная проверка ЖК перед покупкой (/check): только факты открытого реестра об основании привлекать деньги дольщиков.
// Ни балла риска, ни «Проверки по рекламе», ни статусов аналитика — это сведения источников на дату сбора, не вывод.
// Поиск — тот же, что в шапке и реестре (find.ts / search.ts): название, адрес, застройщик, БИН, уполномоченная компания.
import type { Complex, Developer, RegistryObject, SourceRecord } from '../data/types'
import { finder, rankObject } from './find'
import { isDemo } from './provenance'

/** Документ основания по способам ст. 7 Закона: гарантия ЕО, разрешение МИО (ЖАО), участие банка второго уровня */
export type CheckDoc = 'guarantee' | 'mio' | 'bvu'
export const CHECK_DOCS: readonly CheckDoc[] = ['guarantee', 'mio', 'bvu']

/** Строка выдачи: объект открытого реестра (key) или ЖК справочника без записи в реестре (complexId) */
export interface CheckEntry {
  key: string
  name: string
  address: string | null
  developer: string | null
  bin: string | null
  agent: string | null
  /** Есть записи открытого реестра — карточка грузится по /registry/{key} */
  registry: boolean
  complexId?: string
}

/** Выдача поиска: объекты реестра и ЖК справочника, которых в реестре нет (по рекламе) — без них житель решит, что
 *  «ЖК не существует». Демо-ЖК прежних версий не показываются: публичный экран — только о реальных объектах */
export function checkEntries(objects: readonly RegistryObject[], complexes: readonly Complex[], developers: readonly Developer[]): CheckEntry[] {
  const reg: CheckEntry[] = objects.map((o) => ({
    key: o.key, name: o.name, address: o.address ?? null, developer: o.developer ?? null, bin: o.developerBin ?? null, agent: o.agent ?? null, registry: true,
  }))
  const keys = new Set(reg.map((e) => e.key))
  const dev = new Map(developers.map((d) => [d.id, d]))
  const extra: CheckEntry[] = complexes
    .filter((c) => !isDemo(c) && !(c.permit?.registryKey && keys.has(c.permit.registryKey)) && !keys.has(c.id.replace(/^r-/, '')))
    .map((c) => {
      const d = dev.get(c.developerId)
      return { key: `cx:${c.id}`, name: c.name, address: c.address || null, developer: d?.name ?? null, bin: d?.bin || null, agent: null, registry: false, complexId: c.id }
    })
  return [...reg, ...extra]
}

const asObject = (e: CheckEntry) => ({ name: e.name, address: e.address, developer: e.developer, developerBin: e.bin, agent: e.agent }) as RegistryObject

/** Поиск по выдаче: точные совпадения (после нормализации регистра, ё, казахских букв, транслита), а нет их — по скелету.
 *  Пустой запрос — пусто: выдача появляется, когда житель что-то ввёл */
export function searchEntries(q: string, xs: readonly CheckEntry[], limit = 8): CheckEntry[] {
  if (!q.trim()) return []
  const fit = finder(q, xs, (s, e) => rankObject(s, asObject(e)))
  return xs.filter(fit).sort((a, b) => Number(b.registry) - Number(a.registry) || a.name.localeCompare(b.name, 'ru')).slice(0, limit)
}

/** Запись реестра, показанная жителю: номер и дата документа, источник, ссылка, когда собрано */
export interface FactRecord {
  number: string | null
  date: string | null
  /** Код источника: homeportal | homeportal_section | ashyq */
  source: string
  sourceName: string
  url: string | null
  collectedAt: string | null
  providerLoadedAt: string | null
  /** Срок строительства по записи (Казреестр): начало и окончание */
  start: string | null
  end: string | null
  section: string | null
}

export interface CheckFacts {
  /** Записи Homeportal о способе привлечения: гарантия ЕО, разрешение МИО, участие банка */
  docs: Record<CheckDoc, FactRecord[]>
  /** Записи открытого набора Казреестра (Ashyq Data) о привлечении денег дольщиков */
  kazreestr: FactRecord[]
  /** Запись отдельного раздела Homeportal (название раздела — как у источника); только факт источника */
  section: FactRecord | null
  /** Дата сведений: последний сбор записей (UTC ISO); null — записей нет */
  asOf: string | null
  /** Дата загрузки набора у поставщика (Ashyq Data) */
  providerLoadedAt: string | null
}

const fact = (r: SourceRecord): FactRecord => ({
  number: r.docNumber ?? null, date: r.docDate ?? null, source: r.source, sourceName: r.sourceName ?? r.source, url: r.sourceUrl ?? null,
  collectedAt: r.collectedAt ?? null, providerLoadedAt: r.providerLoadedAt ?? null,
  start: r.start ?? null, end: r.plannedEnd ?? null, section: r.sourceSection ?? null,
})
const latest = (xs: (string | null | undefined)[]) => xs.filter((x): x is string => !!x).sort().at(-1) ?? null
/** Свежий документ — первым: по дате документа, без даты — в конце */
const byDate = (a: FactRecord, b: FactRecord) => (b.date ?? '').localeCompare(a.date ?? '')

/** Факты открытого реестра по записям объекта. Способ берётся только из поля источника (fundingMethod) — без догадок по номеру */
export function checkFacts(records: readonly SourceRecord[]): CheckFacts {
  const docs: Record<CheckDoc, FactRecord[]> = { guarantee: [], mio: [], bvu: [] }
  const kazreestr: FactRecord[] = []
  let section: FactRecord | null = null
  for (const r of records) {
    if (r.source === 'ashyq') kazreestr.push(fact(r))
    else if (r.source === 'homeportal_section') section ??= fact(r)
    else if (r.fundingMethod && r.fundingMethod in docs) docs[r.fundingMethod as CheckDoc].push(fact(r))
  }
  for (const d of CHECK_DOCS) docs[d].sort(byDate)
  kazreestr.sort(byDate)
  return {
    docs, kazreestr, section,
    asOf: latest(records.map((r) => r.collectedAt)),
    providerLoadedAt: latest(records.map((r) => r.providerLoadedAt)),
  }
}

/** Есть ли хоть одна запись о законном основании (гарантия, разрешение, банк, Казреестр) */
export const anyBasis = (f: CheckFacts) => CHECK_DOCS.some((d) => f.docs[d].length > 0) || f.kazreestr.length > 0

/** Статья закона на «Әділет» на языке интерфейса: казахская версия — /kaz/ вместо /rus/ */
export const adiletUrl = (url: string, lang: 'ru' | 'kk') => (lang === 'kk' ? url.replace('/rus/', '/kaz/') : url)
