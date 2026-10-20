// Фото объекта (сервер: app.gallery): список с обложкой первой, адрес файла и подписи источника — для галереи
// (components/Gallery.tsx) и миниатюр ЖК на остальных экранах
import type { Complex, GalleryPhoto } from '../data/types'
import { fmtDate } from '../components/ui'
import { mediaSrc } from './api'
import { externalUrl } from './platform'

/** Фото объекта, обложка первой; сервер старой версии — только cover */
export function galleryOf(c: Complex): GalleryPhoto[] {
  if (c.gallery) return c.gallery
  return c.cover ? [{ src: c.cover, kind: 'operator', source: 'Обложка карточки', cover: true }] : []
}

export const photoSrc = (p: GalleryPhoto) => mediaSrc(p.src) ?? p.src

/** Ссылка "страница источника": только http(s)(externalUrl) - другой адрес не исполняется как javascript:, ссылки нет */
export const photoPage = (p: GalleryPhoto) => externalUrl(p.url)

/** Адрес обложки ЖК для миниатюр (первое фото объекта); фото нет — undefined */
export const coverSrc = (c: Complex | null | undefined): string | undefined => { const p = c ? galleryOf(c)[0] : undefined; return p ? photoSrc(p) : undefined }

const KIND: Record<GalleryPhoto['kind'], string> = { operator: 'Фото оператора', site: 'Фото-мониторинг', homeportal: 'Homeportal', ad: 'Реклама' }

/** Короткая подпись на фото: откуда оно */
export const photoTag = (p: GalleryPhoto) => (p.kind === 'ad' && p.hypothesis ? 'Реклама · гипотеза' : KIND[p.kind])

/** Полная подпись: источник, дата, кто загрузил, оговорка */
export function photoCaption(p: GalleryPhoto): string {
  const when = p.date ? fmtDate(p.date) : null
  switch (p.kind) {
    case 'operator': return [p.source, when, p.by].filter(Boolean).join(' · ')
    case 'site': return ['Снимок площадки', p.source, when].filter(Boolean).join(' · ')
    case 'homeportal': return 'Homeportal — фото или визуализация из карточки ЖК, не снимок площадки'
    case 'ad': return [`Из рекламы: ${p.source}`, when, p.hypothesis ? 'публикация отнесена к ЖК гипотезой' : 'привязка подтверждена'].filter(Boolean).join(' · ') + ' — может быть визуализация'
  }
}
