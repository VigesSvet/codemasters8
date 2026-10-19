// Индекс спутниковых снимков (Esri Wayback) по точкам ЖК → public/wayback-index.json: вкладка «Спутник» открывается
// без обхода архива. Запуск при работающем API: node scripts/wayback-index.mjs [http://localhost:8000]
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
globalThis.localStorage = { getItem: () => null, setItem: () => {} }
// индекс в этом запуске не читаем — снимки собираются заново
const realFetch = globalThis.fetch
globalThis.fetch = (u, ...r) => (String(u) === '/wayback-index.json' ? Promise.resolve(new Response('{}', { status: 404 })) : realFetch(u, ...r))
const { shotsAt, shotKey } = await import(path.join(here, '../src/lib/wayback.ts'))

const api = process.argv[2] ?? 'http://localhost:8000'
const list = await (await realFetch(`${api}/api/complexes`)).json()
const items = (Array.isArray(list) ? list : list.items ?? []).filter((c) => typeof c.lat === 'number' && typeof c.lng === 'number')
const out = path.join(here, '../public/wayback-index.json')
const prev = fs.existsSync(out) ? JSON.parse(fs.readFileSync(out, 'utf8')).tiles ?? {} : {}
const tiles = { ...prev }
for (const c of items) {
  const key = shotKey(c.lat, c.lng)
  if (tiles[key]?.length) { console.log(`${c.name}: уже есть`); continue }
  const t0 = Date.now()
  try {
    tiles[key] = await shotsAt(c.lat, c.lng)
    console.log(`${c.name}: ${tiles[key].length} снимков, ${Date.now() - t0} мс`)
  } catch (e) {
    console.log(`${c.name}: ошибка — ${e.message}`)
  }
}
fs.writeFileSync(out, JSON.stringify({ generatedAt: new Date().toISOString(), source: 'Esri World Imagery Wayback', tiles }))
console.log(`записано ${Object.keys(tiles).length} точек → ${out}`)
