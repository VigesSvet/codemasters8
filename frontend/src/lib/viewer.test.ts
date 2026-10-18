import { describe, expect, it } from 'vitest'
import type { MediaRef } from '../data/types'
import { adImage } from './media'
import {
  ZOOM_MAX, adGallery, clampPan, clampScale, counterText, downloadName, fileExt, fitScale, isZoomed, opensInTab, percent, pinchStep,
  stepIndex, swipeDir, toggleZoom, viewerError, wheelFactor, zoomAt, zoomLimits, type View,
} from './viewer'

const ID = (n: number) => String(n).padStart(32, '0')
const img = (n: number, role?: string | null, mime = 'image/jpeg'): MediaRef => ({ id: ID(n), kind: 'image', url: `/api/media/${ID(n)}`, role, mime })
const video = (n: number, role?: string | null): MediaRef => ({ id: ID(n), kind: 'video', url: `/api/media/${ID(n)}`, role, mime: 'video/mp4' })
const src = (n: number) => `/api/media/${ID(n)}`

describe('adGallery — файлы публикации для просмотра', () => {
  it('первой — обложка, затем изображения и видео по порядку; подписи по-русски', () => {
    const g = adGallery({ image: src(1), media: [img(1, 'cover'), img(2, 'slide'), img(3, 'slide'), video(4), img(5, 'attachment')] })
    expect(g.map((x) => [x.src, x.kind, x.role, x.label])).toEqual([
      [src(1), 'image', 'cover', 'обложка'],
      [src(2), 'image', 'slide', 'слайд 1'],
      [src(3), 'image', 'slide', 'слайд 2'],
      [src(4), 'video', 'file', 'видео'],
      [src(5), 'image', 'attachment', 'вложение'],
    ])
    expect(g[0].mime).toBe('image/jpeg')
    expect(g[3].mime).toBe('video/mp4')
  })

  it('обложка — первая картинка, как в разборе (adImage): и когда это слайд карусели', () => {
    const a = { image: src(2), media: [img(1, 'slide'), img(2, 'slide'), video(3, 'slide')] }
    const g = adGallery(a)
    expect(g[0].src).toBe(adImage(a))
    expect(g.map((x) => x.label)).toEqual(['слайд 2', 'слайд 1', 'слайд 3, видео'])
  })

  it('без обложки — первое изображение впереди, даже если видео раньше', () => {
    const a = { media: [video(1), img(2, null), img(3, null)] }
    const g = adGallery(a)
    expect(g[0].src).toBe(adImage(a))
    expect(g.map((x) => [x.src, x.role, x.label])).toEqual([
      [src(2), 'cover', 'обложка'], [src(1), 'file', 'видео'], [src(3), 'file', 'изображение'],
    ])
  })

  it('повторы адреса — один раз; файлы без безопасного адреса пропускаются', () => {
    const bad: MediaRef = { id: ID(9), kind: 'image', url: 'javascript:alert(1)' }
    const g = adGallery({ image: src(1), media: [img(1), bad, img(2), { ...img(2), id: ID(7) }] })
    expect(g.map((x) => x.src)).toEqual([src(1), src(2)])
    expect(adGallery({ image: 'javascript:alert(1)' })).toEqual([])
  })

  it('снимок публикации в сигнале и демо: одна картинка, null и пусто — без файлов', () => {
    expect(adGallery({ image: '/img/site-b.jpg' })).toEqual([{ src: '/img/site-b.jpg', kind: 'image', role: 'cover', label: 'обложка', mime: undefined }])
    expect(adGallery({ image: 'blob:http://localhost:5180/6f1c' })[0].src).toBe('blob:http://localhost:5180/6f1c')
    expect(adGallery({ image: null })).toEqual([])
    expect(adGallery({})).toEqual([])
    expect(adGallery({ media: [video(1)] }).map((x) => x.kind)).toEqual(['video'])
  })
})

describe('счётчик, листание по кругу, тексты', () => {
  it('«2 из 5»', () => {
    expect(counterText(1, 5)).toBe('2 из 5')
  })

  it('stepIndex: после последнего — первый, перед первым — последний', () => {
    expect([stepIndex(0, 1, 3), stepIndex(2, 1, 3), stepIndex(0, -1, 3), stepIndex(1, -1, 3)]).toEqual([1, 0, 2, 0])
    expect(stepIndex(0, 1, 1)).toBe(0)
    expect(stepIndex(0, 1, 0)).toBe(0)
  })

  it('ошибка загрузки: предпросмотр вкладки (blob:) и файл с сервера', () => {
    expect(viewerError('blob:http://localhost:5180/1')).toBe('Предпросмотр этой вкладки — после перезагрузки страницы недоступен')
    expect(viewerError(src(1))).toBe('Файл недоступен — попробуйте открыть оригинал')
  })

  it('«Открыть оригинал» — кроме data:-адресов', () => {
    expect([opensInTab(src(1)), opensInTab('blob:http://x/1'), opensInTab('data:image/png;base64,AA'), opensInTab('DATA:image/png,AA')]).toEqual([true, true, false, false])
  })

  it('масштаб в процентах', () => {
    expect([percent(1), percent(0.375), percent(2)]).toEqual(['100%', '38%', '200%'])
  })
})

