// ОТКРЫТЫЕ ДАННЫЕ БЕЗ БЭКЕНДА: копия shared/registry_ust_kamenogorsk.json (его пишет python -m app.importer) — те же объекты
// и записи, что GET /api/registry после загрузки. Грузится отдельным чанком и только в офлайн-копии. Риска, зон и сигналов нет (T-31).
import { finder, rankObject } from '../lib/find'
import type { RegistryCard, RegistryList, RegistryObject, SourceInfo, SourceRecord } from './types'

export const OFFLINE_FILE = 'shared/registry_ust_kamenogorsk.json'

interface OfflineFile {
  meta: { builtAt?: string | null; builtFrom?: string | null }
  sources: { code: string; name: string; url?: string | null; note?: string | null }[]
  records: SourceRecord[]
  objects: RegistryObject[]
  withoutCoords: RegistryObject[]
}

let file: Promise<OfflineFile> | null = null
const load = () => (file ??= import('../../../shared/registry_ust_kamenogorsk.json')
  .then((m) => m.default as unknown as OfflineFile)
  .catch((e: unknown) => { file = null; throw e }))

const ORDER = ['homeportal', 'homeportal_section', 'ashyq']
const time = (x: string) => new Date(x).getTime() || 0
const latest = (xs: (string | null | undefined)[]) => xs.filter((x): x is string => !!x).sort((a, b) => time(b) - time(a))[0] ?? null

/** Источники по встроенной копии: записей, объектов, последний сбор */
export async function offlineSources(): Promise<SourceInfo[]> {
  const f = await load()
  return f.sources.map((s) => {
    const rs = f.records.filter((r) => r.source === s.code)
    return { ...s, records: rs.length, objects: new Set(rs.map((r) => r.key)).size, collectedAt: latest(rs.map((r) => r.collectedAt)), providerLoadedAt: latest(rs.map((r) => r.providerLoadedAt)) }
  }).filter((s) => s.records > 0)
}

export async function offlineRegistry(q?: string): Promise<RegistryList> {
  const f = await load()
  // поиск — тот же, что в шапке и в списке открытых данных (lib/find): транслит, без «ЖК/МЖК»; есть точные совпадения — только они
  const m = finder(q ?? '', [...f.objects, ...f.withoutCoords], rankObject)
  return { objects: f.objects.filter(m), withoutCoords: f.withoutCoords.filter(m) }
}

/** Объект и его записи — в порядке API: по источнику, затем по строке файла; null — такого ключа нет */
export async function offlineObject(key: string): Promise<RegistryCard | null> {
  const f = await load()
  const object = [...f.objects, ...f.withoutCoords].find((o) => o.key === key)
  if (!object) return null
  const rank = (r: SourceRecord) => (ORDER.includes(r.source) ? ORDER.indexOf(r.source) : 9)
  const records = f.records.filter((r) => r.key === key).sort((a, b) => rank(a) - rank(b) || (a.row ?? 0) - (b.row ?? 0))
  return { object, records }
}
