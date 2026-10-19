// Офлайн-копия стартового набора: адреса картинок и загрузка настоящих файлов shared/snapshot (api/*.json, manifest.json, media/)
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { Ad } from './types'
import { SNAPSHOT_MEDIA, loadSnapshot, snapshotMediaUrl, snapshotSource, withSnapshotMedia, type MediaMap } from './snapshot'

const shared = (name: string) => fileURLToPath(new URL(`../../../shared/${name}`, import.meta.url))
const read = <T>(name: string): T => JSON.parse(readFileSync(shared(name), 'utf-8')) as T

const ID = '0123456789abcdef0123456789abcdef'
const FILE = `${'a'.repeat(64)}.jpg`
const MEDIA: MediaMap = { [ID]: FILE }
const ad = (patch: Partial<Ad> = {}): Ad => ({
  id: 'x', platform: 'instagram', account: '@a', url: '', date: '2026-10-18T12:00', text: 't', matchConfidence: 0, source: 'parser', status: 'new', ...patch,
})

describe('адреса картинок офлайн-копии', () => {
  it('файл сервера из копии → /snapshot-media/<файл>', () => {
    expect(snapshotMediaUrl(`/api/media/${ID}`, MEDIA)).toBe(`/snapshot-media/${FILE}`)
  })
  it('файла в копии нет (видео, не выгружен) — null', () => {
    expect(snapshotMediaUrl(`/api/media/${'f'.repeat(32)}`, MEDIA)).toBeNull()
    expect(snapshotMediaUrl('', MEDIA)).toBeNull()
    expect(snapshotMediaUrl(null, MEDIA)).toBeNull()
    expect(snapshotMediaUrl(undefined, MEDIA)).toBeNull()
  })
  it('имя файла в карте — только хэш и расширение изображения: иначе null', () => {
    for (const bad of ['../secret.jpg', 'a.jpg', `${'a'.repeat(64)}.mp4`, `x/${'a'.repeat(64)}.jpg`]) {
      expect(snapshotMediaUrl(`/api/media/${ID}`, { [ID]: bad })).toBeNull()
    }
  })
  it('прочие адреса — как есть', () => {
    expect(snapshotMediaUrl('https://example.kz/a.jpg', MEDIA)).toBe('https://example.kz/a.jpg')
    expect(snapshotMediaUrl('/img/site-b.jpg', MEDIA)).toBe('/img/site-b.jpg')
  })

  it('публикация: обложка и файлы — на копию, отсутствующие убраны', () => {
    const unknown = 'e'.repeat(32)
    const got = withSnapshotMedia(ad({
      image: `/api/media/${ID}`,
      media: [
        { id: ID, kind: 'image', mime: 'image/jpeg', role: 'cover', url: `/api/media/${ID}` },
        { id: unknown, kind: 'video', mime: 'video/mp4', role: 'attachment', url: `/api/media/${unknown}` },
        { id: ID, kind: 'image', url: '' },
      ],
    }), MEDIA)
    expect(got.image).toBe(`${SNAPSHOT_MEDIA}${FILE}`)
    expect(got.media).toEqual([
      { id: ID, kind: 'image', mime: 'image/jpeg', role: 'cover', url: `/snapshot-media/${FILE}` },
      { id: ID, kind: 'image', url: `/snapshot-media/${FILE}` },
    ])
  })
  it('обложки нет в копии — без картинки; без файлов — поле media не появляется', () => {
    const got = withSnapshotMedia(ad({ image: `/api/media/${'e'.repeat(32)}` }), MEDIA)
    expect(got.image).toBeUndefined()
    expect('media' in got).toBe(false)
  })
})