describe('имя файла для «Скачать»', () => {
  it('номер публикации, номер файла и расширение по типу', () => {
    expect(downloadName('AD-007', 0, { src: src(1), kind: 'image', mime: 'image/jpeg' })).toBe('AD-007-1.jpg')
    expect(downloadName('AD-3F2A9C1B', 2, { src: src(1), kind: 'image', mime: 'image/png' })).toBe('AD-3F2A9C1B-3.png')
    expect(downloadName('AD-007', 1, { src: src(1), kind: 'video', mime: 'video/quicktime' })).toBe('AD-007-2.mov')
  })

  it('без типа — по адресу (только известные расширения), data: — по типу в адресе, иначе jpg / mp4', () => {
    expect(fileExt({ src: '/img/site-a-2026-08.jpg', kind: 'image' })).toBe('jpg')
    expect(fileExt({ src: 'https://cdn.example.com/x/photo.JPEG?w=1#a', kind: 'image' })).toBe('jpg')
    expect(fileExt({ src: 'https://cdn.example.com/clip.webm', kind: 'video' })).toBe('webm')
    expect(fileExt({ src: 'https://cdn.example.com/get.php', kind: 'image' })).toBe('jpg')
    expect(fileExt({ src: 'data:image/webp;base64,AAAA', kind: 'image' })).toBe('webp')
    expect(fileExt({ src: src(1), kind: 'video' })).toBe('mp4')
    expect(fileExt({ src: 'blob:http://localhost:5180/6f1c.png', kind: 'image' })).toBe('jpg')
    expect(fileExt({ src: 'blob:http://localhost:5180/6f1c', kind: 'image', mime: 'image/png' })).toBe('png')
    expect(fileExt({ src: src(1), kind: 'image', mime: 'image/svg+xml; charset=utf-8' })).toBe('svg')
  })

  it('недопустимые для имени файла символы заменяются', () => {
    expect(downloadName('скриншот', 0, { src: 'blob:x', kind: 'image', mime: 'image/png' })).toBe('скриншот-1.png')
    expect(downloadName('a/b: c', 0, { src: src(1), kind: 'image' })).toBe('a-b-c-1.jpg')
    expect(downloadName('  ', 0, { src: src(1), kind: 'image' })).toBe('файл-1.jpg')
  })
})

describe('масштаб: по размеру экрана, пределы', () => {
  const box = { w: 1000, h: 800 }

  it('крупное изображение — целиком в окне; мелкое — не крупнее 100%', () => {
    expect(fitScale({ w: 1080, h: 2400 }, box)).toBeCloseTo(800 / 2400)
    expect(fitScale({ w: 4000, h: 1000 }, box)).toBeCloseTo(0.25)
    expect(fitScale({ w: 200, h: 100 }, box)).toBe(1)
    // поле вокруг изображения
    expect(fitScale({ w: 1080, h: 2400 }, box, 16)).toBeCloseTo(768 / 2400)
  })

  it('размеров ещё нет — 1, без деления на ноль', () => {
    expect(fitScale({ w: 0, h: 0 }, box)).toBe(1)
    expect(fitScale({ w: 100, h: 100 }, { w: 0, h: 0 })).toBe(1)
    expect(fitScale({ w: 100, h: 100 }, { w: 10, h: 10 }, 20)).toBeCloseTo(0.01)
  })

  it('пределы: от «по размеру» до 8×', () => {
    expect(zoomLimits(0.25)).toEqual({ min: 0.25, max: ZOOM_MAX })
    expect([clampScale(0.1, 0.25), clampScale(3, 0.25), clampScale(20, 0.25), clampScale(Number.NaN, 0.25)]).toEqual([0.25, 3, 8, 0.25])
    expect([isZoomed(0.25, 0.25), isZoomed(0.2501, 0.25), isZoomed(0.3, 0.25)]).toEqual([false, false, true])
  })
})

