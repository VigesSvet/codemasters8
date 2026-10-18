// Просмотр файлов публикации (components/MediaViewer): состав галереи, имя файла для «Скачать», масштаб и сдвиг изображения.
// Чистые функции без DOM — их проверяет viewer.test.ts. Адреса файлов — только через adImage / mediaRefSrc (lib/media):
// опасные схемы туда не попадают, серверные /api/media/… учитывают VITE_API_URL.
import type { MediaRef } from '../data/types'
import { adImage, isLocalPreview, mediaRefSrc } from './media'

export type ViewerKind = 'image' | 'video'
/** cover — обложка, slide — слайд карусели, attachment — вложение, file — файл без роли */
export type ViewerRole = 'cover' | 'slide' | 'attachment' | 'file'

/** Файл в просмотре: src — безопасный адрес для <img>/<video>, label — подпись по-русски («обложка», «слайд 2», «вложение», «видео») */
export interface ViewerItem {
  src: string
  kind: ViewerKind
  role: ViewerRole
  label: string
  mime?: string
}

/** Что нужно галерее от публикации: Ad целиком или снимок публикации в сигнале (SignalAd — без файлов) */
export interface GallerySource {
  image?: string | null
  media?: MediaRef[] | null
}

const roleOf = (r?: string | null): ViewerRole => (r === 'cover' || r === 'slide' || r === 'attachment' ? r : 'file')

/** Галерея публикации: первой — обложка (та же картинка, что крупно в разборе, — adImage), за ней изображения и видео
 *  из файлов публикации по порядку. Повторы одного адреса (обложка — она же первый файл) и файлы без безопасного адреса
 *  пропускаются. Номер слайда — по порядку слайдов в публикации, как в карусели источника */
export function adGallery(a: GallerySource): ViewerItem[] {
  const media = a.media ?? []
  const slide = new Map<MediaRef, number>()
  for (const m of media) if (roleOf(m.role) === 'slide') slide.set(m, slide.size + 1)
  const labelOf = (m: MediaRef) => {
    const role = roleOf(m.role)
    const n = slide.get(m)
    if (m.kind === 'video') return n ? `слайд ${n}, видео` : 'видео'
    return role === 'cover' ? 'обложка' : n ? `слайд ${n}` : role === 'attachment' ? 'вложение' : 'изображение'
  }
  const out: ViewerItem[] = []
  const seen = new Set<string>()
  const add = (src: string | undefined, x: Omit<ViewerItem, 'src'>) => {
    if (!src || seen.has(src)) return
    seen.add(src)
    out.push({ src, ...x })
  }
  const cover = adImage({ image: a.image ?? undefined, media })
  if (cover) {
    // обложка — один из файлов публикации: подпись и тип файла — его; иначе это картинка публикации («обложка»)
    const ref = media.find((m) => mediaRefSrc(m) === cover)
    const role = ref ? roleOf(ref.role) : 'cover'
    add(cover, { kind: ref?.kind === 'video' ? 'video' : 'image', role: role === 'file' ? 'cover' : role,
      label: ref && role !== 'file' ? labelOf(ref) : 'обложка', mime: ref?.mime || undefined })
  }
  for (const m of media) {
    const kind: ViewerKind = m.kind === 'video' ? 'video' : 'image'
    add(mediaRefSrc(m), { kind, role: roleOf(m.role), label: labelOf(m), mime: m.mime || undefined })
  }
  return out
}

/** «2 из 5» */
export const counterText = (i: number, n: number) => `${i + 1} из ${n}`

/** Соседний файл по кругу: d = 1 — следующий, −1 — предыдущий; после последнего — первый */
export const stepIndex = (i: number, d: number, n: number) => (n > 0 ? (((i + d) % n) + n) % n : 0)

/** Текст ошибки загрузки: предпросмотр этой вкладки (blob:) после перезагрузки страницы уже не существует */
export const viewerError = (src: string) => (isLocalPreview(src)
  ? 'Предпросмотр этой вкладки — после перезагрузки страницы недоступен'
  : 'Файл недоступен — попробуйте открыть оригинал')