describe('строка источника «Стартовый набор (офлайн-копия)»', () => {
  it('счётчики и дата копии', () => {
    const s = snapshotSource({
      exportedAt: '2026-10-18T22:35:41+00:00', asOf: '2026-10-19',
      counts: { complexes: 23, developers: 21, ads: 230, appeals: 0, discovered: 15, signals: 2, images: 347 },
    })
    expect(s).toMatchObject({ code: 'snapshot', records: 230, objects: 23, collectedAt: '2026-10-18T22:35:41+00:00', counts: { complexes: 23, developers: 21, ads: 230, appeals: 0 } })
    expect(s.note).toContain('на 19.10.2026')
    expect(s.note).toContain('Только для просмотра')
  })
})

describe('загрузка настоящей копии shared/snapshot', () => {
  interface Manifest { exportedAt: string; api: { asOf: string }; tables: Record<string, { rows: number }> }
  const manifest = read<Manifest>('snapshot/manifest.json')
  const rows = (t: string) => manifest.tables[t].rows

  it('один и тот же промис на повторный вызов', () => {
    expect(loadSnapshot()).toBe(loadSnapshot())
  })

  it('все списки — как в выгрузке (сейчас 23 ЖК, 21 застройщик, 230 публикаций, 15 объектов из рекламы)', async () => {
    const s = await loadSnapshot()
    expect(s.complexes.length).toBeGreaterThan(0)
    expect(s.complexes).toHaveLength(rows('complexes'))
    expect(s.developers).toHaveLength(rows('developers'))
    expect(s.ads).toHaveLength(rows('ads'))
    expect(s.appeals).toHaveLength(rows('appeals'))
    expect(s.discovered).toHaveLength(rows('discovered_objects'))
    expect(s.signals).toHaveLength(rows('signals'))
    expect(s.analysts).toHaveLength(rows('analysts'))
    expect(s.meta).toMatchObject({
      exportedAt: manifest.exportedAt, asOf: manifest.api.asOf,
      counts: { complexes: rows('complexes'), ads: rows('ads'), discovered: rows('discovered_objects') },
    })
    // ЖК публикаций, сигналов и решённых объектов из рекламы — в списке ЖК копии
    const ids = new Set(s.complexes.map((c) => c.id))
    for (const a of s.ads) if (a.complexId) expect(ids).toContain(a.complexId)
    for (const x of s.signals) expect(ids).toContain(x.complexId)
    expect(s.developers.map((d) => d.id)).toEqual(expect.arrayContaining([...new Set(s.complexes.map((c) => c.developerId))]))
  })

  it('у каждой публикации — анализ Claude; «Проверка по рекламе» и сводка — как посчитал сервер', async () => {
    const s = await loadSnapshot()
    for (const a of s.ads) expect(a.llm, a.id).toBeTruthy()
    for (const c of s.complexes) expect(c.screening?.status, c.id).toBeTruthy()
    expect(s.stats?.screening?.disclaimer).toContain('не вывод о нарушении')
  })

  it('риск — по строке на каждый ЖК, ответ /api/risk', async () => {
    const s = await loadSnapshot()
    expect(s.risk.map((r) => r.complexId).sort()).toEqual(s.complexes.map((c) => c.id).sort())
    for (const r of s.risk) expect(r.factors.length).toBeGreaterThan(0)
  })

  it('картинки — только из shared/snapshot/media, и каждый файл на месте', async () => {
    const s = await loadSnapshot()
    const urls = s.ads.flatMap((a) => [a.image, ...(a.media ?? []).map((m) => m.url)]).filter((u): u is string => !!u)
    expect(urls.length).toBeGreaterThan(0)
    for (const u of urls) {
      expect(u).toMatch(/^\/snapshot-media\/[0-9a-f]{64}\.(jpg|png|webp|gif)$/)
      expect(existsSync(shared(`snapshot/media/${u.slice(SNAPSHOT_MEDIA.length)}`)), u).toBe(true)
    }
    // ни одного адреса сервера: без API они бы не открылись
    expect(JSON.stringify(s.ads)).not.toContain('/api/media/')
  })
})
