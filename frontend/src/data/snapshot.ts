// ОФЛАЙН-КОПИЯ СТАРТОВОГО НАБОРА: shared/snapshot/api/*.json — ответы GET /api/… сервера на стартовом наборе (реальные данные
// мониторинга: ЖК открытого реестра и известные по рекламе, застройщики, публичная реклама с OCR и анализом ИИ, объекты из рекламы).
// Открывается, когда сервер недоступен; только для просмотра — изменения сохраняет сервер. Каждый файл — отдельный чанк
// (import.meta.glob без eager), грузится один раз и только в этом режиме. Изображения — /snapshot-media/<sha256>.<ext>: их отдаёт
// vite.config.ts из shared/snapshot/media; видео в копию не входят. Риск и «Проверка по рекламе» — как их посчитал сервер.
import type { Ad, Analyst, Appeal, Complex, Developer, Discovered, MediaRef, RiskResult, SignalSummary, SourceInfo, Stats } from './types'

/** Путь картинок копии на сайте */
export const SNAPSHOT_MEDIA = '/snapshot-media/'
/** Копия как источник данных */
export const SNAPSHOT_NAME = 'Стартовый набор (офлайн-копия)'
/** Действие меняет данные: в офлайн-копии недоступно */
export const READ_ONLY = 'Офлайн-копия только для просмотра — действие доступно с сервером'

export type RiskRow = RiskResult & { complexId: string }

/** Что за копия: когда выгружена, на какую дату данные, сколько записей */
export interface SnapshotMeta {
  /** Время выгрузки набора, UTC ISO */
  exportedAt: string | null
  /** Дата данных, YYYY-MM-DD */
  asOf: string | null
  counts: { complexes: number; developers: number; ads: number; appeals: number; discovered: number; signals: number; images: number }
}

export interface Snapshot {
  complexes: Complex[]
  developers: Developer[]
  ads: Ad[]
  appeals: Appeal[]
  /** Риск ЖК, как его вернул GET /api/risk */
  risk: RiskRow[]
  stats: Stats | null
  signals: SignalSummary[]
  analysts: Analyst[]
  discovered: Discovered[]
  meta: SnapshotMeta
}

/** id файла сервера → имя файла в shared/snapshot/media (api/media.json) */
export type MediaMap = Record<string, string>

const MEDIA_ID = /^\/api\/media\/([0-9a-f]{32})$/
const MEDIA_FILE = /^[0-9a-f]{64}\.(jpg|png|webp|gif)$/

/** Адрес файла в копии: /api/media/{id} → /snapshot-media/<файл>; файла в копии нет (видео, не выгружен) — null.
 *  Прочие адреса (http(s), пути сайта) — как есть */
export function snapshotMediaUrl(u: string | null | undefined, media: MediaMap): string | null {
  if (!u) return null
  const m = MEDIA_ID.exec(u)
  if (!m) return u
  const file = Object.hasOwn(media, m[1]) ? media[m[1]] : null
  return file && MEDIA_FILE.test(file) ? SNAPSHOT_MEDIA + file : null
}

/** Публикация копии: обложка и файлы — на файлы копии; чего в копии нет — убираем (вместо картинки будет значок площадки) */
export function withSnapshotMedia(a: Ad, media: MediaMap): Ad {
  const image = snapshotMediaUrl(a.image, media) ?? undefined
  const files = a.media?.flatMap((m): MediaRef[] => {
    const url = snapshotMediaUrl(m.url || `/api/media/${m.id}`, media)
    return url ? [{ ...m, url }] : []
  })
  return { ...a, image, ...(files ? { media: files } : {}) }
}

/** ЖК копии: фото объекта — на файлы копии; чего в копии нет (фото Homeportal, загруженные оператором) — убираем */
export function withSnapshotGallery(c: Complex, media: MediaMap): Complex {
  if (!c.gallery) return c
  const gallery = c.gallery.flatMap((p) => { const src = snapshotMediaUrl(p.src, media); return src ? [{ ...p, src }] : [] })
  return { ...c, gallery }
}

/** Копия как строка списка источников (страница «Источники» без сервера) */
export function snapshotSource(m: SnapshotMeta): SourceInfo {
  const on = m.asOf ? ` на ${m.asOf.split('-').reverse().join('.')}` : ''
  return {
    code: 'snapshot', name: SNAPSHOT_NAME, records: m.counts.ads, objects: m.counts.complexes, collectedAt: m.exportedAt,
    note: `Реальные данные мониторинга${on}: ЖК открытого реестра и известные по рекламе, застройщики, публичная реклама с OCR и анализом ИИ, объекты из рекламы. Только для просмотра`,
    counts: { complexes: m.counts.complexes, developers: m.counts.developers, ads: m.counts.ads, appeals: m.counts.appeals },
  }
}

// ---------- Загрузка ----------

const ROOT = '../../../shared/snapshot/'
const FILES = import.meta.glob<unknown>(['../../../shared/snapshot/api/*.json', '../../../shared/snapshot/manifest.json'], { import: 'default' })

const file = (name: string): Promise<unknown> => {
  const get = FILES[ROOT + name]
  return get ? get() : Promise.reject(new Error(`Стартовый набор не выгружен: в копии нет «${name}»`))
}
const list = <T>(x: unknown, name: string): T[] => {
  if (!Array.isArray(x)) throw new Error(`Стартовый набор повреждён: «${name}» — не список`)
  return x as T[]
}
const obj = (x: unknown): Record<string, unknown> => (x && typeof x === 'object' && !Array.isArray(x) ? x as Record<string, unknown> : {})
const str = (x: unknown) => (typeof x === 'string' && x ? x : null)

async function read(): Promise<Snapshot> {
  const names = ['complexes', 'developers', 'ads', 'appeals', 'risk', 'stats', 'signals', 'analysts', 'discovered', 'media'] as const
  const [manifest, ...got] = await Promise.all([file('manifest.json'), ...names.map((n) => file(`api/${n}.json`))])
  const raw = Object.fromEntries(names.map((n, i) => [n, got[i]])) as Record<(typeof names)[number], unknown>
  const media = obj(raw.media) as MediaMap
  const complexes = list<Complex>(raw.complexes, 'complexes.json').map((c) => withSnapshotGallery(c, media))
  const developers = list<Developer>(raw.developers, 'developers.json')
  const ads = list<Ad>(raw.ads, 'ads.json').map((a) => withSnapshotMedia(a, media))
  const appeals = list<Appeal>(raw.appeals, 'appeals.json')
  const signals = list<SignalSummary>(raw.signals, 'signals.json')
  const discovered = list<Discovered>(raw.discovered, 'discovered.json')
  const m = obj(manifest)
  const files = obj(m.media).files
  return {
    complexes, developers, ads, appeals, signals, discovered,
    risk: list<RiskRow>(raw.risk, 'risk.json'),
    analysts: list<Analyst>(raw.analysts, 'analysts.json'),
    stats: Object.keys(obj(raw.stats)).length ? raw.stats as Stats : null,
    meta: {
      exportedAt: str(m.exportedAt),
      asOf: str(obj(m.api).asOf),
      counts: {
        complexes: complexes.length, developers: developers.length, ads: ads.length, appeals: appeals.length, discovered: discovered.length,
        signals: signals.length, images: typeof files === 'number' ? files : new Set(Object.values(media)).size,
      },
    },
  }
}

let cache: Promise<Snapshot> | null = null
/** Офлайн-копия целиком; загружается один раз, при ошибке — заново при следующем вызове */
export const loadSnapshot = (): Promise<Snapshot> => (cache ??= read().catch((e: unknown) => { cache = null; throw e }))
