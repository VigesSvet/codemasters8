// Архив спутниковых снимков Esri World Imagery Wayback: выпуски мозаики с 2014 года (бесплатно, без ключа, CORS открыт).
// Для точки ЖК оставляем только выпуски, где снимок этого места действительно менялся (служба tilemap отвечает,
// из какого выпуска взят тайл), и для каждого — дату съёмки из службы метаданных. Снимок — подложка, а не доказательство:
// дата съёмки по данным Esri, разрешение ~0,3–0,6 м.

const CONFIG = 'https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json'
const BASE = 'https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery'
export const WAYBACK_ATTR = 'Снимки: Esri World Imagery Wayback (Maxar, Earthstar Geographics)'
/** Масштаб, на котором проверяем смену снимка: видны отдельные дома и котлован */
export const SAT_ZOOM = 17

export interface Release { id: number; date: string; metadata: string }
/** Снимок места: выпуск, дата выпуска; acquired — дата съёмки (ГГГГ-ММ-ДД), res — разрешение в метрах, sensor — спутник */
export interface SatShot extends Release { acquired?: string | null; res?: number | null; sensor?: string | null }

/** Шаблон тайлов выпуска для MapLibre (WMTS: level/row/col = z/y/x) */
export const tileUrl = (id: number) => `${BASE}/WMTS/1.0.0/default028mm/MapServer/tile/${id}/{z}/{y}/{x}`

let releases: Promise<Release[]> | null = null
/** Все выпуски архива, новые первыми */
export function loadReleases(): Promise<Release[]> {
  releases ??= fetch(CONFIG).then((r) => {
    if (!r.ok) throw new Error(`архив снимков не ответил (HTTP ${r.status})`)
    return r.json()
  }).then((cfg: Record<string, { itemTitle: string; metadataLayerUrl: string }>) =>
    Object.entries(cfg).flatMap(([id, v]) => {
      const d = /(\d{4}-\d\d-\d\d)/.exec(v.itemTitle)?.[1]
      return d ? [{ id: Number(id), date: d, metadata: v.metadataLayerUrl }] : []
    }).sort((a, b) => b.date.localeCompare(a.date)))
  releases.catch(() => { releases = null })
  return releases
}

/** Тайл Web Mercator, в который попадает точка */
export function tileOf(lat: number, lng: number, z: number) {
  const n = 2 ** z
  const x = Math.floor(((lng + 180) / 360) * n)
  const r = (lat * Math.PI) / 180
  const y = Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n)
  return { x, y, z }
}

/** Дата съёмки и разрешение снимка в точке (служба метаданных выпуска; слой = 23 − масштаб) */
async function acquisition(r: Release, lat: number, lng: number, z: number): Promise<Pick<SatShot, 'acquired' | 'res' | 'sensor'>> {
  const q = new URLSearchParams({
    geometry: `${lng},${lat}`, geometryType: 'esriGeometryPoint', inSR: '4326', spatialRel: 'esriSpatialRelIntersects',
    outFields: 'SRC_DATE,SRC_DATE2,SRC_RES,SRC_DESC', returnGeometry: 'false', f: 'json',
  })
  try {
    const res = await fetch(`${r.metadata}/${Math.max(0, Math.min(13, 23 - z))}/query?${q}`)
    const a = (await res.json())?.features?.[0]?.attributes
    if (!a) return {}
    const raw = a.SRC_DATE2 ?? a.SRC_DATE
    // SRC_DATE — число ГГГГММДД, SRC_DATE2 — миллисекунды эпохи
    const acquired = typeof raw === 'number'
      ? (raw > 1e11 ? new Date(raw).toISOString().slice(0, 10) : `${String(raw).slice(0, 4)}-${String(raw).slice(4, 6)}-${String(raw).slice(6, 8)}`)
      : null
    return { acquired, res: typeof a.SRC_RES === 'number' ? a.SRC_RES : null, sensor: a.SRC_DESC ?? null }
  } catch {
    return {}
  }
}

const memo = new Map<string, Promise<SatShot[]>>()
const CACHE = 'saq.wayback.'
const CACHE_DAYS = 7
/** Индекс снимков по точкам ЖК, собранный заранее (scripts/wayback-index.mjs): открывается мгновенно */
const INDEX = '/wayback-index.json'
let index: Promise<Record<string, SatShot[]>> | null = null
const loadIndex = () => (index ??= fetch(INDEX).then((r) => (r.ok ? r.json() : { tiles: {} })).then((j) => j.tiles ?? {}).catch(() => ({})))

/** Разные снимки точки, новые первыми: готовый индекс → кэш браузера → обход архива.
 *  Обход: служба tilemap называет выпуск, из которого взят тайл; все выпуски между ним и запрошенным показывают
 *  тот же снимок — следующий запрос сразу к выпуску старше источника. Запросов — по числу смен снимка (~10) */
export function shotsAt(lat: number, lng: number, z = SAT_ZOOM): Promise<SatShot[]> {
  const t = tileOf(lat, lng, z)
  const key = `${t.z}/${t.y}/${t.x}`
  const hit = memo.get(key)
  if (hit) return hit
  const run = (async () => {
    const pre = (await loadIndex())[key]
    if (pre?.length) return pre
    try {
      const c = JSON.parse(localStorage.getItem(CACHE + key) ?? 'null') as { at: number; shots: SatShot[] } | null
      if (c && Date.now() - c.at < CACHE_DAYS * 86400000) return c.shots
    } catch { /* нет хранилища или испорченная запись — запросим заново */ }
    const all = await loadReleases()
    const found: Release[] = []
    let i = 0
    while (i < all.length) {
      const j = await fetch(`${BASE}/MapServer/tilemap/${all[i].id}/${t.z}/${t.y}/${t.x}`).then((x) => x.json()).catch(() => null) as { data?: number[]; select?: number[] } | null
      if (!j) throw new Error('архив снимков не ответил')
      if (!j.data?.[0]) break // в этом и более старых выпусках снимка такого масштаба нет
      const at = Math.max(i, all.findIndex((r) => r.id === (j.select?.[0] ?? all[i].id)))
      found.push(all[at])
      i = at + 1
    }
    const withDates = await Promise.all(found.map(async (r) => ({ ...r, ...(await acquisition(r, lat, lng, z)) })))
    // один и тот же снимок в соседних выпусках (перекраска мозаики) — оставляем самый свежий выпуск
    const seen = new Set<string>()
    const shots = withDates.filter((s) => { const k = s.acquired ?? `r${s.id}`; if (seen.has(k)) return false; seen.add(k); return true })
    try { localStorage.setItem(CACHE + key, JSON.stringify({ at: Date.now(), shots })) } catch { /* кэш необязателен */ }
    return shots
  })()
  memo.set(key, run)
  run.catch(() => memo.delete(key))
  return run
}

/** Ключ тайла точки — для индекса */
export const shotKey = (lat: number, lng: number, z = SAT_ZOOM) => { const t = tileOf(lat, lng, z); return `${t.z}/${t.y}/${t.x}` }

/** Дата, по которой ставим снимок на ленту времени: съёмка, а без неё — выпуск */
export const shotDate = (s: SatShot) => s.acquired ?? s.date
