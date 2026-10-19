import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, UNREACHABLE, apiGet, apiPost, messageOf, onReach } from './api'

afterEach(() => vi.unstubAllGlobals())

const reply = (status: number, body: unknown, type = 'application/json') =>
  vi.fn(async () => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers: { 'Content-Type': type } }))

async function failure(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError)
    return e as ApiError
  }
  throw new Error('запрос должен был завершиться ошибкой')
}

describe('messageOf', () => {
  it('detail строкой', () => {
    expect(messageOf({ detail: 'Публикация не найдена' }, 'x')).toBe('Публикация не найдена')
  })

  it('detail.message (409 сигнала)', () => {
    expect(messageOf({ detail: { message: 'По ЖК уже есть сигнал S-0003', signalId: 3 } }, 'x')).toBe('По ЖК уже есть сигнал S-0003')
  })

  it('список 422: перевод типовых ошибок, поле — последний элемент loc', () => {
    const detail = [
      { type: 'missing', loc: ['body', 'author'], msg: 'Field required' },
      { type: 'string_too_short', loc: ['body', 'text'], msg: 'String should have at least 1 character' },
      { type: 'string_too_long', loc: ['body', 'comment'], msg: 'String should have at most 2000 characters' },
      { type: 'literal_error', loc: ['body', 'status'], msg: "Input should be 'new', 'needs_check'" },
      { type: 'enum', loc: ['body', 'topics', 0], msg: 'Input should be ...' },
    ]
    expect(messageOf({ detail }, 'x')).toBe(
      'автор: обязательное поле; текст: не может быть пустым; комментарий: слишком длинное значение; статус: недопустимое значение; темы запроса: недопустимое значение')
  })

  it('value_error — текст сервера как есть, без префикса pydantic; ошибка всего тела — без поля', () => {
    expect(messageOf({ detail: [{ type: 'value_error', loc: ['body', 'comment'], msg: 'Value error, Для подтверждения нарушения нужно основание' }] }, 'x'))
      .toBe('комментарий: Для подтверждения нарушения нужно основание')
    expect(messageOf({ detail: [{ type: 'value_error', loc: ['body'], msg: 'Value error, Нужно основание' }] }, 'x')).toBe('Нужно основание')
  })

  it('незнакомое поле и тип — как есть', () => {
    expect(messageOf({ detail: [{ type: 'uuid_parsing', loc: ['query', 'limit'], msg: 'Input should be a valid UUID' }] }, 'x')).toBe('limit: Input should be a valid UUID')
  })

  it('числа и поля сверки сведений — по-русски', () => {
    expect(messageOf({ detail: [{ type: 'int_parsing', loc: ['body', 'applicants'], msg: 'Input should be a valid integer' }] }, 'x')).toBe('заявителей: нужно целое число')
    expect(messageOf({ detail: [{ type: 'float_parsing', loc: ['body', 'value'], msg: 'Input should be a valid number' }] }, 'x')).toBe('значение: нужно число')
    expect(messageOf({ detail: [{ type: 'value_error', loc: ['body', 'checkedAt'], msg: 'дата не может быть позже сегодняшней' }] }, 'x'))
      .toBe('дата сверки: дата не может быть позже сегодняшней')
  })

  it('нет detail — запасной текст', () => {
    expect(messageOf(null, 'Ошибка 418')).toBe('Ошибка 418')
    expect(messageOf('<html>', 'Ошибка 418')).toBe('Ошибка 418')
    expect(messageOf({ detail: [] }, 'Ошибка 422')).toBe('Ошибка 422')
  })
})

describe('apiFetch', () => {
  it('сеть недоступна — status 0, «Сервер недоступен»', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('NetworkError when attempting to fetch resource.') }))
    const e = await failure(apiGet('/health'))
    expect(e.status).toBe(0)
    expect(e.message).toBe(UNREACHABLE)
  })

  it('502 от прокси (API остановлен) — тоже status 0', async () => {
    vi.stubGlobal('fetch', reply(502, '', 'text/plain'))
    const e = await failure(apiGet('/ads'))
    expect(e.status).toBe(0)
    expect(e.message).toBe('Сервер недоступен')
  })

  it('422 — переведённый текст и исходный список в data', async () => {
    const detail = [{ type: 'missing', loc: ['body', 'author'], msg: 'Field required' }]
    vi.stubGlobal('fetch', reply(422, { detail }))
    const e = await failure(apiPost('/ads/a1/comments', { text: 'x' }))
    expect(e.status).toBe(422)
    expect(e.message).toBe('автор: обязательное поле')
    expect(e.data).toEqual(detail)
  })

  it('500 — detail сервера; без тела — общий текст', async () => {
    vi.stubGlobal('fetch', reply(500, { detail: 'Внутренняя ошибка сервера. Подробности — в журнале API.' }))
    expect((await failure(apiGet('/risk'))).message).toBe('Внутренняя ошибка сервера. Подробности — в журнале API.')
    vi.stubGlobal('fetch', reply(500, 'Internal Server Error', 'text/plain'))
    const e = await failure(apiGet('/risk'))
    expect([e.status, e.message]).toEqual([500, 'Внутренняя ошибка сервера'])
  })

  it('409 — detail-объект в data', async () => {
    vi.stubGlobal('fetch', reply(409, { detail: { message: 'Уже есть', signalId: 7 } }))
    const e = await failure(apiPost('/signals', {}))
    expect(e.data).toEqual({ message: 'Уже есть', signalId: 7 })
  })

  it('успех — разобранный JSON', async () => {
    vi.stubGlobal('fetch', reply(200, { ok: true }))
    await expect(apiGet('/health')).resolves.toEqual({ ok: true })
  })
})

describe('onReach — связь с сервером по итогу запроса (индикатор в шапке)', () => {
  const seen = async (fetcher: ReturnType<typeof vi.fn>) => {
    vi.stubGlobal('fetch', fetcher)
    const got: boolean[] = []
    const off = onReach((x) => got.push(x))
    try {
      await apiGet('/ads').catch(() => undefined)
    } finally {
      off()
    }
    return got
  }

  it('сеть недоступна и 502–504 прокси — нет связи', async () => {
    expect(await seen(vi.fn(async () => { throw new TypeError('NetworkError') }))).toEqual([false])
    for (const st of [502, 503, 504]) expect(await seen(reply(st, '', 'text/plain'))).toEqual([false])
  })

  it('сервер ответил — связь есть, даже если с ошибкой', async () => {
    expect(await seen(reply(200, []))).toEqual([true])
    expect(await seen(reply(422, { detail: [] }))).toEqual([true])
    expect(await seen(reply(500, { detail: 'Внутренняя ошибка сервера. Подробности — в журнале API.' }))).toEqual([true])
  })

  it('обрыв при чтении тела — нет связи и ApiError со status 0, а не сырой TypeError', async () => {
    const broken = { ok: true, status: 200, text: async () => { throw new TypeError('network error') } }
    vi.stubGlobal('fetch', vi.fn(async () => broken))
    const got: boolean[] = []
    const off = onReach((x) => got.push(x))
    const e = await failure(apiGet('/ads'))
    off()
    expect([e.status, e.message, got]).toEqual([0, UNREACHABLE, [false]])
  })

  it('после отписки не вызывается', async () => {
    vi.stubGlobal('fetch', reply(200, {}))
    const got: boolean[] = []
    onReach((x) => got.push(x))()
    await apiGet('/health')
    expect(got).toEqual([])
  })
})
