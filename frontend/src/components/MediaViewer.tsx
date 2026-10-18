// Просмотр файлов публикации на весь экран: скриншот или фото — с масштабом (кнопки, клавиши, колесо, двойной щелчок,
// щипок) и перетаскиванием, видео — со своими кнопками. Листание, миниатюры, «Открыть оригинал», «Скачать», текст OCR рядом
// с картинкой для сверки. Расчёты масштаба и состав галереи — lib/viewer; адреса файлов приходят оттуда же (adGallery)
import { useEffect, useId, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { isLocalPreview } from '../lib/media'
import { platformName } from '../lib/platform'
import {
  ZOOM_STEP, clampPan, clampScale, counterText, downloadName, fitScale, isZoomed, opensInTab, percent, pinchStep, stepIndex,
  swipeDir, toggleZoom, viewerError, wheelFactor, zoomAt, zoomLimits, type Point, type Size, type View, type ViewerItem, type ViewerMeta,
} from '../lib/viewer'
import { IcChevronLeft, IcChevronRight, IcClose, IcDoc, IcDownload, IcExternal, IcMinus, IcPlay, IcPlus, IcTriangleExclamation } from '../ui/icons'
import { Highlight, fmtDT } from './ui'

type Props = {
  items: ViewerItem[]
  /** С какого файла открыть (номер в items) */
  start?: number
  /** Название окна для экранного диктора: «Файлы публикации AD-007» */
  title: string
  /** Основа имени скачиваемого файла — номер публикации: AD-007 → AD-007-1.jpg */
  fileBase: string
  meta?: ViewerMeta
  /** Текст на изображении (OCR) — панель рядом с картинкой для сверки */
  ocr?: string | null
  onClose: () => void
}

/** Загрузка файла src: ok — загрузился (w, h — исходный размер изображения; у видео 0), иначе ошибка */
type Load = { src: string; ok: boolean; w: number; h: number }
/** Масштаб и сдвиг файла src; fit — «по размеру экрана»: пересчитывается при изменении окна */
type Zoom = View & { src: string; fit: boolean }
/** Жест на изображении: start — где нажали, moved — сдвинули дальше порога, multi — было два пальца, touch — палец или перо */
type Gesture = { start: Point; moved: boolean; multi: boolean; touch: boolean }
type Geo = { pic: Size; box: Size; fit: number }

/** Поле вокруг изображения «по размеру экрана», px */
const PAD = 16
const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
/** Двойное касание: не дольше 320 мс между касаниями и не дальше 30 px */
const TAP_MS = 320
const TAP_PX = 30

/** Кнопка панели: off — сейчас не действует (aria-disabled — фокус с неё не пропадает, в отличие от disabled) */
function Btn({ label, title, off, onClick, children }: { label: string; title: string; off?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" className="mv-btn" aria-label={label} title={title} aria-disabled={off || undefined} onClick={() => { if (!off) onClick() }}>
      {children}
    </button>
  )
}

export function MediaViewer({ items, start = 0, title, fileBase, meta, ocr, onClose }: Props) {
  const n = items.length
  const [index, setIndex] = useState(() => Math.min(Math.max(0, start), Math.max(0, n - 1)))
  const [load, setLoad] = useState<Load | null>(null)
  const [zoom, setZoom] = useState<Zoom | null>(null)
  const [box, setBox] = useState<Size>({ w: 0, h: 0 })
  const [ocrOpen, setOcrOpen] = useState(false)
  // миниатюры, которые не загрузились: вместо пустой плитки — значок
  const [broken, setBroken] = useState<string[]>([])
  const panelId = useId()
  const root = useRef<HTMLDivElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const strip = useRef<HTMLDivElement>(null)
  const video = useRef<HTMLVideoElement>(null)
  const ocrBtn = useRef<HTMLButtonElement>(null)
  const ptrs = useRef(new Map<number, Point>())
  const gesture = useRef<Gesture | null>(null)
  const lastTap = useRef<{ t: number; p: Point } | null>(null)
  const lastPointer = useRef('mouse')
  const wheel = useRef<((e: WheelEvent) => void) | null>(null)

  // список мог укоротиться, пока окно открыто (лента перечитана) — номер не выходит за него
  const i = Math.min(index, Math.max(0, n - 1))
  const item: ViewerItem | undefined = items[i]
  const src = item?.src ?? ''
  const isImage = item?.kind === 'image'
  const cur = load?.src === src ? load : null
  const status: 'loading' | 'ready' | 'error' = !cur ? 'loading' : cur.ok ? 'ready' : 'error'
  const pic: Size | null = isImage && cur?.ok && cur.w > 0 && cur.h > 0 ? { w: cur.w, h: cur.h } : null
  const geo: Geo | null = pic && box.w > 0 && box.h > 0 ? { pic, box, fit: fitScale(pic, box, PAD) } : null
  const fit = geo?.fit ?? 1
  /** Вид файла: свой масштаб и сдвиг — в пределах текущего окна; иначе «по размеру экрана» */
  const derive = (zm: Zoom | null): View => (!geo || !zm || zm.src !== src || zm.fit
    ? { s: fit, x: 0, y: 0 }
    : clampPan({ s: clampScale(zm.s, fit), x: zm.x, y: zm.y }, geo.pic, geo.box))
  const view = derive(zoom)
  const zoomed = isZoomed(view.s, fit)
  const { max } = zoomLimits(fit)

  /** Изменить масштаб или сдвиг: f получает текущий вид; вернулись к «по размеру» — дальше он следует за размером окна */
  const apply = (f: (v: View, g: Geo) => View) => {
    if (!geo) return
    setZoom((zm) => {
      const v = f(derive(zm), geo)
      return { ...v, src, fit: !isZoomed(v.s, geo.fit) }
    })
  }
  const zoomBy = (k: number) => apply((v, g) => zoomAt(v, v.s * k, { x: 0, y: 0 }, g.pic, g.box, g.fit))
  const actual = () => apply((v, g) => zoomAt(v, 1, { x: 0, y: 0 }, g.pic, g.box, g.fit))
  const toFit = () => setZoom(null)
  const show = (k: number) => {
    ptrs.current.clear()
    gesture.current = null
    lastTap.current = null
    setIndex(k)
  }
  const go = (d: number) => { if (n > 1) show(stepIndex(i, d, n)) }
  /** Точка экрана — относительно центра окна просмотра */
  const toStage = (x: number, y: number): Point => {
    const r = stage.current?.getBoundingClientRect()
    return r ? { x: x - r.left - r.width / 2, y: y - r.top - r.height / 2 } : { x: 0, y: 0 }
  }

  // открыто: фокус — в окно, страница под ним не прокручивается, видео на странице — на паузе (звук не накладывается);
  // закрыто — прокрутка и фокус возвращаются туда, откуда открыли
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const body = document.body
    const was = { overflow: body.style.overflow, padding: body.style.paddingRight }
    const bar = window.innerWidth - document.documentElement.clientWidth
    body.style.overflow = 'hidden'
    // полоса прокрутки исчезла — страница не дёргается вбок
    if (bar > 0) body.style.paddingRight = `${bar}px`
    const own = root.current
    document.querySelectorAll('video').forEach((v) => { if (!own?.contains(v)) v.pause() })
    own?.focus({ preventScroll: true })
    return () => {
      body.style.overflow = was.overflow
      body.style.paddingRight = was.padding
      if (opener?.isConnected) opener.focus({ preventScroll: true })
    }
  }, [])

  // размер окна просмотра: от него «по размеру экрана» и пределы сдвига (поворот телефона, панель OCR)
  useEffect(() => {
    const el = stage.current
    if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // колесо — масштаб вокруг курсора. Слушатель не пассивный: иначе браузер масштабирует всю страницу (щипок на тачпаде)
  const onWheel = (e: WheelEvent) => {
    if (!geo) return
    e.preventDefault()
    const p = toStage(e.clientX, e.clientY)
    const k = wheelFactor(e.deltaY, e.deltaMode, e.ctrlKey)
    apply((v, g) => zoomAt(v, v.s * k, p, g.pic, g.box, g.fit))
  }
  useEffect(() => { wheel.current = onWheel })
  useEffect(() => {
    const el = stage.current
    if (!el) return
    const on = (e: WheelEvent) => wheel.current?.(e)
    el.addEventListener('wheel', on, { passive: false })
    return () => el.removeEventListener('wheel', on)
  }, [])

  // видео при переходе к другому файлу и при закрытии — на паузу
  useEffect(() => {
    const v = video.current
    return () => v?.pause()
  }, [src])

  // текущая миниатюра — в середине полосы
  useEffect(() => {
    const el = strip.current
    const th = el?.children[i]
    if (!el || !(th instanceof HTMLElement)) return
    const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollTo({ left: Math.max(0, th.offsetLeft - (el.clientWidth - th.offsetWidth) / 2), behavior: calm ? 'auto' : 'smooth' })
  }, [i])

  /** Tab не уходит из окна: с последнего элемента — на первый, с первого (Shift+Tab) — на последний */
  const trap = (e: KeyboardEvent<HTMLDivElement>) => {
    const el = root.current
    if (!el) return
    const list = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((x) => x.getClientRects().length > 0)
    const first = list[0]
    const last = list[list.length - 1]
    const at = document.activeElement
    if (!first || !last) { e.preventDefault(); el.focus(); return }
    const out = !el.contains(at)
    if (e.shiftKey ? out || at === first || at === el : out || at === last) {
      e.preventDefault()
      ;(e.shiftKey ? last : first).focus()
    }
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // клавиши окна — только ему: «/» быстрого поиска и другие сочетания страницы под окном не срабатывают
    e.stopPropagation()
    if (e.key === 'Escape') { e.preventDefault(); onClose(); return }
    if (e.key === 'Tab') { trap(e); return }
    if (e.altKey || e.ctrlKey || e.metaKey) return
    // стрелки у видео (перемотка) и в тексте OCR (прокрутка) — им самим
    if (e.target instanceof Element && e.target.closest('video, .mv-ocr')) return
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); go(e.key === 'ArrowLeft' ? -1 : 1); return }
    if (!geo) return
    if (e.key === '+' || e.key === '=' || e.code === 'NumpadAdd') zoomBy(ZOOM_STEP)
    else if (e.key === '-' || e.key === '_' || e.code === 'NumpadSubtract') zoomBy(1 / ZOOM_STEP)
    else if (e.key === '0' || e.code === 'Numpad0') toFit()
    else return
    e.preventDefault()
  }

  // Жесты на изображении — события указателя: мышь, палец, перо. Один указатель — сдвиг увеличенного изображения
  // (на «по размеру» палец листает), два пальца — щипок
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    lastPointer.current = e.pointerType
    if (!geo || (e.pointerType === 'mouse' && e.button !== 0)) return
    if (e.target instanceof Element && e.target.closest('button, a')) return
    e.currentTarget.setPointerCapture(e.pointerId)
    const p = { x: e.clientX, y: e.clientY }
    ptrs.current.set(e.pointerId, p)
    if (ptrs.current.size === 1) gesture.current = { start: p, moved: false, multi: false, touch: e.pointerType !== 'mouse' }
    else if (gesture.current) gesture.current.multi = true
  }
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const prev = ptrs.current.get(e.pointerId)
    if (!prev || !geo) return
    const now = { x: e.clientX, y: e.clientY }
    const g = gesture.current
    if (g && !g.moved && Math.hypot(now.x - g.start.x, now.y - g.start.y) > 6) g.moved = true
    const two = [...ptrs.current.keys()].slice(0, 2)
    if (two.length === 2 && two.includes(e.pointerId)) {
      const at = (id: number): Point => { const q = ptrs.current.get(id) ?? now; return toStage(q.x, q.y) }
      const from: [Point, Point] = [at(two[0]), at(two[1])]
      ptrs.current.set(e.pointerId, now)
      const to: [Point, Point] = [at(two[0]), at(two[1])]
      apply((v, gg) => pinchStep(v, from, to, gg.pic, gg.box, gg.fit))
      return
    }
    ptrs.current.set(e.pointerId, now)
    if (ptrs.current.size === 1 && zoomed) {
      const dx = now.x - prev.x
      const dy = now.y - prev.y
      apply((v, gg) => clampPan({ s: v.s, x: v.x + dx, y: v.y + dy }, gg.pic, gg.box))
    }
  }
  const onPointerEnd = (e: PointerEvent<HTMLDivElement>) => {
    if (!ptrs.current.delete(e.pointerId)) return
    const g = gesture.current
    if (ptrs.current.size > 0 || !g) return
    gesture.current = null
    if (e.type === 'pointercancel' || !g.touch || g.multi) return
    if (!zoomed) {
      const d = swipeDir(e.clientX - g.start.x, e.clientY - g.start.y)
      if (d) { go(d); return }
    }
    if (g.moved) return
    // двойное касание — как двойной щелчок: на сенсорных экранах dblclick приходит не всегда
    const p = { x: e.clientX, y: e.clientY }
    const t = lastTap.current
    if (t && e.timeStamp - t.t < TAP_MS && Math.hypot(p.x - t.p.x, p.y - t.p.y) < TAP_PX) {
      lastTap.current = null
      const q = toStage(p.x, p.y)
      apply((v, gg) => toggleZoom(v, q, gg.pic, gg.box, gg.fit))
    } else lastTap.current = { t: e.timeStamp, p }
  }
  const onDoubleClick = (e: MouseEvent<HTMLDivElement>) => {
    // касания — в onPointerEnd
    if (!geo || lastPointer.current !== 'mouse') return
    if (e.target instanceof Element && e.target.closest('button, a')) return
    const q = toStage(e.clientX, e.clientY)
    apply((v, gg) => toggleZoom(v, q, gg.pic, gg.box, gg.fit))
  }

  if (!item) return null
  const name = downloadName(fileBase, i, item)
  const caption = [meta?.platform ? platformName(meta.platform) : null, meta?.account, meta?.date ? fmtDT(meta.date) : null].filter(Boolean).join(' · ')
  const alt = `${title}: ${item.label}`
  const onLoaded = (w: number, h: number) => setLoad({ src, ok: true, w, h })
  const onFailed = () => setLoad({ src, ok: false, w: 0, h: 0 })

  return createPortal(
    <div ref={root} className="mv" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} onKeyDown={onKeyDown}>
      <div className="mv-bar">
        <div className="mv-info">
          <div className="mv-count">{n > 1 && <span className="num">{counterText(i, n)} · </span>}{item.label}</div>
          {caption && <div className="mv-cap">{caption}</div>}
        </div>
        {((isImage && status !== 'error') || ocr) && (
          <div className="mv-tools">
            {isImage && status !== 'error' && (
              <div className="mv-group" role="group" aria-label="Масштаб">
                <Btn label="Уменьшить" title="Уменьшить (−)" off={!geo || !zoomed} onClick={() => zoomBy(1 / ZOOM_STEP)}><IcMinus /></Btn>
                <span className="mv-pct num" title="Масштаб от исходного размера изображения">{geo ? percent(view.s) : '—'}</span>
                <Btn label="Увеличить" title="Увеличить (+)" off={!geo || view.s >= max * 0.999} onClick={() => zoomBy(ZOOM_STEP)}><IcPlus /></Btn>
                <Btn label="По размеру экрана" title="По размеру экрана (0)" off={!geo || !zoomed} onClick={toFit}>По размеру</Btn>
                <Btn label="Исходный размер, 100%" title="Исходный размер: пиксель изображения — пиксель экрана" off={!geo || Math.abs(view.s - 1) < 0.001} onClick={actual}>100%</Btn>
              </div>
            )}
            {ocr && (
              <button ref={ocrBtn} type="button" className={`mv-btn ${ocrOpen ? 'on' : ''}`} aria-pressed={ocrOpen} aria-controls={ocrOpen ? panelId : undefined}
                aria-label="Текст на изображении (OCR)" title={ocrOpen ? 'Скрыть текст на изображении (OCR)' : 'Показать текст на изображении (OCR) — сверить с картинкой'}
                onClick={() => setOcrOpen(!ocrOpen)}>
                <IcDoc /><span className="mv-lbl">Текст на изображении (OCR)</span><span className="mv-lbl-s">OCR</span>
              </button>
            )}
          </div>
        )}
        <div className="mv-acts">
          {opensInTab(src) && (
            <a className="mv-btn" href={src} target="_blank" rel="noopener noreferrer" aria-label="Открыть оригинал в новой вкладке" title="Открыть оригинал в новой вкладке">
              <IcExternal /><span className="mv-lbl">Открыть оригинал</span>
            </a>
          )}
          <a className="mv-btn" href={src} download={name} target="_blank" rel="noopener noreferrer" aria-label={`Скачать файл ${name}`} title={`Скачать файл ${name}`}>
            <IcDownload /><span className="mv-lbl">Скачать</span>
          </a>
        </div>
        <button type="button" className="mv-btn mv-x" aria-label="Закрыть просмотр" title="Закрыть (Esc)" onClick={onClose}><IcClose /></button>
      </div>

      <div className="mv-body">
        <div ref={stage} className={`mv-stage${isImage ? ` img${zoomed ? ' pan' : ''}` : ''}`}
          onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerEnd} onPointerCancel={onPointerEnd} onDoubleClick={onDoubleClick}>
          {isImage ? (
            <img key={src} className="mv-img" src={src} alt={alt} draggable={false}
              onLoad={(e) => onLoaded(e.currentTarget.naturalWidth || box.w || 1, e.currentTarget.naturalHeight || box.h || 1)} onError={onFailed}
              style={geo ? {
                width: geo.pic.w * view.s, height: geo.pic.h * view.s,
                transform: `translate3d(${(geo.box.w - geo.pic.w * view.s) / 2 + view.x}px, ${(geo.box.h - geo.pic.h * view.s) / 2 + view.y}px, 0)`,
              } : { visibility: 'hidden' }} />
          ) : (
            <video key={src} ref={video} className="mv-video" src={src} controls playsInline preload="metadata" tabIndex={0} aria-label={alt}
              onLoadedMetadata={() => onLoaded(0, 0)} onError={onFailed} style={status === 'error' ? { visibility: 'hidden' } : undefined} />
          )}
          {status === 'loading' && (
            <div className="mv-state" role="status"><span className="mv-spin" aria-hidden /><span className="mv-sr">Загружаем файл…</span></div>
          )}
          {status === 'error' && (
            <div className="mv-state mv-err" role="alert">
              <p>{viewerError(src)}</p>
              {!isLocalPreview(src) && opensInTab(src) && <a className="mv-btn" href={src} target="_blank" rel="noopener noreferrer"><IcExternal />Открыть оригинал</a>}
            </div>
          )}
          {n > 1 && (
            <>
              <button type="button" className="mv-nav prev" aria-label="Предыдущий файл" title="Предыдущий файл (←)" onClick={() => go(-1)}><IcChevronLeft size={24} /></button>
              <button type="button" className="mv-nav next" aria-label="Следующий файл" title="Следующий файл (→)" onClick={() => go(1)}><IcChevronRight size={24} /></button>
            </>
          )}
        </div>
        {ocr && ocrOpen && (
          <aside id={panelId} className="mv-ocr" aria-label="Текст на изображении (OCR)">
            <div className="mv-ocr-h">
              <b>Текст на изображении (OCR)</b>
              <button type="button" className="mv-ocr-x" aria-label="Скрыть текст OCR" title="Скрыть текст OCR" onClick={() => { ocrBtn.current?.focus(); setOcrOpen(false) }}><IcClose size={18} /></button>
            </div>
            <p className="mv-ocr-note">Распознан автоматически и может содержать ошибки — сверяйте с изображением</p>
            <div className="mv-ocr-t" tabIndex={0} role="region" aria-label="Распознанный текст"><Highlight text={ocr} /></div>
          </aside>
        )}
      </div>

      {n > 1 && (
        <div ref={strip} className="mv-thumbs" role="group" aria-label="Все файлы публикации">
          {items.map((x, k) => (
            <button key={x.src} type="button" className={`mv-th${k === i ? ' on' : ''}`} aria-current={k === i ? 'true' : undefined}
              aria-label={`Файл ${counterText(k, n)}: ${x.label}`} title={`${counterText(k, n)} · ${x.label}`} onClick={() => show(k)}>
              {x.kind === 'image'
                ? broken.includes(x.src) ? <span className="mv-th-v"><IcTriangleExclamation size={18} /></span>
                  : <img src={x.src} alt="" loading="lazy" draggable={false} onError={() => setBroken((b) => (b.includes(x.src) ? b : [...b, x.src]))} />
                : <span className="mv-th-v"><IcPlay size={18} /></span>}
            </button>
          ))}
        </div>
      )}
    </div>,
    document.body,
  )
}
