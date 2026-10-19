import { afterEach, describe, expect, it, vi } from 'vitest'
import { adImage, isLocalPreview, isServerMedia, keepLocalPreviews, mediaRefSrc, mediaSrc, mediaUrl, screenshotProblem, uploadScreenshot, uploadWarning } from './media'

afterEach(() => vi.unstubAllGlobals())

const ID = '0123456789abcdef0123456789abcdef'
const reply = (status: number, body: unknown, type = 'application/json') =>
  vi.fn(async (_url: string, _init?: RequestInit) => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers: { 'Content-Type': type } }))
const png = () => new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', { type: 'image/png' })

describe('uploadScreenshot — скриншот на сервер до POST /api/ads', () => {
  it('успех — адрес /api/media/{id}; файл уходит полем file в POST /api/media', async () => {
    const f = reply(201, { id: ID, url: `/api/media/${ID}`, mime: 'image/png', size: 4 })
    vi.stubGlobal('fetch', f)
    expect(await uploadScreenshot(png())).toEqual({ ok: true, id: ID, url: `/api/media/${ID}`, mime: 'image/png', size: 4 })
    const [url, init] = f.mock.calls[0]
    expect([url, init?.method]).toEqual(['/api/media', 'POST'])
    const form = init?.body as FormData
    expect(form).toBeInstanceOf(FormData)
    expect((form.get('file') as File).name).toBe('shot.png')
    // multipart: Content-Type с границей ставит сам браузер
    expect(init?.headers).toBeUndefined()
  })

  it('в публикацию — только путь хранилища этого сервера, что бы ни пришло в url', async () => {
    vi.stubGlobal('fetch', reply(201, { id: ID, url: 'https://evil.example/x.png', mime: 'image/png', size: 4 }))
    expect(await uploadScreenshot(png())).toMatchObject({ ok: true, url: `/api/media/${ID}` })
  })

  it('ошибка — текст сервера, без исключения', async () => {
    for (const [status, detail] of [
      [413, 'Файл больше 10 МБ'],
      [415, 'Неподдерживаемый тип файла — нужен JPEG, PNG, WEBP, GIF, MP4, MOV или WEBM'],
      [429, 'Слишком много загрузок — попробуйте позже'],
      [507, 'Мало места для файлов на сервере — файл не сохранён'],
    ] as const) {
      vi.stubGlobal('fetch', reply(status, { detail }))
      expect(await uploadScreenshot(png())).toEqual({ ok: false, error: detail })
    }
  })

  it('нет связи, сервер старой версии, неожиданный ответ — понятный текст', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('NetworkError') }))
    expect(await uploadScreenshot(png())).toEqual({ ok: false, error: 'Сервер недоступен' })
    vi.stubGlobal('fetch', reply(404, { detail: 'Not Found' }))
    expect(await uploadScreenshot(png())).toEqual({ ok: false, error: 'Сервер не принимает файлы публикаций — обновите бэкенд' })
    vi.stubGlobal('fetch', reply(405, { detail: 'Method Not Allowed' }))
    expect(await uploadScreenshot(png())).toMatchObject({ ok: false, error: 'Сервер не принимает файлы публикаций — обновите бэкенд' })
    vi.stubGlobal('fetch', reply(200, '<!doctype html>', 'text/html'))
    expect(await uploadScreenshot(png())).toEqual({ ok: false, error: 'Сервер вернул неожиданный ответ' })
  })

  it('больше 10 МБ и не изображение — отказ без запроса', async () => {
    const f = reply(201, {})
    vi.stubGlobal('fetch', f)
    const big = { name: 'big.png', size: 11 * 1024 * 1024, type: 'image/png' } as File
    expect(await uploadScreenshot(big)).toEqual({ ok: false, error: 'Файл больше 10 МБ' })
    expect(screenshotProblem({ size: 10, type: 'application/pdf' })).toBe('Нужно изображение: JPEG, PNG, WEBP или GIF')
    expect(screenshotProblem({ size: 10, type: '' })).toBeNull()
    expect(f).not.toHaveBeenCalled()
  })

  it('предупреждение формы', () => {
    expect(uploadWarning('Файл больше 10 МБ')).toBe('Скриншот не сохранён на сервере: Файл больше 10 МБ — публикация будет добавлена без него')
  })
})

