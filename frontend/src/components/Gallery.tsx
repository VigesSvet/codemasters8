// Фото объекта (сервер: app.gallery): обложка в выдаче «Новостройки» и галерея в карточке ЖК. Источник у каждого фото
// подписан — Homeportal и реклама дают фото или визуализацию застройщика, а не снимок площадки; привязка рекламы без
// подтверждения — гипотеза. Нет ни одного фото — оператор добавляет своё; выбранное фото можно закрепить обложкой.
// На риск фото не влияют: датированный снимок площадки с активностью — «Добавить снимок» на вкладке «Снимки». Стили — gallery.css
import { useRef, useState } from 'react'
import type { Complex, GalleryPhoto } from '../data/types'
import { errorText, mediaSrc } from '../lib/api'
import { useStore } from '../lib/store'
import { IcCameraAdd, IcCheck, IcClose, IcDownload, IcPicture } from '../ui/icons'
import { SafeImg, fmtDate } from './ui'
import '../styles/gallery.css'

/** Фото объекта, обложка первой; сервер старой версии — только cover */
export function galleryOf(c: Complex): GalleryPhoto[] {
  if (c.gallery) return c.gallery
  return c.cover ? [{ src: c.cover, kind: 'operator', source: 'Обложка карточки', cover: true }] : []
}

export const photoSrc = (p: GalleryPhoto) => mediaSrc(p.src) ?? p.src

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

/** Выбор файла и загрузка фото оператора; children — кнопка */
function useUpload(c: Complex) {
  const { addGalleryPhoto, notify } = useStore()
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const pick = () => input.current?.click()
  const el = (
    <input ref={input} type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden onClick={(e) => e.stopPropagation()}
      onChange={(e) => {
        const f = e.target.files?.[0]
        e.target.value = ''
        if (!f) return
        setBusy(true)
        addGalleryPhoto(c.id, f).then(() => notify('Фото добавлено в карточку ЖК'), (err: unknown) => notify(errorText(err)))
          .finally(() => setBusy(false))
      }} />
  )
  return { pick, el, busy }
}

/** Обложка в выдаче «Новостройки»: фото с подписью источника или пустая рамка с «Добавить фото» */
export function CoverPh({ c, children }: { c: Complex; children?: React.ReactNode }) {
  const { mode } = useStore()
  const p = galleryOf(c)[0]
  const up = useUpload(c)
  const empty = (
    <div className="gl-empty">
      <IcPicture size={28} />
      <span>Фото объекта нет</span>
      {mode === 'api' && (
        <button type="button" className="btn w s" disabled={up.busy} onClick={(e) => { e.stopPropagation(); up.pick() }}>
          <IcCameraAdd size={16} />{up.busy ? 'Загружаем…' : 'Добавить фото'}
        </button>
      )}
    </div>
  )
  return (
    <div className="ph">
      {p ? <SafeImg src={photoSrc(p)} alt="" loading="lazy" fallback={empty} /> : empty}
      {children}
      {p && <span className="gl-src" title={photoCaption(p)}>{photoTag(p)}</span>}
      {up.el}
    </div>
  )
}

/** Галерея карточки ЖК: большое фото, подпись источника, миниатюры; добавить фото, закрепить обложкой, подгрузить с Homeportal */
export function ObjectGallery({ c, tags }: { c: Complex; tags?: React.ReactNode }) {
  const { mode, setCover, removeGalleryPhoto, fetchGallery, notify } = useStore()
  const live = mode === 'api'
  const photos = galleryOf(c)
  const [cur, setCur] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const up = useUpload(c)
  const i = Math.min(cur, Math.max(0, photos.length - 1))
  const p = photos[i]
  const run = (what: string, done: string, f: () => Promise<unknown>) => {
    setBusy(what)
    f().then(() => notify(done), (e: unknown) => notify(errorText(e))).finally(() => setBusy(null))
  }

  return (
    <div className="gallery">
      <div className="ph main-ph">
        {p ? <SafeImg key={p.src} src={photoSrc(p)} alt="" fallback={<div className="gl-empty"><IcPicture size={32} /><span>Фото не загрузилось</span></div>} />
          : (
            <div className="gl-empty big">
              <IcPicture size={40} />
              <span>Фото объекта нет{live ? (c.homeportal ? ' — подгрузите фото из карточки Homeportal или добавьте своё' : ' — добавьте фото дома') : ''}</span>
              {live && <button type="button" className="btn y s" disabled={up.busy} onClick={up.pick}><IcCameraAdd size={16} />{up.busy ? 'Загружаем…' : 'Добавить фото'}</button>}
            </div>
          )}
        <div className="tl">{tags}</div>
        {p && <span className="gl-src">{photoTag(p)}{p.cover && ' · обложка'}</span>}
      </div>
      {p && <p className="gl-cap t-sm c2">{photoCaption(p)}{p.note && <> · {p.note}</>}{p.url && <> · <a className="link" href={p.url} target="_blank" rel="noopener noreferrer">страница источника</a></>}</p>}
      {photos.length > 1 && (
        <div className="thumbs gl-thumbs">
          {photos.map((x, n) => (
            <button key={x.src} type="button" className={n === i ? 'on' : ''} onClick={() => setCur(n)} title={photoCaption(x)}>
              <SafeImg src={photoSrc(x)} alt="" loading="lazy" fallback={<span className="noimg"><IcPicture size={18} /></span>} />
              {x.cover && <span className="gl-pin"><IcCheck size={12} strokeWidth={3} /></span>}
            </button>
          ))}
        </div>
      )}
      {live && (
        <div className="gl-acts">
          {p && <button type="button" className="btn g s" disabled={up.busy} onClick={up.pick}><IcCameraAdd size={16} />{up.busy ? 'Загружаем…' : 'Добавить фото'}</button>}
          {p && !p.cover && <button type="button" className="btn g s" disabled={!!busy} onClick={() => run('cover', 'Обложка карточки закреплена', () => setCover(c.id, p.src))}><IcCheck size={16} />Сделать обложкой</button>}
          {p?.cover && c.cover && <button type="button" className="btn g s" disabled={!!busy} onClick={() => run('auto', 'Обложка — снова автоматическая', () => setCover(c.id, ''))}>Обложка автоматически</button>}
          {c.homeportal && <button type="button" className="btn g s" disabled={!!busy} onClick={() => run('fetch', 'Фото с Homeportal обновлены', () => fetchGallery(c.id))}><IcDownload size={16} />{busy === 'fetch' ? 'Подгружаем…' : 'Фото с Homeportal'}</button>}
          {p?.kind === 'operator' && p.mediaId && <button type="button" className="btn g s" disabled={!!busy} onClick={() => { if (window.confirm('Удалить это фото из карточки ЖК?')) run('rm', 'Фото удалено', () => removeGalleryPhoto(c.id, p.mediaId!)) }}><IcClose size={16} />Удалить фото</button>}
        </div>
      )}
      {up.el}
    </div>
  )
}