describe('сдвиг и масштаб вокруг точки', () => {
  const box = { w: 1000, h: 800 }
  const pic = { w: 2000, h: 1000 }
  const fit = fitScale(pic, box) // 0.5: 1000 × 500

  it('по оси, где изображение меньше окна, — по центру; где больше — не дальше края', () => {
    expect(clampPan({ s: fit, x: 300, y: -200 }, pic, box)).toEqual({ s: fit, x: 0, y: 0 })
    // 2×: 4000 × 2000 в окне 1000 × 800 — сдвиг не больше (4000 − 1000) / 2 и (2000 − 800) / 2
    expect(clampPan({ s: 2, x: 5000, y: -5000 }, pic, box)).toEqual({ s: 2, x: 1500, y: -600 })
    expect(clampPan({ s: 2, x: 100, y: 50 }, pic, box)).toEqual({ s: 2, x: 100, y: 50 })
    expect(Object.is(clampPan({ s: fit, x: -5, y: -5 }, pic, box).x, 0)).toBe(true)
  })

  it('точка изображения под курсором остаётся под курсором', () => {
    const v: View = { s: 1, x: 120, y: -40 }
    const p = { x: 200, y: 100 }
    const under = (w: View) => ({ u: (p.x - w.x) / w.s, v: (p.y - w.y) / w.s })
    const after = zoomAt(v, 2, p, pic, box, fit)
    expect(after.s).toBe(2)
    expect(under(after).u).toBeCloseTo(under(v).u)
    expect(under(after).v).toBeCloseTo(under(v).v)
  })

  it('масштаб вокруг центра окна не сдвигает центр; пределы масштаба соблюдаются', () => {
    expect(zoomAt({ s: fit, x: 0, y: 0 }, 1, { x: 0, y: 0 }, pic, box, fit)).toEqual({ s: 1, x: 0, y: 0 })
    expect(zoomAt({ s: 1, x: 0, y: 0 }, 100, { x: 0, y: 0 }, pic, box, fit).s).toBe(ZOOM_MAX)
    // уменьшение ниже «по размеру» — ровно «по размеру», по центру
    expect(zoomAt({ s: 2, x: 900, y: 300 }, 0.01, { x: 0, y: 0 }, pic, box, fit)).toEqual({ s: fit, x: 0, y: 0 })
  })

  it('масштаб у края не выводит изображение за экран', () => {
    const v = zoomAt({ s: fit, x: 0, y: 0 }, 4, { x: 499, y: 399 }, pic, box, fit)
    expect(Math.abs(v.x)).toBeLessThanOrEqual((pic.w * v.s - box.w) / 2)
    expect(Math.abs(v.y)).toBeLessThanOrEqual((pic.h * v.s - box.h) / 2)
  })

  it('двойной щелчок: «по размеру» ↔ вдвое крупнее в точке щелчка', () => {
    const zoomed = toggleZoom({ s: fit, x: 0, y: 0 }, { x: 100, y: 0 }, pic, box, fit)
    expect(zoomed.s).toBe(fit * 2)
    expect(zoomed.x).toBeCloseTo(-100) // точка под курсором осталась на месте: 100 − (100 − 0) × 2
    expect(toggleZoom(zoomed, { x: 0, y: 0 }, pic, box, fit)).toEqual({ s: fit, x: 0, y: 0 })
  })
})

describe('щипок, колесо, смахивание', () => {
  const box = { w: 400, h: 800 }
  const pic = { w: 1080, h: 2400 }
  const fit = fitScale(pic, box)

  it('пальцы разошлись вдвое — вдвое крупнее; середина щипка двигает изображение', () => {
    const v: View = { s: 1, x: 0, y: 0 }
    const out = pinchStep(v, [{ x: -50, y: 0 }, { x: 50, y: 0 }], [{ x: -100, y: 0 }, { x: 100, y: 0 }], pic, box, fit)
    expect(out).toEqual({ s: 2, x: 0, y: 0 })
    const moved = pinchStep(v, [{ x: -50, y: 0 }, { x: 50, y: 0 }], [{ x: -30, y: 20 }, { x: 70, y: 20 }], pic, box, fit)
    expect(moved).toEqual({ s: 1, x: 20, y: 20 })
  })

  it('щипок не уменьшает ниже «по размеру» и не делит на ноль', () => {
    const v: View = { s: 1, x: 0, y: 0 }
    expect(pinchStep(v, [{ x: -100, y: 0 }, { x: 100, y: 0 }], [{ x: -1, y: 0 }, { x: 1, y: 0 }], pic, box, fit)).toEqual({ s: fit, x: 0, y: 0 })
    expect(pinchStep(v, [{ x: 0, y: 0 }, { x: 0, y: 0 }], [{ x: 1, y: 0 }, { x: 2, y: 0 }], pic, box, fit)).toBe(v)
  })

  it('колесо: вверх — крупнее, вниз — мельче; строки и страницы — в пикселях; шаг ограничен', () => {
    expect(wheelFactor(-100)).toBeGreaterThan(1)
    expect(wheelFactor(100)).toBeLessThan(1)
    expect(wheelFactor(100) * wheelFactor(-100)).toBeCloseTo(1)
    expect(wheelFactor(0)).toBe(1)
    expect(wheelFactor(3, 1)).toBeCloseTo(wheelFactor(48))
    expect(wheelFactor(1, 2)).toBe(wheelFactor(10_000))
    expect(wheelFactor(-10, 0, true)).toBeGreaterThan(wheelFactor(-10))
  })

  it('смахивание: влево — следующий, вправо — предыдущий; короткое и вертикальное — нет', () => {
    expect([swipeDir(-80, 10), swipeDir(80, -10), swipeDir(30, 0), swipeDir(-80, 70), swipeDir(0, 200)]).toEqual([1, -1, 0, 0, 0])
  })
})