describe('mediaSrc — адрес файла для <img>/<video>', () => {
  it('серверные /api/media/… — через базу API; пути сайта /img/… и http(s) — как есть', () => {
    expect(mediaSrc(`/api/media/${ID}`)).toBe(`/api/media/${ID}`)
    expect(mediaUrl(`/api/media/${ID}`, 'https://saq.example/api')).toBe(`https://saq.example/api/media/${ID}`)
    expect(mediaUrl(`/api/media/${ID}`, '')).toBe(`/media/${ID}`)
    expect(mediaSrc('/img/site-a-2026-08.jpg')).toBe('/img/site-a-2026-08.jpg')
    expect(mediaSrc('https://scontent.cdninstagram.com/x.jpg')).toBe('https://scontent.cdninstagram.com/x.jpg')
  })

  it('blob: (предпросмотр этой вкладки) и data:image — как есть; опасные схемы — нет', () => {
    expect(mediaSrc('blob:http://localhost:5180/6f1c')).toBe('blob:http://localhost:5180/6f1c')
    expect(mediaSrc('data:image/png;base64,iVBORw0KGgo=')).toBe('data:image/png;base64,iVBORw0KGgo=')
    expect(mediaSrc('data:text/html,<script>1</script>')).toBeUndefined()
    expect(mediaSrc('javascript:alert(1)')).toBeUndefined()
    expect(mediaSrc('JaVaScRiPt:alert(1)')).toBeUndefined()
    expect(mediaSrc(undefined)).toBeUndefined()
    expect(mediaSrc('   ')).toBeUndefined()
  })

  it('схему определяет разбор URL, как в браузере: управляющие символы и обход регулярного выражения — нет', () => {
    // браузер выбрасывает табуляцию и переводы строки внутри адреса, а управляющие символы в начале — до схемы
    for (const bad of ['java\tscript:alert(1)', 'jav\nascript:alert(1)', 'java\rscript:alert(1)', '\u0001javascript:alert(1)',
      'da\tta:text/html,<script>1</script>', ' javascript:alert(1)', 'vbscript:msgbox(1)', 'file:///etc/passwd', 'http://exa\u0000mple.com/x.png']) {
      expect(mediaSrc(bad), JSON.stringify(bad)).toBeUndefined()
    }
    // пути сайта (абсолютные и относительные) и адреса без схемы — как есть
    expect(mediaSrc('img/a.jpg')).toBe('img/a.jpg')
    expect(mediaSrc('//cdn.example.com/a.jpg')).toBe('//cdn.example.com/a.jpg')
    expect(mediaSrc('  https://cdn.example.com/a b.jpg ')).toBe('https://cdn.example.com/a b.jpg')
    expect(mediaSrc('DATA:image/png;base64,iVBORw0KGgo=')).toBe('DATA:image/png;base64,iVBORw0KGgo=')
    expect(mediaSrc('data:video/mp4;base64,AAAA')).toBe('data:video/mp4;base64,AAAA')
  })

  it('файлы публикации из Ad.media; признаки адресов', () => {
    expect(mediaRefSrc({ id: ID, url: `/api/media/${ID}` })).toBe(`/api/media/${ID}`)
    expect(mediaRefSrc({ id: ID, url: '' })).toBe(`/api/media/${ID}`)
    expect([isServerMedia(`/api/media/${ID}`), isServerMedia('/img/a.jpg'), isServerMedia(`/api/media/${ID}/../x`)]).toEqual([true, false, false])
    expect([isLocalPreview('blob:http://x/1'), isLocalPreview('/img/a.jpg'), isLocalPreview(null)]).toEqual([true, false, false])
  })
})

describe('adImage — картинка публикации для <img src>', () => {
  const ID2 = 'fedcba9876543210fedcba9876543210'
  const img = (id: string, role = 'slide') => ({ id, kind: 'image' as const, url: `/api/media/${id}`, role })
  const video = (id: string) => ({ id, kind: 'video' as const, url: `/api/media/${id}` })

  it('обложка — через mediaSrc: путь сайта /img/… как есть, серверная /api/media/… — от базы API', () => {
    expect(adImage({ image: '/img/site-a-2026-08.jpg' })).toBe('/img/site-a-2026-08.jpg')
    expect(adImage({ image: `/api/media/${ID}`, media: [img(ID2)] })).toBe(`/api/media/${ID}`)
  })

  it('без обложки — первое изображение из файлов публикации, видео пропускается', () => {
    expect(adImage({ media: [video(ID2), img(ID)] })).toBe(`/api/media/${ID}`)
    expect(adImage({ image: '', media: [img(ID), img(ID2)] })).toBe(`/api/media/${ID}`)
    // url пустой — адрес собирается по id
    expect(adImage({ media: [{ id: ID, kind: 'image', url: '' }] })).toBe(`/api/media/${ID}`)
  })

  it('опасная обложка не выводится: берётся файл публикации, а без него — значок площадки', () => {
    expect(adImage({ image: 'javascript:alert(1)', media: [img(ID)] })).toBe(`/api/media/${ID}`)
    expect(adImage({ image: 'javascript:alert(1)' })).toBeUndefined()
    expect(adImage({ media: [{ id: ID, kind: 'image', url: 'javascript:alert(1)' }] })).toBeUndefined()
  })

  it('ни обложки, ни изображений — undefined', () => {
    expect(adImage({})).toBeUndefined()
    expect(adImage({ image: undefined, media: [] })).toBeUndefined()
    expect(adImage({ media: [video(ID)] })).toBeUndefined()
  })
})

describe('keepLocalPreviews — скриншот этой вкладки, не сохранённый на сервере', () => {
  const blob = 'blob:http://localhost:5180/6f1c'
  const server = [{ id: 'a1', text: 'x' }, { id: 'a2', image: `/api/media/${ID}` }, { id: 'a3', image: null }]

  it('blob: остаётся у своей публикации, пока сервер не прислал картинку', () => {
    const tab = [{ id: 'a1', image: blob }, { id: 'a2', image: 'blob:http://localhost:5180/old' }]
    expect(keepLocalPreviews(server, tab)).toEqual([{ id: 'a1', text: 'x', image: blob }, { id: 'a2', image: `/api/media/${ID}` }, { id: 'a3', image: null }])
  })

  it('без локальных предпросмотров — та же лента (тот же массив)', () => {
    expect(keepLocalPreviews(server, [{ id: 'a1', image: '/img/a.jpg' }])).toBe(server)
  })

  it('опрос с локальным предпросмотром сходится: лента сервера без изменений — «не изменилась»', () => {
    const tab = keepLocalPreviews(server, [{ id: 'a1', image: blob }])
    // следующий опрос вернул то же — сравнение с учётом предпросмотра совпадает
    expect(JSON.stringify(keepLocalPreviews(server, tab))).toBe(JSON.stringify(tab))
    expect(JSON.stringify(server)).not.toBe(JSON.stringify(tab))
  })
})