/** «Открыть оригинал» в новой вкладке: data:-адреса браузеры в вкладке не открывают */
export const opensInTab = (src: string) => !/^data:/i.test(src.trim())

// ---------- Имя файла для «Скачать» ----------

const MIME_EXT: Record<string, string> = {
  'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/pjpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif',
  'image/avif': 'avif', 'image/svg+xml': 'svg', 'image/bmp': 'bmp', 'image/heic': 'heic',
  'video/mp4': 'mp4', 'video/quicktime': 'mov', 'video/webm': 'webm', 'video/x-m4v': 'm4v', 'video/ogg': 'ogv',
}
const KNOWN_EXT = new Set([...Object.values(MIME_EXT), 'jpeg'])

/** Расширение файла: по типу (mime), иначе по адресу (data:image/png…, /img/site.jpg) — только известные; иначе jpg / mp4 */
export function fileExt(x: Pick<ViewerItem, 'src' | 'kind' | 'mime'>): string {
  const byMime = MIME_EXT[(x.mime ?? '').split(';')[0].trim().toLowerCase()]
  if (byMime) return byMime
  const src = x.src.trim()
  const data = /^data:([^;,]+)/i.exec(src)
  if (data) return MIME_EXT[data[1].toLowerCase()] ?? (x.kind === 'video' ? 'mp4' : 'jpg')
  if (!isLocalPreview(src)) {
    const path = src.split(/[?#]/)[0]
    const ext = /\.([a-z0-9]{2,5})$/i.exec(path.slice(path.lastIndexOf('/') + 1))?.[1].toLowerCase()
    if (ext && KNOWN_EXT.has(ext)) return ext === 'jpeg' ? 'jpg' : ext
  }
  return x.kind === 'video' ? 'mp4' : 'jpg'
}

/** Имя скачиваемого файла: номер публикации, номер файла в галерее и расширение — AD-007-1.jpg */
export function downloadName(key: string, index: number, x: Pick<ViewerItem, 'src' | 'kind' | 'mime'>): string {
  const base = key.trim().replace(/[\\/:*?"<>|\s]+/g, '-') || 'файл'
  return `${base}-${index + 1}.${fileExt(x)}`
}

// ---------- Масштаб и сдвиг изображения ----------

export type Size = { w: number; h: number }
export type Point = { x: number; y: number }
/** s — масштаб к исходному размеру (1 — пиксель в пиксель); x, y — сдвиг центра изображения от центра окна просмотра, px */
export type View = { s: number; x: number; y: number }

/** Наибольший масштаб — 8× исходного размера */
export const ZOOM_MAX = 8
/** Шаг кнопок «−» / «+» и клавиш */
export const ZOOM_STEP = 1.25
/** Двойной щелчок — вдвое крупнее, чем «по размеру экрана» */
export const ZOOM_DOUBLE = 2

const positive = (n: number) => Number.isFinite(n) && n > 0
/** -0 → 0: в подписях и сравнениях */
const z = (n: number) => n + 0

/** «По размеру экрана»: изображение целиком в окне (с полем pad), мелкое — не крупнее исходного размера */
export function fitScale(img: Size, box: Size, pad = 0): number {
  if (!positive(img.w) || !positive(img.h) || !positive(box.w) || !positive(box.h)) return 1
  const w = Math.max(1, box.w - 2 * pad)
  const h = Math.max(1, box.h - 2 * pad)
  return Math.min(1, w / img.w, h / img.h)
}

/** Пределы масштаба: от «по размеру экрана» до 8× */
export const zoomLimits = (fit: number) => ({ min: fit, max: Math.max(ZOOM_MAX, fit) })

export function clampScale(s: number, fit: number): number {
  const { min, max } = zoomLimits(fit)
  return Number.isFinite(s) ? Math.min(max, Math.max(min, s)) : min
}

/** Увеличено ли изображение относительно «по размеру экрана» (с допуском на округление) */
export const isZoomed = (s: number, fit: number) => s > fit * 1.001

/** Сдвиг в пределах: по оси, где изображение меньше окна, — по центру; где больше — край изображения не отходит
 *  от края окна (изображение нельзя утащить за экран) */
export function clampPan(v: View, img: Size, box: Size): View {
  const lim = (x: number, d: number, b: number) => {
    const m = Math.max(0, (d - b) / 2)
    return z(Math.min(m, Math.max(-m, Number.isFinite(x) ? x : 0)))
  }
  return { s: v.s, x: lim(v.x, img.w * v.s, box.w), y: lim(v.y, img.h * v.s, box.h) }
}

/** Масштаб без пределов сдвига: точка изображения под p (от центра окна) остаётся под p */
function scaleAround(v: View, s: number, p: Point): View {
  const k = s / v.s
  return { s, x: p.x - (p.x - v.x) * k, y: p.y - (p.y - v.y) * k }
}

/** Масштаб s вокруг точки p (px от центра окна — курсор, середина щипка; центр окна — {0, 0}) с пределами масштаба и сдвига */
export function zoomAt(v: View, s: number, p: Point, img: Size, box: Size, fit: number): View {
  return clampPan(scaleAround(v, clampScale(s, fit), p), img, box)
}

/** «По размеру» ↔ «вдвое крупнее» в точке p — двойной щелчок или двойное касание */
export function toggleZoom(v: View, p: Point, img: Size, box: Size, fit: number): View {
  return isZoomed(v.s, fit) ? { s: fit, x: 0, y: 0 } : zoomAt(v, fit * ZOOM_DOUBLE, p, img, box, fit)
}

const dist = ([a, b]: readonly [Point, Point]) => Math.hypot(a.x - b.x, a.y - b.y)
const mid = ([a, b]: readonly [Point, Point]): Point => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 })

/** Шаг щипка двумя пальцами (точки — от центра окна): масштаб — во сколько раз изменилось расстояние между пальцами,
 *  вокруг их середины; сдвиг середины двигает изображение */
export function pinchStep(v: View, from: readonly [Point, Point], to: readonly [Point, Point], img: Size, box: Size, fit: number): View {
  const d0 = dist(from)
  const d1 = dist(to)
  if (!positive(d0) || !positive(d1)) return v
  const m0 = mid(from)
  const m1 = mid(to)
  const z1 = scaleAround(v, clampScale(v.s * (d1 / d0), fit), m0)
  return clampPan({ s: z1.s, x: z1.x + m1.x - m0.x, y: z1.y + m1.y - m0.y }, img, box)
}

/** Во сколько раз изменить масштаб от колеса: вниз — мельче, вверх — крупнее. Строки и страницы (deltaMode 1, 2) — в пикселях;
 *  щипок на тачпаде приходит как колесо с Ctrl и мелким шагом — он чувствительнее. Один шаг ограничен */
export function wheelFactor(deltaY: number, deltaMode = 0, ctrl = false): number {
  if (!Number.isFinite(deltaY) || deltaY === 0) return 1
  const px = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * 400 : deltaY
  const step = Math.max(-120, Math.min(120, px))
  return Math.exp(-step * (ctrl ? 0.01 : 0.002))
}

/** Смахивание пальцем при масштабе «по размеру»: влево — следующий файл (1), вправо — предыдущий (−1), иначе 0 */
export function swipeDir(dx: number, dy: number, min = 50): -1 | 0 | 1 {
  if (Math.abs(dx) < min || Math.abs(dx) < Math.abs(dy) * 1.5) return 0
  return dx < 0 ? 1 : -1
}

/** Масштаб в процентах от исходного размера: «38%» */
export const percent = (s: number) => `${Math.round(s * 100)}%`

/** Подпись окна просмотра: площадка, аккаунт, дата публикации — что известно */
export type ViewerMeta = { platform?: string | null; account?: string | null; date?: string | null }
