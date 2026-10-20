// Файлы публикаций: адреса для <img>/<video> и загрузка скриншота на сервер.
// Серверные файлы — /api/media/{id}: в разметке только через mediaSrc (учитывает VITE_API_URL). Пути сайта (/img/…, файлы
// офлайн-копии /snapshot-media/…) не меняются, blob: — локальный предпросмотр этой вкладки (на сервер его адрес не уходит).
import type { Ad, MediaRef, MediaUpload } from '../data/types'
import { ApiError, apiUpload, errorText, isRouteMissing, mediaSrc } from './api'

export { mediaSrc, mediaUrl } from './api'

/** Предел загрузки скриншота на сервере (POST /api/media) */
export const UPLOAD_MAX = 10 * 1024 * 1024

const MEDIA_PATH = /^\/api\/media\/([0-9a-f]{32})$/

/** Файл из хранилища сервера (/api/media/{id}) */
export const isServerMedia = (u?: string | null) => !!u && MEDIA_PATH.test(u)
/** Локальный предпросмотр этой вкладки (URL.createObjectURL) — после перезагрузки его нет */
export const isLocalPreview = (u?: string | null) => !!u && u.startsWith('blob:')

/** Адрес файла публикации из Ad.media для <img>/<video>: url сервера, а без него — /api/media/{id} */
export const mediaRefSrc = (m: Pick<MediaRef, 'id' | 'url'>) => mediaSrc(m.url || `/api/media/${m.id}`)

/** Картинка публикации для <img src>: обложка (image) через mediaSrc — серверные /api/media/… учитывают VITE_API_URL;
 *  без обложки — первое изображение из файлов публикации; иначе undefined (вместо картинки — значок площадки) */
export function adImage(a: Pick<Ad, 'image' | 'media'>): string | undefined {
  const cover = mediaSrc(a.image)
  if (cover) return cover
  const first = a.media?.find((m) => m.kind === 'image')
  return first ? mediaRefSrc(first) : undefined
}

/** Лента с сервера и предпросмотры этой вкладки (blob:): скриншот, не сохранённый на сервере, остаётся у своей публикации
 *  до перезагрузки — если сервер картинки для неё не прислал. Тот же расчёт — и для сравнения «лента не изменилась» */
type WithImage = { id: string; image?: string | null }
export function keepLocalPreviews<T extends WithImage>(next: T[], prev: readonly WithImage[]): T[] {
  const local = new Map(prev.flatMap((x) => (x.image && isLocalPreview(x.image) ? [[x.id, x.image] as const] : [])))
  return local.size ? next.map((x) => (!x.image && local.has(x.id) ? { ...x, image: local.get(x.id) } : x)) : next
}

export type UploadResult = ({ ok: true } & MediaUpload) | { ok: false; error: string }

/** Что не так с файлом до загрузки: не изображение или больше 10 МБ; иначе null (формат точно проверяет сервер) */
export function screenshotProblem(file: Pick<File, 'size' | 'type'>): string | null {
  if (file.type && !file.type.startsWith('image/')) return 'Нужно изображение: JPEG, PNG, WEBP или GIF'
  if (file.size > UPLOAD_MAX) return 'Файл больше 10 МБ'
  return null
}

/** Загрузить скриншот публикации на сервер ДО POST /api/ads: успех — {ok, id, url: "/api/media/{id}", …}, этот url идёт
 *  в image публикации. Ошибка не бросается — {ok: false, error}: 413/415/429/507 — текст сервера, нет связи — «Сервер недоступен»,
 *  сервер старой версии — «не принимает файлы». Форма тогда предупреждает и оставляет blob: только для предпросмотра */
export async function uploadScreenshot(file: File): Promise<UploadResult> {
  const problem = screenshotProblem(file)
  if (problem) return { ok: false, error: problem }
  const form = new FormData()
  form.append('file', file, file.name || 'screenshot')
  try {
    const got = await apiUpload<MediaUpload>('/media', form)
    const id = typeof got?.id === 'string' ? got.id.toLowerCase() : ''
    if (!/^[0-9a-f]{32}$/.test(id)) throw new ApiError(-1, 'Сервер вернул неожиданный ответ')
    // в публикацию — только путь хранилища этого сервера, что бы ни пришло в url
    const url = isServerMedia(got.url) ? got.url : `/api/media/${id}`
    return { ok: true, id, url, mime: got.mime ?? file.type, size: typeof got.size === 'number' ? got.size : file.size }
  } catch (e) {
    if (isRouteMissing(e)) return { ok: false, error: 'Сервер не принимает файлы публикаций — обновите бэкенд' }
    return { ok: false, error: errorText(e) }
  }
}

/** Предупреждение формы, если скриншот не сохранён на сервере */
export const uploadWarning = (error: string) => `Скриншот не сохранён на сервере: ${error} — публикация будет добавлена без него`
