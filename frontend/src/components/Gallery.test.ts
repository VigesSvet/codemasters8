import { describe, expect, it } from 'vitest'
import type { Complex, GalleryPhoto } from '../data/types'
import { loadSnapshot, withSnapshotGallery } from '../data/snapshot'
import { galleryOf, photoCaption, photoPage, photoTag } from '../lib/gallery'

const cx = (p: Partial<Complex>) => ({ id: 'x', name: 'x', cover: '', photos: [], ...p }) as Complex

describe('фото объекта', () => {
  it('сервер старой версии — обложка из cover', () => {
    expect(galleryOf(cx({ gallery: undefined, cover: '/img/a.jpg' }))).toEqual([{ src: '/img/a.jpg', kind: 'operator', source: 'Обложка карточки', cover: true }])
    expect(galleryOf(cx({ gallery: undefined, cover: '' }))).toEqual([])
    expect(galleryOf(cx({ gallery: [], cover: '/img/a.jpg' }))).toEqual([])
  })

  it('подпись называет источник; реклама и Homeportal — не снимок площадки', () => {
    const ad: GalleryPhoto = { src: '/api/media/1', kind: 'ad', source: 'Instagram, @x', date: '2026-10-01', hypothesis: true }
    expect(photoTag(ad)).toBe('Реклама · гипотеза')
    expect(photoTag({ ...ad, hypothesis: false })).toBe('Реклама')
    expect(photoCaption(ad)).toContain('гипотезой')
    expect(photoCaption(ad)).toContain('может быть визуализация')
    expect(photoCaption({ src: 's', kind: 'homeportal', source: 'Homeportal' })).toContain('не снимок площадки')
    expect(photoTag({ src: 's', kind: 'operator', source: 'Фото оператора' })).toBe('Фото оператора')
  })

  it('офлайн-набор: фото — файлы копии, обложка — первой', async () => {
    const { complexes } = await loadSnapshot()
    expect(complexes.some((c) => c.gallery!.length > 0)).toBe(true)
    for (const c of complexes) {
      for (const p of c.gallery!) expect(p.src.startsWith('/snapshot-media/') || !p.src.startsWith('/api/media/')).toBe(true)
      expect(Array.isArray(c.gallery)).toBe(true)
      expect(c.gallery!.filter((p) => p.cover).length).toBeLessThanOrEqual(1)
      if (c.gallery!.some((p) => p.cover)) expect(c.gallery![0].cover).toBe(true)
    }
    expect(withSnapshotGallery(cx({ gallery: [{ src: '/api/media/' + 'a'.repeat(32), kind: 'homeportal', source: 'Homeportal' }] }), {}).gallery).toEqual([])
  })

  // адрес страницы приходит из записей открытого реестра: не http(s) - ссылки "страница источника" нет
  it('страница источника - только http(s)', () => {
    const hp = (url?: string | null) => photoPage({ src: 's', kind: 'homeportal', source: 'Homeportal', url })
    expect(hp('https://homeportal.kz/ru/projects/953')).toBe('https://homeportal.kz/ru/projects/953')
    for (const url of ['javascript:alert(1)//homeportal.kz/projects/953', ' JaVaScRiPt:alert(1)', 'data:text/html,1', 'homeportal.kz/ru/projects/953', '', null, undefined]) {
      expect(hp(url)).toBeUndefined()
    }
  })
})
